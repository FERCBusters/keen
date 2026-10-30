"""Risks recorded against posture results, and the acceptances they carry.

A failing posture result can be recorded as a risk: a workstation check, a
cloud check, or one identity within a cloud check. While a linked risk's
treatment is Accept, it is not closed and its treatment due date has not
passed, the result reads as Risk accepted. The evidence itself never changes;
acceptance is KEEN's judgement beside it, and it lapses on its own.
"""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    ControlItem,
    Mapping,
    PostureRiskLink,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    User,
)
from app.security.permissions import has_permission
from app.services.errors import DomainError
from app.services.risks import (
    RISK_MANAGE_PERMISSION,
    RISK_READ_PERMISSION,
    TREATMENT_STRATEGIES,
    RiskUpsertPayload,
    insert_risk,
)

ORIGINS = ("workstation", "cloud")
# Links shown for one page of posture results; a page never needs more.
MAX_LINKS = 2000
MAX_CONTROLS = 200


class PostureRiskInput(BaseModel):
    """What an administrator states when recording a posture risk."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=256)
    threat_summary: str = Field(default="", max_length=12000)
    treatment_strategy: str = Field(default="", max_length=32)
    treatment_due_at: date | None = None
    risk_owner_user_id: uuid.UUID | None = None
    framework: str | None = Field(default=None, max_length=64)


def can_record(db: Session, user) -> bool:
    return isinstance(user, User) and has_permission(db, user, RISK_MANAGE_PERMISSION)


def can_read(db: Session, user) -> bool:
    return isinstance(user, User) and (
        has_permission(db, user, RISK_MANAGE_PERMISSION)
        or has_permission(db, user, RISK_READ_PERMISSION)
    )


def is_accepted(risk: Risk, today: date) -> bool:
    return (
        risk.treatment_strategy == "accept"
        and risk.treatment_status != "closed"
        and risk.treatment_due_at is not None
        and risk.treatment_due_at >= today
    )


def linked(db: Session, origin: str, origin_keys, *, titles: bool) -> dict:
    """Risks linked to results, keyed by (origin key, scope, check, subject)."""
    keys = sorted({str(k) for k in origin_keys})
    if origin not in ORIGINS or not keys:
        return {}
    rows = (
        db.query(PostureRiskLink, Risk)
        .join(Risk, Risk.id == PostureRiskLink.risk_id)
        .filter(PostureRiskLink.origin == origin, PostureRiskLink.origin_key.in_(keys))
        .order_by(Risk.created_at, Risk.id)
        .limit(MAX_LINKS)
        .all()
    )
    today = utc_now_naive().date()
    out: dict = {}
    for link, risk in rows:
        accepted = is_accepted(risk, today)
        key = (link.origin_key, link.scope_key, link.check_key, link.subject_key)
        out.setdefault(key, []).append(
            {
                "id": str(risk.id),
                # Titles are risk content; the result's state is not.
                "title": risk.title if titles else None,
                "accepted": accepted,
                "acceptance_expired": risk.treatment_strategy == "accept"
                and not accepted,
                "due": risk.treatment_due_at.isoformat()
                if risk.treatment_due_at
                else None,
            }
        )
    return out


def mapped_controls(db: Session, event_ids, framework: str) -> list[str]:
    """Control references the given evidence is mapped to in one framework."""
    ids = list(event_ids)
    if not ids:
        return []
    rows = (
        db.query(ControlItem.ref)
        .join(Mapping, Mapping.control_item_id == ControlItem.id)
        .filter(Mapping.event_id.in_(ids), ControlItem.framework_slug == framework)
        .distinct()
        .order_by(ControlItem.ref)
        .limit(MAX_CONTROLS)
        .all()
    )
    return [ref for (ref,) in rows]


def category_asset(
    db: Session,
    *,
    asset_id: uuid.UUID,
    name: str,
    category: str,
    subcategory: str,
    description: str,
    vendor_id: uuid.UUID | None = None,
) -> RiskAsset:
    """The asset with a deterministic ID, created on first use and then left
    to administrators: KEEN never overwrites its fields once it exists."""
    asset = db.get(RiskAsset, asset_id)
    if asset is not None:
        return asset
    group = db.query(RiskCategory).filter_by(name=category).one_or_none()
    if group is None:
        group = RiskCategory(name=category)
        db.add(group)
        db.flush()
    sub = (
        db.query(RiskAssetSubcategory)
        .filter_by(category_id=group.id, name=subcategory)
        .one_or_none()
    )
    if sub is None:
        sub = RiskAssetSubcategory(category_id=group.id, name=subcategory)
        db.add(sub)
        db.flush()
    asset = RiskAsset(
        id=asset_id,
        name=name[:256],
        category_id=group.id,
        subcategory_id=sub.id,
        vendor_id=vendor_id,
        description=description,
    )
    db.add(asset)
    db.flush()
    return asset


def record(
    db: Session,
    user,
    body: PostureRiskInput,
    *,
    origin: str,
    origin_key: str,
    scope_key: str,
    check_key: str,
    subject_key: str = "",
    asset_id: uuid.UUID,
    controls: list[str],
    framework: str,
    request_path: str,
) -> Risk:
    """Create the risk and its posture link in the caller's transaction."""
    if origin not in ORIGINS:
        raise DomainError(400, "Unknown posture origin")
    strategy = body.treatment_strategy.strip().lower()
    if strategy not in TREATMENT_STRATEGIES:
        raise DomainError(400, "Invalid treatment strategy")
    if strategy == "accept":
        # Acceptance must be owned and must lapse, so it is reviewed.
        if body.risk_owner_user_id is None:
            raise DomainError(400, "An accepted risk needs an owner")
        if body.treatment_due_at is None:
            raise DomainError(400, "An accepted risk needs a review date")
        if body.treatment_due_at < utc_now_naive().date():
            raise DomainError(400, "The review date must not be in the past")
    title = body.title.strip()
    if not title:
        raise DomainError(400, "A risk needs a title")
    payload = RiskUpsertPayload(
        asset_id=asset_id,
        title=title,
        threat_summary=body.threat_summary,
        treatment_strategy=strategy,
        treatment_due_at=body.treatment_due_at,
        risk_owner_user_id=body.risk_owner_user_id,
        framework=framework,
        controls=controls,
    )
    risk = insert_risk(payload=payload, user=user, db=db, request_path=request_path)
    db.add(
        PostureRiskLink(
            origin=origin,
            origin_key=origin_key,
            scope_key=scope_key,
            check_key=check_key,
            subject_key=subject_key,
            risk_id=risk.id,
            created_by_user_id=user.id,
        )
    )
    db.flush()
    return risk
