"""The legal register: laws, regulations and contractual requirements the
organisation has identified, whether each is in scope, who owns it, how it was
last assessed and when it is assessed next.

An item links to controls and clauses of a framework through the ISMS link
tables, and to documents, assets, risks and SWOT items through its own. A related record
is resolved with the reader's own access each time it is shown, so an item
never shows more of a record than the record's own page would.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, timedelta
from functools import partial
from typing import Annotated, Any, Literal
from uuid import UUID

from mosp.core.datetime_utils import iso_or_none
from mosp.core.datetime_utils import utc_now_naive as _utcnow
from mosp.security.csv import spreadsheet_cell
from mosp.security.urls import external_url as validate_external_url
from mosp.services.errors import DomainError
from mosp.services.mailer import user_email
from mosp.services.text import clean_text
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
    IsmsLegalAssessment,
    IsmsLegalCategory,
    IsmsLegalItem,
    IsmsLegalItemLink,
    User,
)
from app.services.control_refs import control_by_id_or_ref, control_summary
from app.services.frameworks import framework_parameter
from app.services.mailer import send_legal_assessment_reminder_email
from app.services.schedules import deliver_once, reminder_window, today_for_schedule

from .access import can_manage_isms, can_read_isms
from .common import _by_id_or_404, _clause_by_ref_or_id, _control_by_ref_or_id, _record
from .mutations import _apply_links, _delete_entity_links
from .related_records import replace_links, search_targets, target_summary
from .related_records import resolve_target as _target
from .schemas import IsmsLinksPayload
from .serializers import _attach_links, _user_summary

ENTITY = "legal_item"
# An assessment's outcome; an item shows the outcome of its newest assessment
# as its compliance status, and "not_assessed" until it has one.
OUTCOMES = ("compliant", "partially_compliant", "non_compliant")
NOT_ASSESSED = "not_assessed"
STATUSES = (NOT_ASSESSED, *OUTCOMES)
STATUS_LABELS = {
    NOT_ASSESSED: "Not assessed",
    "compliant": "Compliant",
    "partially_compliant": "Partially compliant",
    "non_compliant": "Non-compliant",
}
# Records an item relates to besides controls and clauses.
LINK_TYPES = ("document", "asset", "risk", "swot")
MAX_LINKS = 200
MAX_ASSESSMENTS = 500
# Controls a new item starts with when a save names none, by framework: the
# control that asks for legal, statutory, regulatory and contractual
# requirements to be identified.
DEFAULT_CONTROLS: dict[str, tuple[str, ...]] = {"iso_27001_2022": ("A.5.31",)}

Reference = Annotated[str, Field(min_length=1, max_length=128)]
Outcome = Literal["compliant", "partially_compliant", "non_compliant"]
LinkType = Literal["document", "asset", "risk", "swot"]


class DraftLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: LinkType
    target_id: UUID


class LegalItemPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=1, max_length=256)
    url: str | None = Field(None, max_length=2048)
    category_id: UUID | None = None
    # Names the category instead of category_id; a new name adds the category.
    category_name: str | None = Field(None, max_length=128)
    jurisdiction: str | None = Field(None, max_length=128)
    authority: str | None = Field(None, max_length=256)
    requirement: str | None = Field(None, max_length=20000)
    applicability: str | None = Field(None, max_length=20000)
    in_scope: bool | None = Field(None, strict=True)
    owner_user_id: UUID | None = None
    next_assessment_date: date | None = None
    controls: list[Reference] | None = Field(None, max_length=100)
    clauses: list[Reference] | None = Field(None, max_length=100)
    links: list[DraftLink] | None = Field(None, max_length=MAX_LINKS)
    expected_version: int | None = Field(None, ge=1)

    _validate_url = field_validator("url")(validate_external_url)


class LinkPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: LinkType
    target_id: UUID
    expected_version: int = Field(ge=1)


class AssessmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessed_on: date
    outcome: Outcome
    notes: str | None = Field(None, max_length=20000)
    # Who assessed it; the person saving the assessment when left out.
    assessed_by_user_id: UUID | None = None
    next_assessment_date: date | None = None
    expected_version: int = Field(ge=1)


class CategoryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(None, max_length=2000)


def require_access(db: Session, user: User | None, *, write: bool = False) -> None:
    if not (can_manage_isms(db, user) if write else can_read_isms(db, user)):
        raise DomainError(403, "ISMS access required")


def _version(row: IsmsLegalItem, expected: int | None) -> None:
    if expected is None or row.version != expected:
        raise DomainError(
            409, "This legal register item changed. Reload before saving."
        )


def _item(db: Session, item_id: Any, *, lock: bool = False) -> IsmsLegalItem:
    return _by_id_or_404(
        db, IsmsLegalItem, str(item_id), "Legal register item", lock=lock
    )


def _active_user(db: Session, user_id: UUID, label: str) -> None:
    if (
        not db.query(User.id)
        .filter(User.id == user_id, User.is_active.is_(True))
        .first()
    ):
        raise DomainError(400, f"Unknown active {label}")


def summary(row: IsmsLegalItem) -> dict[str, Any]:
    """An item as the register lists it, without its links and assessments."""
    category = row.category
    due = row.next_assessment_date
    return {
        "id": str(row.id),
        "title": row.title,
        "url": row.url,
        "category_id": str(row.category_id) if row.category_id else None,
        "category": {"id": str(category.id), "name": category.name}
        if category
        else None,
        "jurisdiction": row.jurisdiction,
        "authority": row.authority,
        "requirement": row.requirement,
        "applicability": row.applicability,
        "in_scope": row.in_scope,
        "owner_user_id": str(row.owner_user_id) if row.owner_user_id else None,
        "owner": _user_summary(row.owner),
        "compliance_status": row.compliance_status,
        "last_assessed_date": iso_or_none(row.last_assessed_date),
        "next_assessment_date": iso_or_none(due),
        # An item out of scope is not assessed, so it is never due.
        "assessment_due": bool(
            row.in_scope and due is not None and due <= today_for_schedule()
        ),
        "version": row.version,
        "created_at": iso_or_none(row.created_at),
        "updated_at": iso_or_none(row.updated_at),
        "display": row.title,
    }


def _assessment_out(row: IsmsLegalAssessment) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "assessed_on": row.assessed_on.isoformat(),
        "assessed_by": _user_summary(row.assessed_by),
        "outcome": row.outcome,
        "notes": row.notes,
        "created_at": iso_or_none(row.created_at),
    }


def _assessments(db: Session, item_id: UUID):
    return (
        db.query(IsmsLegalAssessment)
        .filter(IsmsLegalAssessment.item_id == item_id)
        .order_by(
            IsmsLegalAssessment.assessed_on.desc(),
            IsmsLegalAssessment.created_at.desc(),
            IsmsLegalAssessment.id.desc(),
        )
    )


def output(
    db: Session, row: IsmsLegalItem, framework: str, user: User
) -> dict[str, Any]:
    """An item in full: its framework links, related records the reader can
    open, and its assessments, newest first."""
    out = _attach_links(db, summary(row), ENTITY, row.id, framework, True)
    out["links"] = []
    links = (
        db.query(IsmsLegalItemLink)
        .filter_by(item_id=row.id)
        .order_by(IsmsLegalItemLink.target_type, IsmsLegalItemLink.target_id)
        .limit(MAX_LINKS)
    )
    for link in links:
        target = _target(db, user, link.target_type, link.target_id, required=False)
        if target:
            out["links"].append(target_summary(link.target_type, target))
    out["assessments"] = [
        _assessment_out(a) for a in _assessments(db, row.id).limit(MAX_ASSESSMENTS)
    ]
    return out


def _history(db: Session, row: IsmsLegalItem, framework: str) -> dict[str, Any]:
    """What the changelog keeps of an item: its fields and framework links.

    Related records are left out: their titles are read with the reader's own
    access, and a history every ISMS reader sees must not carry a risk's.
    """
    out = _attach_links(db, summary(row), ENTITY, row.id, framework, True)
    for key in ("assessment_due", "display", "owner_user_id", "category_id"):
        out.pop(key)
    out["category"] = row.category.name if row.category else None
    out["owner"] = row.owner.username if row.owner else None
    out["framework"] = framework
    return out


# --- Categories -------------------------------------------------------------------


def _category_out(row: IsmsLegalCategory, items: int) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "item_count": items,
    }


def _categories(db: Session) -> list[dict[str, Any]]:
    counts: dict[UUID | None, int] = {
        category_id: count
        for category_id, count in db.query(
            IsmsLegalItem.category_id, func.count(IsmsLegalItem.id)
        )
        .filter(IsmsLegalItem.category_id.isnot(None))
        .group_by(IsmsLegalItem.category_id)
    }
    rows = (
        db.query(IsmsLegalCategory)
        .order_by(func.lower(IsmsLegalCategory.name), IsmsLegalCategory.id)
        .all()
    )
    return [_category_out(row, counts.get(row.id, 0)) for row in rows]


def _category_by_name(
    db: Session, name: str, *, shared: bool = False
) -> IsmsLegalCategory | None:
    query = db.query(IsmsLegalCategory).filter(
        func.lower(IsmsLegalCategory.name) == name.lower()
    )
    return (_held(query) if shared else query).one_or_none()


def _held(query):
    """Read a category an item is about to name, holding it until the save
    commits: a deletion of the category waits, then finds the item."""
    return query.with_for_update(read=True, key_share=True)


def _category_for(
    db: Session, category_id: UUID | None, name: str | None
) -> IsmsLegalCategory | None:
    """The category a save names by id or by name, adding a new name."""
    if category_id:
        row = _held(db.query(IsmsLegalCategory).filter_by(id=category_id)).one_or_none()
        if not row:
            raise DomainError(400, "Unknown legal register category")
        return row
    cleaned = clean_text(name, max_len=128, single_line=True, label="category")
    if not cleaned:
        return None
    row = _category_by_name(db, cleaned, shared=True)
    if row is not None:
        return row
    row = IsmsLegalCategory(
        name=cleaned, description="", created_at=_utcnow(), updated_at=_utcnow()
    )
    try:
        with db.begin_nested():
            db.add(row)
    except IntegrityError:
        # Another save added the same name first; names are unique.
        return _category_by_name(db, cleaned, shared=True)
    return row


def save_category(
    *, payload: CategoryPayload, user: User, db: Session, category_id=None
):
    require_access(db, user, write=True)
    name = clean_text(
        payload.name, max_len=128, required=True, single_line=True, label="name"
    )
    row = (
        _by_id_or_404(db, IsmsLegalCategory, str(category_id), "Category", lock=True)
        if category_id
        else IsmsLegalCategory(description="", created_at=_utcnow())
    )
    taken = DomainError(409, "A category with this name already exists")
    existing = _category_by_name(db, name)
    if existing is not None and existing.id != row.id:
        raise taken
    row.name = name
    if "description" in payload.model_fields_set or not category_id:
        row.description = clean_text(payload.description, max_len=2000)
    row.updated_at = _utcnow()
    try:
        with db.begin_nested():
            db.add(row)
    except IntegrityError:
        # A save of the same name landed between the check and this one.
        raise taken from None
    db.commit()
    used = db.query(IsmsLegalItem.id).filter_by(category_id=row.id).count()
    return _category_out(row, used)


def delete_category(*, category_id, user: User, db: Session):
    require_access(db, user, write=True)
    row = _by_id_or_404(db, IsmsLegalCategory, str(category_id), "Category", lock=True)
    if db.query(IsmsLegalItem.id).filter_by(category_id=row.id).first():
        raise DomainError(
            409, "Move this category's items to another category before deleting it"
        )
    db.delete(row)
    db.commit()
    return {"ok": True}


# --- Reading ----------------------------------------------------------------------


def default_controls(db: Session, framework: str) -> list[dict[str, Any]]:
    """The controls a new item starts with in ``framework``, where it has them."""
    found = (
        control_by_id_or_ref(db, framework, ref, ignore_case=True)
        for ref in DEFAULT_CONTROLS.get(framework, ())
    )
    return [control_summary(control) for control in found if control]


def meta(*, framework: str, user: User, db: Session):
    require_access(db, user)
    fw = framework_parameter(framework)
    return {
        "framework": fw,
        "can_manage": can_manage_isms(db, user),
        "categories": _categories(db),
        "statuses": [{"code": s, "label": STATUS_LABELS[s]} for s in STATUSES],
        "outcomes": [{"code": s, "label": STATUS_LABELS[s]} for s in OUTCOMES],
        "link_types": list(LINK_TYPES),
        "default_controls": default_controls(db, fw),
    }


def _filtered(
    db: Session,
    user: User,
    *,
    q: str = "",
    category_id: UUID | None = None,
    in_scope: bool | None = None,
    status: str = "",
    owner_user_id: UUID | None = None,
    due: bool | None = None,
    target_type: str = "",
    target_id: UUID | None = None,
    control: str = "",
    clause: str = "",
    framework: str | None = None,
):
    qry = db.query(IsmsLegalItem)
    if q:
        needle = f"%{q[:256]}%"
        qry = qry.filter(
            or_(
                IsmsLegalItem.title.ilike(needle),
                IsmsLegalItem.requirement.ilike(needle),
                IsmsLegalItem.applicability.ilike(needle),
                IsmsLegalItem.jurisdiction.ilike(needle),
                IsmsLegalItem.authority.ilike(needle),
            )
        )
    if category_id:
        qry = qry.filter(IsmsLegalItem.category_id == category_id)
    if in_scope is not None:
        qry = qry.filter(IsmsLegalItem.in_scope.is_(in_scope))
    if status:
        if status not in STATUSES:
            raise DomainError(400, "Unknown compliance status")
        qry = qry.filter(IsmsLegalItem.compliance_status == status)
    if owner_user_id:
        qry = qry.filter(IsmsLegalItem.owner_user_id == owner_user_id)
    if due is not None:
        is_due = (
            IsmsLegalItem.in_scope.is_(True)
            & IsmsLegalItem.next_assessment_date.isnot(None)
            & (IsmsLegalItem.next_assessment_date <= today_for_schedule())
        )
        qry = qry.filter(is_due if due else ~is_due)
    if target_type or target_id:
        if target_type not in LINK_TYPES or not target_id:
            raise DomainError(400, "Choose a relationship type and item")
        _target(db, user, target_type, target_id)
        qry = qry.join(
            IsmsLegalItemLink, IsmsLegalItemLink.item_id == IsmsLegalItem.id
        ).filter(
            IsmsLegalItemLink.target_type == target_type,
            IsmsLegalItemLink.target_id == target_id,
        )
    if control or clause:
        fw = framework_parameter(framework)
        if control:
            found = _control_by_ref_or_id(db, control, fw)
            qry = qry.filter(
                IsmsLegalItem.id.in_(
                    db.query(IsmsEntityControlLink.entity_id).filter(
                        IsmsEntityControlLink.entity_type == ENTITY,
                        IsmsEntityControlLink.framework_slug == fw,
                        IsmsEntityControlLink.control_item_id == found.id,
                    )
                )
            )
        if clause:
            found_clause = _clause_by_ref_or_id(db, clause, fw)
            qry = qry.filter(
                IsmsLegalItem.id.in_(
                    db.query(IsmsEntityClauseLink.entity_id).filter(
                        IsmsEntityClauseLink.entity_type == ENTITY,
                        IsmsEntityClauseLink.framework_slug == fw,
                        IsmsEntityClauseLink.clause_id == found_clause.id,
                    )
                )
            )
    return qry


_ORDER = (func.lower(IsmsLegalItem.title), IsmsLegalItem.id)


def list_items(
    *, user: User, db: Session, limit: int = 100, offset: int = 0, **filters
):
    require_access(db, user)
    qry = _filtered(db, user, **filters)
    total = qry.count()
    limit, offset = min(max(limit, 1), 200), max(offset, 0)
    rows = qry.order_by(*_ORDER).offset(offset).limit(limit).all()
    return {
        "items": [summary(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_item(*, item_id, framework: str, user: User, db: Session):
    require_access(db, user)
    return output(db, _item(db, item_id), framework_parameter(framework), user)


def choices(*, target_type: str, q: str, user: User, db: Session):
    require_access(db, user)
    if target_type not in LINK_TYPES:
        raise DomainError(400, "Unknown relationship type")
    return {"items": search_targets(db, user, target_type, q)}


def _mapped(framework: str):
    """The condition that an item links to a control or clause of ``framework``."""
    return or_(
        *(
            IsmsLegalItem.id.in_(
                select(model.entity_id).where(
                    model.entity_type == ENTITY, model.framework_slug == framework
                )
            )
            for model in (IsmsEntityControlLink, IsmsEntityClauseLink)
        )
    )


def mapped_count(db: Session, framework: str) -> int:
    """How many items link to a control or clause of ``framework``, for the
    Statement of Applicability."""
    return (
        db.query(func.count(IsmsLegalItem.id)).filter(_mapped(framework)).scalar() or 0
    )


def owned_items(db: Session, user: User) -> list[dict[str, Any]]:
    """The items ``user`` owns, those with an assessment due first and then by
    title, for the owner's own account page."""
    rows = (
        db.query(IsmsLegalItem)
        .filter(IsmsLegalItem.owner_user_id == user.id)
        .order_by(
            IsmsLegalItem.next_assessment_date.asc().nullslast(),
            *_ORDER,
        )
        .limit(MAX_MAPPED)
        .all()
    )
    return [
        {
            **summary(row),
            "compliance_status_label": STATUS_LABELS[row.compliance_status],
        }
        for row in rows
    ]


MAX_MAPPED = 500


def mapped_items(db: Session, framework: str) -> list[dict[str, Any]]:
    """Up to ``MAX_MAPPED`` items linked to a control or clause of
    ``framework``, by title, each with those controls and clauses and the
    label of its compliance status, for the Statement of Applicability."""
    rows = (
        db.query(IsmsLegalItem)
        .filter(_mapped(framework))
        .order_by(*_ORDER)
        .limit(MAX_MAPPED)
        .all()
    )
    return [
        _attach_links(
            db,
            {
                **summary(row),
                "compliance_status_label": STATUS_LABELS[row.compliance_status],
            },
            ENTITY,
            row.id,
            framework,
            True,
        )
        for row in rows
    ]


CSV_COLUMNS = (
    "title",
    "url",
    "category",
    "jurisdiction",
    "authority",
    "requirement",
    "applicability",
    "in_scope",
    "owner",
    "compliance_status",
    "last_assessed_date",
    "next_assessment_date",
    "controls",
    "clauses",
)


def export_register(
    *, framework: str, user: User, db: Session, **filters
) -> Iterator[str]:
    """The register, or the part of it the filters keep, as CSV rows.

    Related documents, assets, risks and SWOT items are left out: the file is read by
    whoever it is passed to, without the access checks a page applies.
    """
    require_access(db, user)
    fw = framework_parameter(framework)
    qry = _filtered(db, user, framework=fw, **filters).order_by(*_ORDER)

    def generate():
        output_ = io.StringIO()
        writer = csv.DictWriter(output_, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in qry.yield_per(100):
            item = _attach_links(db, summary(row), ENTITY, row.id, fw, True)
            values = {
                **{key: item[key] for key in CSV_COLUMNS if key in item},
                "category": row.category.name if row.category else "",
                "in_scope": "yes" if row.in_scope else "no",
                "owner": row.owner.username if row.owner else "",
                "compliance_status": STATUS_LABELS[row.compliance_status],
                "controls": ";".join(c["ref"] for c in item["controls"]),
                "clauses": ";".join(c["ref"] for c in item["clauses"]),
            }
            writer.writerow(
                {
                    key: spreadsheet_cell("" if value is None else str(value))
                    for key, value in values.items()
                }
            )
            yield output_.getvalue()
            output_.seek(0)
            output_.truncate(0)
        yield output_.getvalue()

    return generate()


# --- Changes ----------------------------------------------------------------------

_TEXT_FIELDS = {
    "title": 256,
    "jurisdiction": 128,
    "authority": 256,
    "requirement": 20000,
    "applicability": 20000,
}


def save_item(*, payload: LegalItemPayload, framework, user, db, context, item_id=None):
    """Add an item, or change the fields a save names; the rest stay."""
    require_access(db, user, write=True)
    fw = framework_parameter(framework)
    fields = payload.model_fields_set
    # Every related record is checked before anything is written.
    draft_links = {(link.target_type, link.target_id) for link in payload.links or []}
    for kind, target_id in draft_links:
        _target(db, user, kind, target_id)
    if item_id:
        row = _item(db, item_id, lock=True)
        _version(row, payload.expected_version)
        before = _history(db, row, fw)
    else:
        row = IsmsLegalItem(
            title="",
            jurisdiction="",
            authority="",
            requirement="",
            applicability="",
            in_scope=True,
            compliance_status=NOT_ASSESSED,
            version=0,
            created_by_user_id=user.id,
            created_at=_utcnow(),
        )
        before = None
    for key, max_len in _TEXT_FIELDS.items():
        if key in fields:
            setattr(
                row,
                key,
                clean_text(
                    getattr(payload, key),
                    max_len=max_len,
                    required=key == "title",
                    single_line=max_len <= 256,
                    label=key,
                ),
            )
    if not row.title:
        raise DomainError(400, "title is required")
    if "url" in fields:
        row.url = payload.url
    if "in_scope" in fields:
        if payload.in_scope is None:
            raise DomainError(400, "in_scope cannot be empty")
        row.in_scope = payload.in_scope
    if "category_id" in fields or "category_name" in fields:
        category = _category_for(db, payload.category_id, payload.category_name)
        row.category = category
        row.category_id = category.id if category else None
    if "owner_user_id" in fields and payload.owner_user_id != row.owner_user_id:
        if payload.owner_user_id:
            _active_user(db, payload.owner_user_id, "owner")
        row.owner_user_id = payload.owner_user_id
        # A new owner is reminded of a date the previous owner was told of.
        row.reminded_at = None
    if "next_assessment_date" in fields:
        _set_next_assessment(row, payload.next_assessment_date)
    row.version += 1
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    db.refresh(row)
    controls = payload.controls
    if not item_id and controls is None:
        # A new item starts with the framework's control for legal requirements.
        controls = [control["id"] for control in default_controls(db, fw)]
    _apply_links(
        db,
        ENTITY,
        row.id,
        fw,
        IsmsLinksPayload(controls=controls, clauses=payload.clauses),
        user,
    )
    # A new item takes its relationships; a saved one has them replaced when
    # the save names them, and keeps them when it does not.
    changed = (
        replace_links(
            db,
            user,
            IsmsLegalItemLink,
            {"item_id": row.id},
            draft_links,
            limit=MAX_LINKS,
            noun="legal register item",
        )
        if "links" in fields or not item_id
        else set()
    )
    after = _history(db, row, fw)
    if changed:
        after["relationship_types"] = sorted({kind for kind, _ in changed})
    _record(
        db,
        ENTITY,
        row,
        "updated" if item_id else "created",
        before,
        after,
        user,
        context,
    )
    db.commit()
    return output(db, row, fw, user)


def _set_next_assessment(row: IsmsLegalItem, due: date | None) -> None:
    if due != row.next_assessment_date:
        row.next_assessment_date = due
        row.reminded_at = None


def delete_item(*, item_id, expected_version: int, framework, user, db, context):
    require_access(db, user, write=True)
    fw = framework_parameter(framework)
    row = _item(db, item_id, lock=True)
    _version(row, expected_version)
    _record(db, ENTITY, row, "deleted", _history(db, row, fw), None, user, context)
    _delete_entity_links(db, ENTITY, row.id, fw)
    db.query(IsmsLegalItemLink).filter_by(item_id=row.id).delete(
        synchronize_session=False
    )
    db.query(IsmsLegalAssessment).filter_by(item_id=row.id).delete(
        synchronize_session=False
    )
    db.delete(row)
    db.commit()
    return {"ok": True}


def change_link(*, item_id, payload: LinkPayload, remove: bool, user, db, context):
    require_access(db, user, write=True)
    row = _item(db, item_id, lock=True)
    _version(row, payload.expected_version)
    _target(db, user, payload.target_type, payload.target_id)
    qry = db.query(IsmsLegalItemLink).filter_by(
        item_id=row.id, target_type=payload.target_type, target_id=payload.target_id
    )
    if remove:
        qry.delete(synchronize_session=False)
    elif not qry.first():
        if db.query(IsmsLegalItemLink).filter_by(item_id=row.id).count() >= MAX_LINKS:
            raise DomainError(
                400, f"A legal register item supports up to {MAX_LINKS} relationships"
            )
        db.add(
            IsmsLegalItemLink(
                item_id=row.id,
                target_type=payload.target_type,
                target_id=payload.target_id,
            )
        )
    row.version += 1
    row.updated_at = _utcnow()
    # The history names the kind of record, never the record: its title is
    # read with the reader's own access. A removal names its target in the
    # request path, so each entry records the item's relationships as its path.
    _record(
        db,
        ENTITY,
        row,
        "relationship_removed" if remove else "relationship_added",
        None,
        {"relationship_type": payload.target_type, "version": row.version},
        user,
        replace(context, path=f"/v1/isms/legal-register/{row.id}/links"),
    )
    db.commit()
    return {"ok": True, "version": row.version}


# --- Assessments ------------------------------------------------------------------


def _apply_latest_assessment(db: Session, row: IsmsLegalItem) -> None:
    """Keep the item's status and last assessed date as its newest assessment."""
    latest = _assessments(db, row.id).first()
    row.compliance_status = latest.outcome if latest else NOT_ASSESSED
    row.last_assessed_date = latest.assessed_on if latest else None


def add_assessment(
    *, item_id, payload: AssessmentPayload, framework, user, db, context
):
    require_access(db, user, write=True)
    fw = framework_parameter(framework)
    row = _item(db, item_id, lock=True)
    _version(row, payload.expected_version)
    # A day's grace covers an assessor ahead of the server's date.
    if payload.assessed_on > today_for_schedule() + timedelta(days=1):
        raise DomainError(400, "An assessment cannot be dated in the future")
    due = payload.next_assessment_date
    if due is not None and due < payload.assessed_on:
        raise DomainError(
            400, "The next assessment date must be on or after the assessment date"
        )
    assessor = payload.assessed_by_user_id or user.id
    if assessor != user.id:
        _active_user(db, assessor, "assessor")
    if _assessments(db, row.id).count() >= MAX_ASSESSMENTS:
        raise DomainError(
            400, f"A legal register item keeps up to {MAX_ASSESSMENTS} assessments"
        )
    before = _history(db, row, fw)
    assessment = IsmsLegalAssessment(
        item_id=row.id,
        assessed_on=payload.assessed_on,
        assessed_by_user_id=assessor,
        outcome=payload.outcome,
        notes=clean_text(payload.notes, max_len=20000),
        created_by_user_id=user.id,
        created_at=_utcnow(),
    )
    db.add(assessment)
    db.flush()
    _apply_latest_assessment(db, row)
    if "next_assessment_date" in payload.model_fields_set:
        _set_next_assessment(row, due)
    row.version += 1
    row.updated_at = _utcnow()
    db.flush()
    after = _history(db, row, fw)
    after["assessment"] = {
        "assessed_on": assessment.assessed_on.isoformat(),
        "outcome": assessment.outcome,
    }
    _record(db, ENTITY, row, "assessed", before, after, user, context)
    db.commit()
    return output(db, row, fw, user)


def delete_assessment(
    *, item_id, assessment_id, expected_version: int, framework, user, db, context
):
    """Remove an assessment recorded in error; the item follows the newest left."""
    require_access(db, user, write=True)
    fw = framework_parameter(framework)
    row = _item(db, item_id, lock=True)
    _version(row, expected_version)
    assessment = (
        db.query(IsmsLegalAssessment)
        .filter_by(id=assessment_id, item_id=row.id)
        .one_or_none()
    )
    if assessment is None:
        raise DomainError(404, "Assessment not found")
    before = _history(db, row, fw)
    db.delete(assessment)
    db.flush()
    _apply_latest_assessment(db, row)
    row.version += 1
    row.updated_at = _utcnow()
    db.flush()
    _record(
        db,
        ENTITY,
        row,
        "assessment_removed",
        before,
        _history(db, row, fw),
        user,
        context,
    )
    db.commit()
    return output(db, row, fw, user)


# --- Reminders --------------------------------------------------------------------


def item_url(item_id: UUID) -> str:
    return f"{settings.public_base_url}/legal-register.html?id={item_id}"


def send_assessment_reminders(
    db: Session, *, today: date | None = None
) -> dict[str, Any]:
    """Remind each owner once that an in-scope item's assessment is due.

    An item is due a reminder from the configured number of days before its
    next assessment date, and stays due while that date has passed without one.
    """
    from app.services.features import state

    window = reminder_window(today)
    if window is None or not state(db, refresh=True).enabled("legal_register"):
        return {"enabled": False, "sent": 0}
    first, last = window
    ids = [
        item_id
        for (item_id,) in db.query(IsmsLegalItem.id)
        .filter(
            IsmsLegalItem.in_scope.is_(True),
            IsmsLegalItem.owner_user_id.isnot(None),
            IsmsLegalItem.next_assessment_date.isnot(None),
            IsmsLegalItem.next_assessment_date <= last,
            IsmsLegalItem.reminded_at.is_(None),
        )
        .order_by(IsmsLegalItem.next_assessment_date, IsmsLegalItem.id)
        .all()
    ]

    def compose(item: IsmsLegalItem):
        owner = item.owner
        email = user_email(owner) if owner and owner.is_active else None
        # The date may have been cleared since the items were listed.
        if not email or item.next_assessment_date is None:
            return False
        return partial(
            send_legal_assessment_reminder_email,
            to_email=email,
            owner_name=owner.username,
            title=item.title,
            due_on=item.next_assessment_date.isoformat(),
            overdue=item.next_assessment_date < first,
            item_url=item_url(item.id),
            uid=f"keen-legal-assessment-{item.id}",
        )

    return {
        "enabled": True,
        "sent": deliver_once(
            db, model=IsmsLegalItem, marker="reminded_at", ids=ids, compose=compose
        ),
        "window_start": first.isoformat(),
        "window_end": last.isoformat(),
    }
