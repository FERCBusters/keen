"""SWOT analysis, bounded hierarchy and permission-aware relationships."""

from dataclasses import replace
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, text

from app.db.models import AuditSwotScope, IsmsSwot, IsmsSwotLink, User
from app.services.errors import DomainError
from app.services.frameworks import framework_parameter

from .access import _can_manage_isms, _can_read_isms
from .common import _by_id_or_404, _record, _utcnow
from .link_diary import remove_entity_link_diary
from .mutations import _apply_links
from .related_records import resolve_target as _target
from .related_records import search_targets, target_summary
from .serializers import _attach_links

KINDS = ("strength", "weakness", "opportunity", "threat")
# Records a SWOT item relates to; stories reach further through the same resolver.
LINK_TYPES = ("objective", "document", "asset", "person", "vendor", "risk")
STATUSES = (
    "defined",
    "planned",
    "in_progress",
    "under_review",
    "completed",
    "deferred",
    "superseded",
)
Reference = Annotated[str, Field(min_length=1, max_length=128)]


class DraftLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: Literal["objective", "document", "asset", "person", "vendor", "risk"]
    target_id: UUID


class SwotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=1, max_length=256)
    description: str | None = Field(None, max_length=20000)
    response: str | None = Field(None, max_length=20000)
    kind: Literal["strength", "weakness", "opportunity", "threat"] | None = None
    significance: int | None = Field(None, ge=1, le=5, strict=True)
    status: (
        Literal[
            "defined",
            "planned",
            "in_progress",
            "under_review",
            "completed",
            "deferred",
            "superseded",
        ]
        | None
    ) = None
    parent_id: UUID | None = None
    owner_user_id: UUID | None = None
    start_date: date | None = None
    target_date: date | None = None
    controls: list[Reference] | None = Field(None, max_length=100)
    clauses: list[Reference] | None = Field(None, max_length=100)
    links: list[DraftLink] | None = Field(None, max_length=200)
    expected_version: int | None = Field(None, ge=1)


class LinkPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: Literal["objective", "document", "asset", "person", "vendor", "risk"]
    target_id: UUID
    expected_version: int = Field(ge=1)


def require_access(db, user, *, write=False):
    if not (_can_manage_isms(db, user) if write else _can_read_isms(db, user)):
        raise DomainError(403, "ISMS access required")


def _lock(db):
    # Serialize structural edits, including reparenting and deletion. Row locks
    # alone cannot prevent two simultaneous edits from introducing a cycle.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(724103610)"))


def _version(row, expected):
    if expected is None or row.version != expected:
        raise DomainError(409, "This SWOT item changed. Reload before saving.")


def summary(row):
    return {
        key: (
            value.isoformat()
            if isinstance(value, date)
            else str(value)
            if isinstance(value, UUID)
            else value
        )
        for key in (
            "id",
            "title",
            "description",
            "response",
            "kind",
            "significance",
            "status",
            "parent_id",
            "owner_user_id",
            "start_date",
            "target_date",
            "version",
            "created_at",
            "updated_at",
        )
        for value in [getattr(row, key)]
    }


def output(db, row, framework, user):
    out = _attach_links(db, summary(row), "swot", row.id, framework, True)
    out["links"] = []
    for link in db.query(IsmsSwotLink).filter_by(swot_id=row.id).limit(200).all():
        target = _target(db, user, link.target_type, link.target_id, required=False)
        if target:
            out["links"].append(target_summary(link.target_type, target))
    return out


def get_item(*, item_id, framework, user, db):
    require_access(db, user)
    return output(
        db,
        _by_id_or_404(db, IsmsSwot, item_id, "SWOT"),
        framework_parameter(framework),
        user,
    )


def list_items(
    *,
    user,
    db,
    q="",
    kind="",
    status="",
    significance=None,
    parent_id=None,
    target_type="",
    target_id=None,
    limit=100,
    offset=0,
    control="",
    clause="",
    framework=None,
    **unused,
):
    require_access(db, user)
    qry = db.query(IsmsSwot)
    if kind:
        if kind not in KINDS:
            raise DomainError(400, "Unknown SWOT type")
        qry = qry.filter(IsmsSwot.kind == kind)
    if status:
        if status not in STATUSES:
            raise DomainError(400, "Unknown status")
        qry = qry.filter(IsmsSwot.status == status)
    if significance is not None:
        qry = qry.filter(IsmsSwot.significance == significance)
    if q:
        qry = qry.filter(
            or_(
                IsmsSwot.title.ilike(f"%{q[:256]}%"),
                IsmsSwot.description.ilike(f"%{q[:256]}%"),
            )
        )
    if parent_id:
        qry = qry.filter(IsmsSwot.parent_id == parent_id)
    if target_type or target_id:
        if not target_type or not target_id:
            raise DomainError(400, "Choose a relationship type and item")
        _target(db, user, target_type, target_id)
        qry = qry.join(IsmsSwotLink, IsmsSwotLink.swot_id == IsmsSwot.id).filter(
            IsmsSwotLink.target_type == target_type, IsmsSwotLink.target_id == target_id
        )
    if control or clause:
        from app.db.models import IsmsEntityClauseLink, IsmsEntityControlLink

        from .common import _clause_by_ref_or_id, _control_by_ref_or_id

        fw = framework_parameter(framework)
        for value, model, resolve, column in (
            (
                control,
                IsmsEntityControlLink,
                _control_by_ref_or_id,
                IsmsEntityControlLink.control_item_id,
            ),
            (
                clause,
                IsmsEntityClauseLink,
                _clause_by_ref_or_id,
                IsmsEntityClauseLink.clause_id,
            ),
        ):
            if value:
                target = resolve(db, value, fw)
                qry = qry.filter(
                    IsmsSwot.id.in_(
                        db.query(model.entity_id).filter(
                            model.entity_type == "swot",
                            model.framework_slug == fw,
                            column == target.id,
                        )
                    )
                )
    total = qry.count()
    limit, offset = min(max(limit, 1), 200), max(offset, 0)
    rows = (
        qry.order_by(IsmsSwot.significance.desc(), IsmsSwot.title, IsmsSwot.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "items": [summary(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def save_item(*, payload, framework, user, db, context, item_id=None):
    require_access(db, user, write=True)
    fw = framework_parameter(framework)
    if item_id and "links" in payload.model_fields_set:
        raise DomainError(400, "Use relationship actions for an existing SWOT item")
    # Validate every draft target before adding the item or any mappings/history.
    # The session dependency rolls back the complete transaction on any failure.
    draft_links = {(link.target_type, link.target_id) for link in payload.links or []}
    for kind, target_id in draft_links:
        _target(db, user, kind, target_id)
    _lock(db)
    row = (
        _by_id_or_404(db, IsmsSwot, item_id, "SWOT")
        if item_id
        else IsmsSwot(
            title="",
            kind="strength",
            significance=3,
            status="defined",
            description="",
            response="",
            version=1,
            created_by_user_id=user.id,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
    )
    before = (
        _attach_links(db, summary(row), "swot", row.id, fw, True) if item_id else None
    )
    if item_id:
        _version(row, payload.expected_version)
    for key in payload.model_fields_set - {
        "controls",
        "clauses",
        "expected_version",
        "links",
    }:
        value = getattr(payload, key)
        if (
            key
            in {"title", "kind", "significance", "status", "description", "response"}
            and value is None
        ):
            raise DomainError(400, f"{key} cannot be empty")
        setattr(row, key, value.strip() if isinstance(value, str) else value)
    with db.no_autoflush:
        if not row.title:
            raise DomainError(400, "Title is required")
        if (
            row.owner_user_id
            and not db.query(User.id)
            .filter(User.id == row.owner_user_id, User.is_active.is_(True))
            .first()
        ):
            raise DomainError(400, "Unknown active owner")
        if row.start_date and row.target_date and row.start_date > row.target_date:
            raise DomainError(400, "Target date must be on or after start date")
        if (
            item_id
            and db.query(IsmsSwot.id)
            .filter(IsmsSwot.parent_id == row.id, IsmsSwot.kind != row.kind)
            .first()
        ):
            raise DomainError(400, "Move children before changing quadrant")
        parent, seen = row.parent_id, {row.id}
        # Depth is bounded, and a move checks descendant height as well.
        depth = 0
        while parent:
            if parent in seen or depth >= 11:
                raise DomainError(
                    400, "Hierarchy must be acyclic and at most 12 levels deep"
                )
            seen.add(parent)
            ancestor = db.get(IsmsSwot, parent)
            if not ancestor or ancestor.kind != row.kind:
                raise DomainError(400, "Parent must be in the same SWOT quadrant")
            parent, depth = ancestor.parent_id, depth + 1
        frontier = [row.id] if row.id else []
        while frontier:
            children = [
                r[0]
                for r in db.query(IsmsSwot.id)
                .filter(IsmsSwot.parent_id.in_(frontier))
                .limit(2001)
                .all()
            ]
            if not children:
                break
            depth += 1
            if depth >= 12 or len(children) > 2000:
                raise DomainError(
                    400, "Hierarchy exceeds the supported depth or branch size"
                )
            frontier = children
    row.updated_at = _utcnow()
    if item_id:
        row.version += 1
    db.add(row)
    db.flush()
    _apply_links(db, "swot", row.id, fw, payload, user)
    for kind, target_id in sorted(
        draft_links, key=lambda pair: (pair[0], str(pair[1]))
    ):
        db.add(IsmsSwotLink(swot_id=row.id, target_type=kind, target_id=target_id))
    # Record framework mapping changes without copying restricted targets.
    after = _attach_links(db, summary(row), "swot", row.id, fw, True)
    after["framework"] = fw
    if draft_links:
        after["relationship_types"] = sorted({kind for kind, _ in draft_links})
    _record(
        db,
        "swot",
        row,
        "updated" if item_id else "created",
        before,
        after,
        user,
        context,
    )
    db.commit()
    return output(db, row, fw, user)


def delete_item(*, item_id, expected_version, user, db, context):
    require_access(db, user, write=True)
    _lock(db)
    row = _by_id_or_404(db, IsmsSwot, item_id, "SWOT")
    _version(row, expected_version)
    if (
        db.query(IsmsSwot.id).filter_by(parent_id=row.id).first()
        or db.query(AuditSwotScope.audit_id).filter_by(swot_id=row.id).first()
    ):
        raise DomainError(
            409, "Move child items and remove audit scopes before deleting"
        )
    from app.db.models import IsmsEntityClauseLink, IsmsEntityControlLink

    for model in (IsmsEntityControlLink, IsmsEntityClauseLink):
        db.query(model).filter_by(entity_type="swot", entity_id=row.id).delete(
            synchronize_session=False
        )
    db.query(IsmsSwotLink).filter_by(swot_id=row.id).delete(synchronize_session=False)
    # Withdraw the automatic diary entry that recorded the deleted item's links.
    remove_entity_link_diary(db, "swot", row.id)
    _record(db, "swot", row, "deleted", summary(row), None, user, context)
    db.delete(row)
    db.commit()
    return {"ok": True}


def change_link(*, item_id, payload, remove, user, db, context):
    require_access(db, user, write=True)
    _lock(db)
    row = _by_id_or_404(db, IsmsSwot, item_id, "SWOT")
    _version(row, payload.expected_version)
    _target(db, user, payload.target_type, payload.target_id)
    qry = db.query(IsmsSwotLink).filter_by(
        swot_id=row.id, target_type=payload.target_type, target_id=payload.target_id
    )
    existing = qry.first()
    if remove:
        qry.delete(synchronize_session=False)
    elif not existing:
        if db.query(IsmsSwotLink).filter_by(swot_id=row.id).count() >= 200:
            raise DomainError(400, "A SWOT item supports up to 200 relationships")
        db.add(
            IsmsSwotLink(
                swot_id=row.id,
                target_type=payload.target_type,
                target_id=payload.target_id,
            )
        )
    row.version += 1
    row.updated_at = _utcnow()
    # Target identifiers and titles are resolved at read time, never copied into
    # an ISMS-readable history that could bypass risk permissions later. A
    # removal names its target in the request path, so every relationship
    # entry records the item's relationship collection as its path.
    if context is not None:
        context = replace(context, path=f"/v1/isms/swot/{row.id}/links")
    _record(
        db,
        "swot",
        row,
        "relationship_removed" if remove else "relationship_added",
        None,
        {"relationship_type": payload.target_type, "version": row.version},
        user,
        context,
    )
    db.commit()
    return {"ok": True, "version": row.version}


def choices(*, target_type, q, user, db):
    require_access(db, user)
    if target_type not in LINK_TYPES:
        raise DomainError(400, "Unknown relationship type")
    return {"items": search_targets(db, user, target_type, q)}
