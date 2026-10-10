"""ISMS personal; see docs/maintainability-review.md for module boundaries."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    IsmsMeeting,
    IsmsMeetingAttendee,
    IsmsObjective,
    IsmsObjectiveResourceUser,
    IsmsOrgNode,
    IsmsOrgNodeUser,
    RiskAsset,
)
from app.db.session import get_db
from app.security.auth import require_authenticated

from .common import (
    _clean_framework,
)
from .serializers import (
    _asset_out,
    _meeting_out,
    _objective_out,
    _org_node_out,
)

router = APIRouter()


@router.get("/v1/me/isms-objectives")
def my_isms_objectives(
    framework: str = settings.default_framework_slug,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    rows = (
        db.query(IsmsObjective)
        .outerjoin(
            IsmsObjectiveResourceUser,
            IsmsObjectiveResourceUser.objective_id == IsmsObjective.id,
        )
        .filter(
            or_(
                IsmsObjective.owner_user_id == user.id,
                IsmsObjectiveResourceUser.user_id == user.id,
            )
        )
        .order_by(
            IsmsObjective.completion_target_date.asc().nullslast(),
            IsmsObjective.updated_at.desc(),
        )
        .all()
    )
    return {"framework": fw, "items": [_objective_out(db, r, fw) for r in rows]}


@router.get("/v1/me/isms-role")
def my_isms_role(user=Depends(require_authenticated), db: Session = Depends(get_db)):
    links = (
        db.query(IsmsOrgNodeUser)
        .join(IsmsOrgNode, IsmsOrgNode.id == IsmsOrgNodeUser.org_node_id)
        .filter(IsmsOrgNodeUser.user_id == user.id)
        .order_by(IsmsOrgNode.sort_order.asc(), IsmsOrgNode.name.asc())
        .all()
    )
    return {
        "items": [
            {
                "relationship_type": link.relationship_type,
                "org_node": _org_node_out(link.org_node, include_people=False),
                "path": _org_path(link.org_node),
            }
            for link in links
        ]
    }


def _org_path(node: IsmsOrgNode | None) -> list[dict[str, Any]]:
    out = []
    cur = node
    seen = set()
    while cur:
        if cur.id in seen:
            break
        seen.add(cur.id)
        out.append({"id": str(cur.id), "name": cur.name, "node_type": cur.node_type})
        cur = getattr(cur, "parent", None)
    return list(reversed(out))


@router.get("/v1/me/isms-meetings")
def my_isms_meetings(
    framework: str = settings.default_framework_slug,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    attendee_links = (
        db.query(IsmsMeetingAttendee)
        .join(IsmsMeeting, IsmsMeeting.id == IsmsMeetingAttendee.meeting_id)
        .filter(IsmsMeetingAttendee.user_id == user.id)
        .order_by(
            IsmsMeeting.date.desc(),
            IsmsMeeting.start_time.desc().nullslast(),
            IsmsMeeting.title.asc(),
        )
        .all()
    )
    grouped: dict[uuid.UUID, dict[str, Any]] = {}
    for link in attendee_links:
        if not link.meeting:
            continue
        row = grouped.setdefault(
            link.meeting_id, {"meeting": link.meeting, "types": []}
        )
        if link.attendance_type not in row["types"]:
            row["types"].append(link.attendance_type)

    items = []
    label_by_type = {"attendee": "Attendee", "apology": "Apology"}
    for row in grouped.values():
        meeting = row["meeting"]
        types = list(row["types"] or [])
        out = _meeting_out(db, meeting, fw)
        out["my_attendance_types"] = types
        out["my_attendance_label"] = ", ".join(
            label_by_type.get(t, t.title()) for t in types
        )
        items.append(out)
    return {"framework": fw, "items": items}


@router.get("/v1/me/isms-assets")
def my_isms_assets(
    framework: str = settings.default_framework_slug,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    node_ids = [
        nid
        for (nid,) in db.query(IsmsOrgNodeUser.org_node_id)
        .filter(IsmsOrgNodeUser.user_id == user.id)
        .all()
    ]
    if not node_ids:
        return {"framework": fw, "items": []}
    rows = (
        db.query(RiskAsset)
        .filter(
            or_(
                RiskAsset.owner_org_node_id.in_(node_ids),
                RiskAsset.register_held_by_org_node_id.in_(node_ids),
            )
        )
        .order_by(func.lower(RiskAsset.name).asc())
        .all()
    )
    items = []
    node_id_set = {str(nid) for nid in node_ids}
    for row in rows:
        out = _asset_out(db, row, fw)
        owner_id = str(row.owner_org_node_id) if row.owner_org_node_id else None
        register_id = (
            str(row.register_held_by_org_node_id)
            if row.register_held_by_org_node_id
            else None
        )
        out["is_owner"] = owner_id in node_id_set
        out["is_register_holder"] = register_id in node_id_set
        items.append(out)
    return {"framework": fw, "items": items}
