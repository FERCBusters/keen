"""ISMS personal domain operations."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db.models import (
    IsmsMeeting,
    IsmsMeetingAttendee,
    IsmsMeetingPerson,
    IsmsObjective,
    IsmsObjectiveResourceUser,
    IsmsOrgNode,
    IsmsOrgNodeUser,
    IsmsPerson,
    RiskAsset,
    User,
)
from app.services.frameworks import framework_parameter

from .access import can_read_isms, meeting_ids_for
from .serializers import (
    _asset_out,
    _meeting_out,
    _objective_out,
    _org_node_out,
    meeting_for_attendee,
)


def my_isms_objectives(*, framework: str, user: User, db: Session):
    fw = framework_parameter(framework)
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


def my_isms_role(*, user: User, db: Session):
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


def my_isms_meetings(*, framework: str, user: User, db: Session):
    """Meetings the person is on, through their account or People record."""
    fw = framework_parameter(framework)
    rows = (
        db.query(IsmsMeeting)
        .filter(IsmsMeeting.id.in_(meeting_ids_for(user)))
        .order_by(
            IsmsMeeting.date.desc(),
            IsmsMeeting.start_time.desc().nullslast(),
            IsmsMeeting.title.asc(),
        )
        .all()
    )
    types: dict[uuid.UUID, set[str]] = {}
    for meeting_id, kind in [
        *db.query(
            IsmsMeetingAttendee.meeting_id, IsmsMeetingAttendee.attendance_type
        ).filter(IsmsMeetingAttendee.user_id == user.id),
        *db.query(IsmsMeetingPerson.meeting_id, IsmsMeetingPerson.attendance_type)
        .join(IsmsPerson, IsmsPerson.id == IsmsMeetingPerson.person_id)
        .filter(IsmsPerson.user_id == user.id),
    ]:
        types.setdefault(meeting_id, set()).add(kind)
    readable = can_read_isms(db, user)
    items = []
    labels = {"attendee": "Attendee", "apology": "Apology"}
    for meeting in rows:
        mine = [kind for kind in labels if kind in types.get(meeting.id, set())]
        out = _meeting_out(db, meeting, fw)
        out["my_attendance_types"] = mine
        out["my_attendance_label"] = ", ".join(labels[kind] for kind in mine)
        items.append(out if readable else meeting_for_attendee(out))
    return {"framework": fw, "items": items}


def my_isms_assets(*, framework: str, user: User, db: Session):
    fw = framework_parameter(framework)
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
        .filter(RiskAsset.owner_org_node_id.in_(node_ids))
        .order_by(func.lower(RiskAsset.name).asc())
        .all()
    )
    items = []
    node_id_set = {str(nid) for nid in node_ids}
    for row in rows:
        out = _asset_out(db, row, fw)
        owner_id = str(row.owner_org_node_id) if row.owner_org_node_id else None
        out["is_owner"] = owner_id in node_id_set
        items.append(out)
    return {"framework": fw, "items": items}
