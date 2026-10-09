"""ISMS mutations; see docs/maintainability-review.md for module boundaries."""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.utils import try_uuid as _try_uuid
from app.db.models import (
    IsmsAccessControlMatrixAwsAccount,
    IsmsAccessControlMatrixEntry,
    IsmsAccessControlMatrixRole,
    IsmsAwsAccount,
    IsmsDocument,
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
    IsmsLicense,
    IsmsMeeting,
    IsmsMeetingAttendee,
    IsmsMeetingLink,
    IsmsMeetingPerson,
    IsmsObjective,
    IsmsObjectiveResourceUser,
    IsmsOrgNode,
    IsmsOrgNodeUser,
    IsmsPerson,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    User,
)

from .common import (
    _clause_by_ref_or_id,
    _clean_text,
    _control_by_ref_or_id,
    _utcnow,
)
from .constants import (
    MEETING_LINK_TYPES,
)
from .schemas import (
    AssetPayload,
    IsmsLinksPayload,
    LicensePayload,
    MeetingLinkPayload,
)


def _asset_name_from_payload(payload: AssetPayload) -> str:
    return _clean_text(
        payload.asset or payload.asset_name or payload.name,
        max_len=256,
        required=True,
        label="asset",
    )


def _asset_category_by_id(db: Session, category_id: uuid.UUID) -> RiskCategory:
    row = db.query(RiskCategory).filter(RiskCategory.id == category_id).one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail="Unknown asset category")
    return row


def _asset_category_or_create(
    db: Session, *, category_id: uuid.UUID | None, category_name: str | None
) -> RiskCategory:
    if category_id:
        return _asset_category_by_id(db, category_id)
    name = _clean_text(category_name, max_len=128, required=True, label="category")
    row = (
        db.query(RiskCategory)
        .filter(func.lower(RiskCategory.name) == name.lower())
        .one_or_none()
    )
    if row:
        return row
    row = RiskCategory(name=name, created_at=_utcnow(), updated_at=_utcnow())
    db.add(row)
    db.flush()
    return row


def _asset_subcategory_by_id(
    db: Session, subcategory_id: uuid.UUID, *, category: RiskCategory | None = None
) -> RiskAssetSubcategory:
    qry = db.query(RiskAssetSubcategory).filter(
        RiskAssetSubcategory.id == subcategory_id
    )
    if category is not None:
        qry = qry.filter(RiskAssetSubcategory.category_id == category.id)
    row = qry.one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail="Unknown asset subcategory")
    return row


def _asset_subcategory_or_create(
    db: Session,
    *,
    category: RiskCategory,
    subcategory_id: uuid.UUID | None,
    subcategory_name: str | None,
) -> RiskAssetSubcategory:
    if subcategory_id:
        return _asset_subcategory_by_id(db, subcategory_id, category=category)
    name = _clean_text(
        subcategory_name, max_len=128, required=True, label="subcategory"
    )
    row = (
        db.query(RiskAssetSubcategory)
        .filter(
            RiskAssetSubcategory.category_id == category.id,
            func.lower(RiskAssetSubcategory.name) == name.lower(),
        )
        .one_or_none()
    )
    if row:
        return row
    row = RiskAssetSubcategory(
        category_id=category.id,
        name=name,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _license_or_404(db: Session, license_id: str | uuid.UUID) -> IsmsLicense:
    lid = (
        license_id if isinstance(license_id, uuid.UUID) else _try_uuid(str(license_id))
    )
    if not lid:
        raise HTTPException(status_code=400, detail="license_id must be a UUID")
    row = db.query(IsmsLicense).filter(IsmsLicense.id == lid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="License not found")
    return row


def _license_by_name(db: Session, name: str) -> IsmsLicense | None:
    cleaned = _clean_text(name, max_len=256)
    if not cleaned:
        return None
    return (
        db.query(IsmsLicense)
        .filter(func.lower(IsmsLicense.name) == cleaned.lower())
        .one_or_none()
    )


def _license_or_create_by_name(
    db: Session, name: str | None, user: User | None = None
) -> IsmsLicense | None:
    cleaned = _clean_text(name, max_len=256)
    if not cleaned:
        return None
    row = _license_by_name(db, cleaned)
    if row:
        return row
    row = IsmsLicense(
        name=cleaned,
        description="",
        created_by_user_id=getattr(user, "id", None),
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _license_from_payload(
    db: Session, payload: AssetPayload, user: User | None = None
) -> IsmsLicense | None:
    if payload.license_id:
        return _license_or_404(db, payload.license_id)
    # ``license_name`` is useful for imports/API callers; the UI sends license_id.
    if payload.license_name:
        return _license_or_create_by_name(db, payload.license_name, user)
    # Backwards compatibility for older callers still sending the old free-text field.
    if payload.license:
        return _license_or_create_by_name(db, payload.license, user)
    return None


def _apply_license_payload(row: IsmsLicense, payload: LicensePayload) -> None:
    row.name = _clean_text(payload.name, max_len=256, required=True, label="license")
    row.description = _clean_text(payload.description, max_len=12000)
    row.updated_at = _utcnow()


def _asset_or_404(db: Session, asset_id: str | uuid.UUID) -> RiskAsset:
    aid = asset_id if isinstance(asset_id, uuid.UUID) else _try_uuid(str(asset_id))
    if not aid:
        raise HTTPException(status_code=400, detail="asset_id must be a UUID")
    row = db.query(RiskAsset).filter(RiskAsset.id == aid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Asset not found")
    return row


def _replace_control_links(
    db: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    framework: str,
    values: list[str] | None,
    user: User,
) -> None:
    if values is None:
        return
    db.query(IsmsEntityControlLink).filter(
        IsmsEntityControlLink.entity_type == entity_type,
        IsmsEntityControlLink.entity_id == entity_id,
        IsmsEntityControlLink.framework_slug == framework,
    ).delete(synchronize_session=False)
    seen = set()
    now = _utcnow()
    for raw in values or []:
        control = _control_by_ref_or_id(db, str(raw), framework)
        if control.id in seen:
            continue
        seen.add(control.id)
        db.add(
            IsmsEntityControlLink(
                entity_type=entity_type,
                entity_id=entity_id,
                framework_slug=framework,
                control_item_id=control.id,
                created_by_user_id=user.id,
                created_at=now,
            )
        )
    db.flush()


def _replace_clause_links(
    db: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    framework: str,
    values: list[str] | None,
    user: User,
) -> None:
    if values is None:
        return
    db.query(IsmsEntityClauseLink).filter(
        IsmsEntityClauseLink.entity_type == entity_type,
        IsmsEntityClauseLink.entity_id == entity_id,
        IsmsEntityClauseLink.framework_slug == framework,
    ).delete(synchronize_session=False)
    seen = set()
    now = _utcnow()
    for raw in values or []:
        clause = _clause_by_ref_or_id(db, str(raw), framework)
        if clause.id in seen:
            continue
        seen.add(clause.id)
        db.add(
            IsmsEntityClauseLink(
                entity_type=entity_type,
                entity_id=entity_id,
                framework_slug=framework,
                clause_id=clause.id,
                created_by_user_id=user.id,
                created_at=now,
            )
        )
    db.flush()


def _apply_links(
    db: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    framework: str,
    payload: IsmsLinksPayload,
    user: User,
) -> None:
    _replace_control_links(
        db, entity_type, entity_id, framework, payload.controls, user
    )
    _replace_clause_links(db, entity_type, entity_id, framework, payload.clauses, user)


def _apply_effectiveness_links(
    db: Session,
    entity_id: uuid.UUID,
    framework: str,
    payload: IsmsLinksPayload,
    user: User,
) -> None:
    # Effectiveness Measures are control-scoped ISMS records. They deliberately
    # do not link directly to clauses, because the applicable clauses should be
    # inferred through the linked controls. Remove any legacy clause links if an
    # existing row is edited after older builds allowed them.
    _replace_control_links(
        db, "effectiveness_measure", entity_id, framework, payload.controls, user
    )
    db.query(IsmsEntityClauseLink).filter(
        IsmsEntityClauseLink.entity_type == "effectiveness_measure",
        IsmsEntityClauseLink.entity_id == entity_id,
        IsmsEntityClauseLink.framework_slug == framework,
    ).delete(synchronize_session=False)
    db.flush()


def _delete_entity_links(
    db: Session, entity_type: str, entity_id: uuid.UUID, framework: str
) -> None:
    db.query(IsmsEntityControlLink).filter(
        IsmsEntityControlLink.entity_type == entity_type,
        IsmsEntityControlLink.entity_id == entity_id,
        IsmsEntityControlLink.framework_slug == framework,
    ).delete(synchronize_session=False)
    db.query(IsmsEntityClauseLink).filter(
        IsmsEntityClauseLink.entity_type == entity_type,
        IsmsEntityClauseLink.entity_id == entity_id,
        IsmsEntityClauseLink.framework_slug == framework,
    ).delete(synchronize_session=False)


def _replace_objective_resources(
    db: Session, objective: IsmsObjective, user_ids: list[uuid.UUID] | None
) -> None:
    if user_ids is None:
        return
    db.query(IsmsObjectiveResourceUser).filter(
        IsmsObjectiveResourceUser.objective_id == objective.id
    ).delete(synchronize_session=False)
    seen = set()
    now = _utcnow()
    for uid in user_ids or []:
        if uid in seen:
            continue
        if (
            not db.query(User.id)
            .filter(User.id == uid, User.is_active.is_(True))
            .first()
        ):
            raise HTTPException(status_code=400, detail=f"Unknown user: {uid}")
        seen.add(uid)
        db.add(
            IsmsObjectiveResourceUser(
                objective_id=objective.id, user_id=uid, created_at=now
            )
        )
    db.flush()
    try:
        db.expire(objective, ["resource_users"])
    except Exception:
        pass


def _replace_access_control_accounts(
    db: Session,
    row: IsmsAccessControlMatrixEntry,
    aws_account_ids: list[uuid.UUID] | None,
) -> None:
    if aws_account_ids is None:
        return
    db.query(IsmsAccessControlMatrixAwsAccount).filter(
        IsmsAccessControlMatrixAwsAccount.entry_id == row.id
    ).delete(synchronize_session=False)
    seen = set()
    now = _utcnow()
    for account_id in aws_account_ids or []:
        if account_id in seen:
            continue
        if (
            not db.query(IsmsAwsAccount.id)
            .filter(IsmsAwsAccount.id == account_id)
            .first()
        ):
            raise HTTPException(
                status_code=400, detail=f"Unknown hosting account: {account_id}"
            )
        seen.add(account_id)
        db.add(
            IsmsAccessControlMatrixAwsAccount(
                entry_id=row.id,
                aws_account_id=account_id,
                created_at=now,
            )
        )
    db.flush()
    try:
        db.expire(row, ["aws_account_links"])
    except Exception:
        pass


def _replace_access_control_roles(
    db: Session,
    row: IsmsAccessControlMatrixEntry,
    role_org_node_ids: list[uuid.UUID] | None,
) -> None:
    if role_org_node_ids is None:
        return
    db.query(IsmsAccessControlMatrixRole).filter(
        IsmsAccessControlMatrixRole.entry_id == row.id
    ).delete(synchronize_session=False)
    seen = set()
    now = _utcnow()
    for org_node_id in role_org_node_ids or []:
        if org_node_id in seen:
            continue
        if not db.query(IsmsOrgNode.id).filter(IsmsOrgNode.id == org_node_id).first():
            raise HTTPException(
                status_code=400,
                detail=f"Unknown organisation chart role: {org_node_id}",
            )
        seen.add(org_node_id)
        db.add(
            IsmsAccessControlMatrixRole(
                entry_id=row.id,
                org_node_id=org_node_id,
                created_at=now,
            )
        )
    db.flush()
    try:
        db.expire(row, ["role_links"])
    except Exception:
        pass


def _replace_org_node_users(
    db: Session,
    node: IsmsOrgNode,
    user_ids: list[uuid.UUID] | None,
    relationship_type: str | None,
) -> None:
    if user_ids is None:
        return
    rel = _clean_text(
        relationship_type or "member",
        max_len=32,
        required=True,
        label="relationship_type",
    )
    db.query(IsmsOrgNodeUser).filter(IsmsOrgNodeUser.org_node_id == node.id).delete(
        synchronize_session=False
    )
    seen = set()
    now = _utcnow()
    for uid in user_ids or []:
        if uid in seen:
            continue
        if (
            not db.query(User.id)
            .filter(User.id == uid, User.is_active.is_(True))
            .first()
        ):
            raise HTTPException(status_code=400, detail=f"Unknown user: {uid}")
        seen.add(uid)
        db.add(
            IsmsOrgNodeUser(
                org_node_id=node.id, user_id=uid, relationship_type=rel, created_at=now
            )
        )
    db.flush()
    try:
        db.expire(node, ["users"])
    except Exception:
        pass


def _replace_meeting_people(
    db: Session,
    meeting: IsmsMeeting,
    attendee_ids: list[uuid.UUID] | None,
    apology_ids: list[uuid.UUID] | None,
) -> None:
    if attendee_ids is None and apology_ids is None:
        return
    db.query(IsmsMeetingAttendee).filter(
        IsmsMeetingAttendee.meeting_id == meeting.id
    ).delete(synchronize_session=False)
    now = _utcnow()
    seen = set()
    for kind, ids in (("attendee", attendee_ids or []), ("apology", apology_ids or [])):
        for uid in ids:
            key = (uid, kind)
            if key in seen:
                continue
            if (
                not db.query(User.id)
                .filter(User.id == uid, User.is_active.is_(True))
                .first()
            ):
                raise HTTPException(status_code=400, detail=f"Unknown user: {uid}")
            seen.add(key)
            db.add(
                IsmsMeetingAttendee(
                    meeting_id=meeting.id,
                    user_id=uid,
                    attendance_type=kind,
                    created_at=now,
                )
            )
    db.flush()
    try:
        db.expire(meeting, ["attendees"])
    except Exception:
        pass


def _replace_meeting_person_links(db: Session, meeting: IsmsMeeting,
                                  attendees: list[uuid.UUID] | None,
                                  apologies: list[uuid.UUID] | None) -> None:
    if attendees is None and apologies is None:
        return
    ids = set(attendees or []) | set(apologies or [])
    people = {p.id: p for p in db.query(IsmsPerson).filter(IsmsPerson.id.in_(ids)).all()} if ids else {}
    if len(people) != len(ids):
        raise HTTPException(400, "Unknown Person selected for meeting")
    db.query(IsmsMeetingPerson).filter(IsmsMeetingPerson.meeting_id == meeting.id).delete(synchronize_session=False)
    for kind, selected in (("attendee", attendees or []), ("apology", apologies or [])):
        for pid in dict.fromkeys(selected):
            person = people[pid]
            db.add(IsmsMeetingPerson(meeting_id=meeting.id, person_id=pid,
                                     attendance_type=kind, name=person.name,
                                     email=person.email or "", created_at=_utcnow()))
    db.flush()


def _replace_meeting_links(
    db: Session, meeting: IsmsMeeting, links: list[MeetingLinkPayload] | None
) -> None:
    if links is None:
        return
    db.query(IsmsMeetingLink).filter(IsmsMeetingLink.meeting_id == meeting.id).delete(
        synchronize_session=False
    )
    now = _utcnow()
    for payload in links or []:
        link_type = _clean_text(
            payload.link_type or "external_url",
            max_len=32,
            required=True,
            label="link_type",
        )
        if link_type not in MEETING_LINK_TYPES:
            raise HTTPException(status_code=400, detail="Invalid meeting link_type")
        document_id = payload.document_id
        url = (
            _clean_text(payload.url, max_len=2048, required=False, label="url") or None
        )
        title = _clean_text(payload.title, max_len=256, required=False, label="title")
        if link_type == "isms_document":
            if (
                not document_id
                or not db.query(IsmsDocument.id)
                .filter(IsmsDocument.id == document_id)
                .first()
            ):
                raise HTTPException(status_code=400, detail="Unknown ISMS document")
            url = None
        elif not url:
            raise HTTPException(
                status_code=400, detail="url is required for external links"
            )
        db.add(
            IsmsMeetingLink(
                meeting_id=meeting.id,
                link_type=link_type,
                document_id=document_id if link_type == "isms_document" else None,
                title=title,
                url=url,
                created_at=now,
            )
        )
    db.flush()
    try:
        db.expire(meeting, ["links"])
    except Exception:
        pass
