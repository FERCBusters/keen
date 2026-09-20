from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.api.utils import control_justification as _control_justification
from app.api.utils import control_upstream_url as _control_upstream_url
from app.api.utils import ref_sort_key as _ref_sort_key
from app.api.utils import try_uuid as _try_uuid
from app.core.config import settings
from app.db.models import (
    ControlItem,
    InterestedParty,
    InterestedPartyCommunication,
    InterestedPartyControlLink,
    InterestedPartyName,
    InterestedPartyNature,
    User,
)
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.security.permissions import has_permission
from app.services.entity_changelog import (
    list_entity_changelogs,
    record_entity_changelog,
)

router = APIRouter()

INTERESTED_PARTIES_READ_PERMISSION = "interested_parties.read"
INTERESTED_PARTIES_MANAGE_PERMISSION = "interested_parties.manage"
RISK_READ_PERMISSION = "risk.read"
RISK_MANAGE_PERMISSION = "risk.manage"


def _split_filter_values(value: str | None) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _uuid_filter_values(value: str | None, *, label: str) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for part in _split_filter_values(value):
        parsed = _try_uuid(part)
        if not parsed:
            raise HTTPException(
                status_code=400, detail=f"{label} must contain UUID values"
            )
        out.append(parsed)
    return out


COMMUNICATION_EVENTS = [
    "Business as Usual",
    "Incident",
    "Alert",
    "Notifiable Event",
]
COMMUNICATION_WHEN = [
    "As Required",
    "Upon Identification",
    "Routine",
    "Within 24 hours",
    "Within 48 hours",
    "Within 72 hours",
    "Within a week",
    "Within a month",
]
COMMUNICATION_WITH_WHOM = [
    "Individual member",
    "Individual staff member",
    "Senior Supplier Point of Contact",
    "Supplier Point of Contact",
    "NCSC",
    "ICO",
    "Sender",
]
COMMUNICATION_METHODS = [
    "Email",
    "Google Chat",
    "Telephone",
    "Fastest means possible",
    "ICO Portal",
    "As Appropriate",
]


class LookupPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)


class CommunicationPayload(BaseModel):
    event: str = Field(..., min_length=1, max_length=64)
    when: str = Field(..., min_length=1, max_length=64)
    with_whom: str = Field(..., min_length=1, max_length=128)
    methods: list[str] = Field(default_factory=list)


class InterestedPartyPayload(BaseModel):
    framework: str | None = Field(default=None, max_length=64)
    name_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=256)
    nature_id: uuid.UUID | None = None
    nature: str | None = Field(default=None, max_length=256)
    note: str | None = Field(default=None, max_length=4000)
    controls: list[str] | None = None
    communications: list[CommunicationPayload] | None = None


class ControlLinksPayload(BaseModel):
    controls: list[str] = Field(default_factory=list)


class CommunicationsPayload(BaseModel):
    items: list[CommunicationPayload] = Field(default_factory=list)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _can_read_interested_parties(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return bool(
        has_permission(db, user, INTERESTED_PARTIES_MANAGE_PERMISSION)
        or has_permission(db, user, INTERESTED_PARTIES_READ_PERMISSION)
        # Backwards-compatible with current risk deployments until the new
        # permission pair is explicitly assigned.
        or has_permission(db, user, RISK_MANAGE_PERMISSION)
        or has_permission(db, user, RISK_READ_PERMISSION)
    )


def _can_manage_interested_parties(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return bool(
        has_permission(db, user, INTERESTED_PARTIES_MANAGE_PERMISSION)
        or has_permission(db, user, RISK_MANAGE_PERMISSION)
    )


def require_interested_parties_read(
    request: Request, db: Session = Depends(get_db)
) -> User:
    user = require_authenticated(request)
    if not _can_read_interested_parties(db, user):
        raise HTTPException(
            status_code=403, detail="interested_parties.read permission required"
        )
    return user


def require_interested_parties_manage(
    request: Request, db: Session = Depends(get_db)
) -> User:
    user = require_authenticated(request)
    if not _can_manage_interested_parties(db, user):
        raise HTTPException(
            status_code=403, detail="interested_parties.manage permission required"
        )
    return user


def _clean_text(
    raw: str | None, *, max_len: int, required: bool = False, label: str = "value"
) -> str:
    value = (raw or "").strip()
    if required and not value:
        raise HTTPException(status_code=400, detail=f"{label} is required")
    if len(value) > max_len:
        raise HTTPException(status_code=400, detail=f"{label} is too long")
    return value


def _clean_framework(raw: str | None) -> str:
    value = (raw or settings.default_framework_slug or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="framework is required")
    if len(value) > 64 or not re.match(r"^[A-Za-z0-9._:-]+$", value):
        raise HTTPException(status_code=400, detail="Invalid framework")
    return value


def _choice(raw: str | None, choices: list[str], label: str) -> str:
    value = _clean_text(raw, max_len=128, required=True, label=label)
    found = {x.lower(): x for x in choices}.get(value.lower())
    if not found:
        raise HTTPException(status_code=400, detail=f"{label} is not valid")
    return found


def _methods(raw: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in raw or []:
        value = _choice(str(item), COMMUNICATION_METHODS, "method")
        key = value.lower()
        if key not in seen:
            seen.add(key)
            out.append(value)
    if not out:
        raise HTTPException(status_code=400, detail="At least one method is required")
    return out


def _control_out(control: ControlItem | None) -> dict[str, Any]:
    if not control:
        return {}
    return {
        "id": str(control.id),
        "framework": control.framework_slug,
        "type": control.type,
        "ref": control.ref,
        "title": control.title,
        "in_scope": control.in_scope,
        "justification": _control_justification(control),
        "upstream_url": _control_upstream_url(control),
    }


def _name_out(row: InterestedPartyName | None) -> dict[str, Any]:
    return {
        "id": str(row.id) if row else None,
        "name": row.name if row else None,
        "created_at": row.created_at.isoformat() if row and row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
    }


def _nature_out(row: InterestedPartyNature | None) -> dict[str, Any]:
    return _name_out(row)  # same public shape


def _communication_out(row: InterestedPartyCommunication | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": str(row.id),
        "event": row.event,
        "when": row.when,
        "with_whom": row.with_whom,
        "methods": list(row.methods or []),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _party_controls(
    db: Session, party_id: uuid.UUID, framework: str
) -> list[ControlItem]:
    rows = (
        db.query(ControlItem)
        .join(
            InterestedPartyControlLink,
            InterestedPartyControlLink.control_item_id == ControlItem.id,
        )
        .filter(
            InterestedPartyControlLink.interested_party_id == party_id,
            InterestedPartyControlLink.framework_slug == framework,
            ControlItem.framework_slug == framework,
        )
        .all()
    )
    return sorted(rows, key=lambda c: _ref_sort_key(c.ref))


def _party_out(
    db: Session,
    row: InterestedParty,
    *,
    include_controls: bool = True,
    include_communications: bool = True,
) -> dict[str, Any]:
    controls = (
        _party_controls(db, row.id, row.framework_slug) if include_controls else []
    )
    comms = list(row.communications or []) if include_communications else []
    return {
        "id": str(row.id),
        "framework": row.framework_slug,
        "name": _name_out(row.name),
        "name_id": str(row.name_id),
        "nature": _nature_out(row.nature),
        "nature_id": str(row.nature_id),
        "note": row.note or "",
        "label": f"{row.name.name if row.name else 'Interested party'} — {row.nature.name if row.nature else 'Nature of Interest'}",
        "controls": [_control_out(c) for c in controls],
        "communications": [_communication_out(c) for c in comms],
        "control_count": len(controls),
        "communication_count": len(comms),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _new_bipartite_graph(
    *, key: str, title: str, left_label: str, right_label: str, empty: str
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "left_label": left_label,
        "right_label": right_label,
        "empty": empty,
        "nodes": {"left": [], "right": []},
        "links": [],
        "stats": {"left_count": 0, "right_count": 0, "link_count": 0},
    }


def _add_bipartite_link(
    graph: dict[str, Any],
    *,
    left: dict[str, Any],
    right: dict[str, Any],
    detail: dict[str, Any] | None = None,
) -> None:
    """Add or aggregate a link in a small client-rendered bipartite graph."""

    left_nodes = graph.setdefault("_left_nodes", {})
    right_nodes = graph.setdefault("_right_nodes", {})
    links = graph.setdefault("_links", {})
    left_id = str(left.get("id") or "")
    right_id = str(right.get("id") or "")
    if not left_id or not right_id:
        return
    left_nodes.setdefault(left_id, {**left, "id": left_id, "count": 0})["count"] += 1
    right_nodes.setdefault(right_id, {**right, "id": right_id, "count": 0})[
        "count"
    ] += 1
    link_key = (left_id, right_id)
    link = links.setdefault(
        link_key,
        {"source": left_id, "target": right_id, "count": 0, "details": []},
    )
    link["count"] += 1
    if detail:
        link["details"].append(detail)


def _finalise_bipartite_graph(graph: dict[str, Any]) -> dict[str, Any]:
    left_nodes = graph.pop("_left_nodes", {})
    right_nodes = graph.pop("_right_nodes", {})
    links = graph.pop("_links", {})

    def _node_sort(row: dict[str, Any]) -> tuple[Any, ...]:
        kind = row.get("kind")
        if kind == "control":
            return (_ref_sort_key(row.get("ref")), str(row.get("label") or "").lower())
        return (-int(row.get("count") or 0), str(row.get("label") or "").lower())

    graph["nodes"] = {
        "left": sorted(left_nodes.values(), key=_node_sort),
        "right": sorted(right_nodes.values(), key=_node_sort),
    }
    graph["links"] = sorted(
        links.values(),
        key=lambda row: (
            str(row.get("source") or ""),
            -int(row.get("count") or 0),
            str(row.get("target") or ""),
        ),
    )
    graph["stats"] = {
        "left_count": len(graph["nodes"]["left"]),
        "right_count": len(graph["nodes"]["right"]),
        "link_count": len(graph["links"]),
    }
    return graph


def _interested_party_bipartite_graphs(
    items: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    graphs = {
        "party_controls": _new_bipartite_graph(
            key="party_controls",
            title="Interested Parties → Controls",
            left_label="Interested Parties",
            right_label="Controls",
            empty="No interested parties have linked controls yet.",
        ),
        "party_communications": _new_bipartite_graph(
            key="party_communications",
            title="Interested Parties → Communications",
            left_label="Interested Parties",
            right_label="Communication rules",
            empty="No interested parties have communication rules yet.",
        ),
    }
    for item in items:
        party = {
            "id": item["id"],
            "label": item.get("label") or (item.get("name") or {}).get("name"),
            "subtitle": (item.get("nature") or {}).get("name") or "Nature of Interest",
            "note": item.get("note") or "",
            "kind": "party",
        }
        nature_name = (item.get("nature") or {}).get("name") or "Unspecified"
        for control in item.get("controls") or []:
            control_label = (
                f"{control.get('ref') or ''} {control.get('title') or ''}".strip()
            )
            _add_bipartite_link(
                graphs["party_controls"],
                left=party,
                right={
                    "id": str(control.get("id") or ""),
                    "label": control_label or "Control",
                    "subtitle": control.get("title") or "",
                    "kind": "control",
                    "ref": control.get("ref"),
                },
                detail={
                    "party": party.get("label"),
                    "nature": nature_name,
                    "control_ref": control.get("ref"),
                    "control_title": control.get("title"),
                },
            )
        for comm in item.get("communications") or []:
            event = comm.get("event") or "Unspecified"
            when = comm.get("when") or "Unspecified"
            with_whom = comm.get("with_whom") or "Unspecified"
            methods = [str(m) for m in (comm.get("methods") or []) if str(m).strip()]
            method_label = ", ".join(methods) if methods else "No method"
            communication_id = "|".join(
                ["communication", event, when, with_whom, method_label]
            )
            _add_bipartite_link(
                graphs["party_communications"],
                left=party,
                right={
                    "id": communication_id,
                    "label": f"{event} → {with_whom}",
                    "subtitle": f"{when} • {method_label}",
                    "kind": "communication",
                },
                detail={
                    "event": event,
                    "when": when,
                    "with_whom": with_whom,
                    "methods": methods,
                },
            )
    return {key: _finalise_bipartite_graph(graph) for key, graph in graphs.items()}


def _party_changelog_state(
    db: Session, row: InterestedParty | uuid.UUID | str
) -> dict[str, Any] | None:
    party = row if isinstance(row, InterestedParty) else None
    if party is None:
        pid = row if isinstance(row, uuid.UUID) else uuid.UUID(str(row))
        party = (
            db.query(InterestedParty).filter(InterestedParty.id == pid).one_or_none()
        )
    if party is None:
        return None
    out = _party_out(db, party, include_controls=True, include_communications=True)
    return {
        "id": out["id"],
        "framework": out["framework"],
        "display": out["label"],
        "name": out["name"].get("name"),
        "nature": out["nature"].get("name"),
        "note": out.get("note") or "",
        "controls": [
            {"id": c["id"], "ref": c.get("ref"), "title": c.get("title")}
            for c in out.get("controls", [])
        ],
        "communications": [
            {
                "event": c.get("event"),
                "when": c.get("when"),
                "with_whom": c.get("with_whom"),
                "methods": c.get("methods") or [],
            }
            for c in out.get("communications", [])
        ],
        "created_at": out.get("created_at"),
        "updated_at": out.get("updated_at"),
    }


def _party_or_404(db: Session, party_id: str) -> InterestedParty:
    pid = _try_uuid(party_id)
    if not pid:
        raise HTTPException(
            status_code=400, detail="interested_party_id must be a UUID"
        )
    row = db.query(InterestedParty).filter(InterestedParty.id == pid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Interested party not found")
    return row


def _lookup_by_id_or_name(
    db: Session,
    model: type[InterestedPartyName] | type[InterestedPartyNature],
    *,
    row_id: uuid.UUID | None = None,
    name: str | None = None,
    user: User | None = None,
    create: bool = False,
    label: str = "name",
) -> InterestedPartyName | InterestedPartyNature:
    if row_id:
        row = db.query(model).filter(model.id == row_id).one_or_none()
        if not row:
            raise HTTPException(status_code=400, detail=f"Unknown {label}_id")
        return row
    value = _clean_text(name, max_len=256, required=True, label=label)
    row = db.query(model).filter(func.lower(model.name) == value.lower()).one_or_none()
    if row:
        return row
    if not create:
        raise HTTPException(status_code=400, detail=f"Unknown {label}")
    row = model(
        name=value,
        created_by_user_id=getattr(user, "id", None),
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _control_by_ref_or_id(db: Session, raw: str, framework: str) -> ControlItem:
    value = (raw or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="control id/ref cannot be blank")
    cid = _try_uuid(value)
    qry = db.query(ControlItem).filter(ControlItem.framework_slug == framework)
    if cid:
        control = qry.filter(ControlItem.id == cid).one_or_none()
    else:
        control = qry.filter(func.lower(ControlItem.ref) == value.lower()).one_or_none()
    if not control:
        raise HTTPException(status_code=400, detail=f"Unknown control: {value}")
    return control


def _controls_for_payload(
    db: Session, controls: list[str] | None, framework: str
) -> list[ControlItem]:
    seen: set[uuid.UUID] = set()
    out: list[ControlItem] = []
    for raw in controls or []:
        control = _control_by_ref_or_id(db, str(raw), framework)
        if control.id not in seen:
            seen.add(control.id)
            out.append(control)
    return sorted(out, key=lambda c: _ref_sort_key(c.ref))


def _replace_control_links(
    db: Session, party: InterestedParty, controls: list[ControlItem]
) -> None:
    db.query(InterestedPartyControlLink).filter(
        InterestedPartyControlLink.interested_party_id == party.id,
        InterestedPartyControlLink.framework_slug == party.framework_slug,
    ).delete(synchronize_session=False)
    now = _utcnow()
    for control in controls:
        db.add(
            InterestedPartyControlLink(
                interested_party_id=party.id,
                framework_slug=party.framework_slug,
                control_item_id=control.id,
                created_at=now,
            )
        )
    db.flush()


def _communication_from_payload(payload: CommunicationPayload) -> dict[str, Any]:
    return {
        "event": _choice(payload.event, COMMUNICATION_EVENTS, "event"),
        "when": _choice(payload.when, COMMUNICATION_WHEN, "when"),
        "with_whom": _choice(payload.with_whom, COMMUNICATION_WITH_WHOM, "with_whom"),
        "methods": _methods(payload.methods),
    }


def _replace_communications(
    db: Session, party: InterestedParty, items: list[CommunicationPayload]
) -> None:
    db.query(InterestedPartyCommunication).filter(
        InterestedPartyCommunication.interested_party_id == party.id
    ).delete(synchronize_session=False)
    now = _utcnow()
    for payload in items or []:
        data = _communication_from_payload(payload)
        db.add(
            InterestedPartyCommunication(
                interested_party_id=party.id,
                created_at=now,
                updated_at=now,
                **data,
            )
        )
    db.flush()
    try:
        db.expire(party, ["communications"])
    except Exception:
        pass


@router.get("/v1/interested-parties/meta")
def interested_parties_meta(
    request: Request,
    framework: str = settings.default_framework_slug,
    db: Session = Depends(get_db),
):
    user = require_authenticated(request)
    return {
        "framework": _clean_framework(framework),
        "can_view": _can_read_interested_parties(db, user),
        "can_manage": _can_manage_interested_parties(db, user),
        "events": COMMUNICATION_EVENTS,
        "when": COMMUNICATION_WHEN,
        "with_whom": COMMUNICATION_WITH_WHOM,
        "methods": COMMUNICATION_METHODS,
    }


@router.get("/v1/interested-parties/names")
def list_interested_party_names(
    q: str | None = None,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    qry = db.query(InterestedPartyName)
    if q:
        qry = qry.filter(InterestedPartyName.name.ilike(f"%{q.strip()}%"))
    rows = qry.order_by(func.lower(InterestedPartyName.name)).all()
    return {"items": [_name_out(r) for r in rows]}


@router.post("/v1/interested-parties/names")
def create_interested_party_name(
    payload: LookupPayload,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    row = _lookup_by_id_or_name(
        db, InterestedPartyName, name=payload.name, user=user, create=True, label="name"
    )
    db.commit()
    db.refresh(row)
    return _name_out(row)


@router.patch("/v1/interested-parties/names/{name_id}")
def update_interested_party_name(
    name_id: str,
    payload: LookupPayload,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    nid = _try_uuid(name_id)
    if not nid:
        raise HTTPException(status_code=400, detail="name_id must be a UUID")
    row = (
        db.query(InterestedPartyName)
        .filter(InterestedPartyName.id == nid)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Name not found")
    value = _clean_text(payload.name, max_len=256, required=True, label="name")
    duplicate = (
        db.query(InterestedPartyName)
        .filter(
            func.lower(InterestedPartyName.name) == value.lower(),
            InterestedPartyName.id != row.id,
        )
        .one_or_none()
    )
    if duplicate:
        raise HTTPException(status_code=400, detail="Name already exists")
    row.name = value
    row.updated_at = _utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return _name_out(row)


@router.delete("/v1/interested-parties/names/{name_id}")
def delete_interested_party_name(
    name_id: str,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    nid = _try_uuid(name_id)
    if not nid:
        raise HTTPException(status_code=400, detail="name_id must be a UUID")
    row = (
        db.query(InterestedPartyName)
        .filter(InterestedPartyName.id == nid)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Name not found")
    if db.query(InterestedParty).filter(InterestedParty.name_id == row.id).first():
        raise HTTPException(
            status_code=400, detail="Name is used by an interested party"
        )
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/v1/interested-parties/natures")
def list_interested_party_natures(
    q: str | None = None,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    qry = db.query(InterestedPartyNature)
    if q:
        qry = qry.filter(InterestedPartyNature.name.ilike(f"%{q.strip()}%"))
    rows = qry.order_by(func.lower(InterestedPartyNature.name)).all()
    return {"items": [_nature_out(r) for r in rows]}


@router.post("/v1/interested-parties/natures")
def create_interested_party_nature(
    payload: LookupPayload,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    row = _lookup_by_id_or_name(
        db,
        InterestedPartyNature,
        name=payload.name,
        user=user,
        create=True,
        label="nature",
    )
    db.commit()
    db.refresh(row)
    return _nature_out(row)


@router.patch("/v1/interested-parties/natures/{nature_id}")
def update_interested_party_nature(
    nature_id: str,
    payload: LookupPayload,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    nid = _try_uuid(nature_id)
    if not nid:
        raise HTTPException(status_code=400, detail="nature_id must be a UUID")
    row = (
        db.query(InterestedPartyNature)
        .filter(InterestedPartyNature.id == nid)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Nature not found")
    value = _clean_text(payload.name, max_len=256, required=True, label="nature")
    duplicate = (
        db.query(InterestedPartyNature)
        .filter(
            func.lower(InterestedPartyNature.name) == value.lower(),
            InterestedPartyNature.id != row.id,
        )
        .one_or_none()
    )
    if duplicate:
        raise HTTPException(status_code=400, detail="Nature already exists")
    row.name = value
    row.updated_at = _utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return _nature_out(row)


@router.delete("/v1/interested-parties/natures/{nature_id}")
def delete_interested_party_nature(
    nature_id: str,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    nid = _try_uuid(nature_id)
    if not nid:
        raise HTTPException(status_code=400, detail="nature_id must be a UUID")
    row = (
        db.query(InterestedPartyNature)
        .filter(InterestedPartyNature.id == nid)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Nature not found")
    if db.query(InterestedParty).filter(InterestedParty.nature_id == row.id).first():
        raise HTTPException(
            status_code=400, detail="Nature is used by an interested party"
        )
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/v1/interested-parties/visualisations")
def interested_parties_visualisations(
    framework: str = settings.default_framework_slug,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    rows = (
        db.query(InterestedParty)
        .join(InterestedPartyName, InterestedPartyName.id == InterestedParty.name_id)
        .join(
            InterestedPartyNature, InterestedPartyNature.id == InterestedParty.nature_id
        )
        .filter(InterestedParty.framework_slug == fw)
        .order_by(
            func.lower(InterestedPartyNature.name), func.lower(InterestedPartyName.name)
        )
        .all()
    )
    items = [_party_out(db, r) for r in rows]
    nature_counts: dict[str, int] = {}
    with_whom_counts: dict[str, int] = {}
    event_counts: dict[str, int] = {}
    method_counts: dict[str, int] = {}
    control_counts: dict[str, dict[str, Any]] = {}
    control_links: list[dict[str, Any]] = []
    communication_links: list[dict[str, Any]] = []
    for item in items:
        nature = (item.get("nature") or {}).get("name") or "Unspecified"
        nature_counts[nature] = nature_counts.get(nature, 0) + 1
        for control in item.get("controls") or []:
            key = control.get("id")
            if key:
                control_counts.setdefault(key, {"control": control, "count": 0})[
                    "count"
                ] += 1
            control_links.append(
                {
                    "party_id": item["id"],
                    "party_label": item.get("label"),
                    "name": (item.get("name") or {}).get("name"),
                    "nature": nature,
                    "control_id": control.get("id"),
                    "control_ref": control.get("ref"),
                    "control_title": control.get("title"),
                }
            )
        for comm in item.get("communications") or []:
            with_whom = comm.get("with_whom") or "Unspecified"
            event = comm.get("event") or "Unspecified"
            with_whom_counts[with_whom] = with_whom_counts.get(with_whom, 0) + 1
            event_counts[event] = event_counts.get(event, 0) + 1
            for method in comm.get("methods") or []:
                method_counts[method] = method_counts.get(method, 0) + 1
            communication_links.append(
                {
                    "party_id": item["id"],
                    "party_label": item.get("label"),
                    "name": (item.get("name") or {}).get("name"),
                    "nature": nature,
                    "event": event,
                    "when": comm.get("when"),
                    "with_whom": with_whom,
                    "methods": comm.get("methods") or [],
                }
            )
    bipartite_graphs = _interested_party_bipartite_graphs(items)
    return {
        "framework": fw,
        "items": items,
        "nature_counts": [
            {"name": k, "count": v}
            for k, v in sorted(
                nature_counts.items(), key=lambda kv: (-kv[1], kv[0].lower())
            )
        ],
        "with_whom_counts": [
            {"name": k, "count": v}
            for k, v in sorted(
                with_whom_counts.items(), key=lambda kv: (-kv[1], kv[0].lower())
            )
        ],
        "event_counts": [
            {"name": k, "count": v}
            for k, v in sorted(
                event_counts.items(), key=lambda kv: (-kv[1], kv[0].lower())
            )
        ],
        "method_counts": [
            {"name": k, "count": v}
            for k, v in sorted(
                method_counts.items(), key=lambda kv: (-kv[1], kv[0].lower())
            )
        ],
        "control_counts": sorted(
            control_counts.values(),
            key=lambda x: (
                -x["count"],
                _ref_sort_key((x.get("control") or {}).get("ref")),
            ),
        ),
        "control_links": control_links,
        "communication_links": communication_links,
        "bipartite_graphs": bipartite_graphs,
    }


@router.get("/v1/interested-parties")
def list_interested_parties(
    framework: str = settings.default_framework_slug,
    q: str | None = None,
    name_id: str | None = None,
    nature_id: str | None = None,
    control: str | None = None,
    limit: int = 200,
    offset: int = 0,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    limit = max(1, min(int(limit or 200), 5000))
    offset = max(0, int(offset or 0))
    qry = (
        db.query(InterestedParty)
        .join(InterestedPartyName, InterestedPartyName.id == InterestedParty.name_id)
        .join(
            InterestedPartyNature, InterestedPartyNature.id == InterestedParty.nature_id
        )
        .filter(InterestedParty.framework_slug == fw)
    )
    if q:
        needle = f"%{q.strip()}%"
        qry = qry.filter(
            or_(
                InterestedPartyName.name.ilike(needle),
                InterestedPartyNature.name.ilike(needle),
                InterestedParty.note.ilike(needle),
            )
        )
    name_ids = _uuid_filter_values(name_id, label="name_id")
    if name_ids:
        qry = qry.filter(InterestedParty.name_id.in_(name_ids))
    nature_ids = _uuid_filter_values(nature_id, label="nature_id")
    if nature_ids:
        qry = qry.filter(InterestedParty.nature_id.in_(nature_ids))
    if control:
        control_row = _control_by_ref_or_id(db, control, fw)
        qry = qry.join(
            InterestedPartyControlLink,
            InterestedPartyControlLink.interested_party_id == InterestedParty.id,
        ).filter(
            InterestedPartyControlLink.framework_slug == fw,
            InterestedPartyControlLink.control_item_id == control_row.id,
        )
    total = int(qry.order_by(None).count() or 0)
    rows = (
        qry.order_by(
            func.lower(InterestedPartyNature.name), func.lower(InterestedPartyName.name)
        )
        .limit(limit)
        .offset(offset)
        .all()
    )
    return {
        "framework": fw,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_party_out(db, r) for r in rows],
    }


@router.post("/v1/interested-parties")
def create_interested_party(
    payload: InterestedPartyPayload,
    request: Request,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(payload.framework)
    name = _lookup_by_id_or_name(
        db,
        InterestedPartyName,
        row_id=payload.name_id,
        name=payload.name,
        user=user,
        create=True,
        label="name",
    )
    nature = _lookup_by_id_or_name(
        db,
        InterestedPartyNature,
        row_id=payload.nature_id,
        name=payload.nature,
        user=user,
        create=True,
        label="nature",
    )
    existing = (
        db.query(InterestedParty)
        .filter(
            InterestedParty.framework_slug == fw,
            InterestedParty.name_id == name.id,
            InterestedParty.nature_id == nature.id,
        )
        .one_or_none()
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail="Interested party with this name and nature already exists",
        )
    now = _utcnow()
    row = InterestedParty(
        framework_slug=fw,
        name_id=name.id,
        nature_id=nature.id,
        note=(
            _clean_text(payload.note, max_len=4000, label="note")
            if payload.note is not None
            else ""
        ),
        created_by_user_id=getattr(user, "id", None),
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    _replace_control_links(db, row, _controls_for_payload(db, payload.controls, fw))
    if payload.communications is not None:
        _replace_communications(db, row, payload.communications)
    record_entity_changelog(
        db,
        entity_type="interested_party",
        entity_id=row.id,
        action="created",
        before=None,
        after=_party_changelog_state(db, row),
        user=user,
        request_method=request.method,
        request_path=str(request.url.path),
    )
    db.commit()
    db.refresh(row)
    return _party_out(db, row)


@router.get("/v1/interested-parties/{party_id}")
def get_interested_party(
    party_id: str,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    return _party_out(db, _party_or_404(db, party_id))


@router.patch("/v1/interested-parties/{party_id}")
def update_interested_party(
    party_id: str,
    payload: InterestedPartyPayload,
    request: Request,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    row = _party_or_404(db, party_id)
    before = _party_changelog_state(db, row)
    fw = _clean_framework(payload.framework or row.framework_slug)
    if (
        fw != row.framework_slug
        and db.query(InterestedPartyControlLink)
        .filter(InterestedPartyControlLink.interested_party_id == row.id)
        .first()
    ):
        raise HTTPException(
            status_code=400, detail="Cannot change framework while controls are linked"
        )
    row.framework_slug = fw
    if payload.note is not None:
        row.note = _clean_text(payload.note, max_len=4000, label="note")
    if payload.name_id or payload.name:
        row.name_id = _lookup_by_id_or_name(
            db,
            InterestedPartyName,
            row_id=payload.name_id,
            name=payload.name,
            user=user,
            create=True,
            label="name",
        ).id
    if payload.nature_id or payload.nature:
        row.nature_id = _lookup_by_id_or_name(
            db,
            InterestedPartyNature,
            row_id=payload.nature_id,
            name=payload.nature,
            user=user,
            create=True,
            label="nature",
        ).id
    duplicate = (
        db.query(InterestedParty)
        .filter(
            InterestedParty.id != row.id,
            InterestedParty.framework_slug == row.framework_slug,
            InterestedParty.name_id == row.name_id,
            InterestedParty.nature_id == row.nature_id,
        )
        .one_or_none()
    )
    if duplicate:
        raise HTTPException(
            status_code=400,
            detail="Interested party with this name and nature already exists",
        )
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    if payload.controls is not None:
        _replace_control_links(
            db, row, _controls_for_payload(db, payload.controls, row.framework_slug)
        )
    if payload.communications is not None:
        _replace_communications(db, row, payload.communications)
    record_entity_changelog(
        db,
        entity_type="interested_party",
        entity_id=row.id,
        action="updated",
        before=before,
        after=_party_changelog_state(db, row),
        user=user,
        request_method=request.method,
        request_path=str(request.url.path),
    )
    db.commit()
    db.refresh(row)
    return _party_out(db, row)


@router.delete("/v1/interested-parties/{party_id}")
def delete_interested_party(
    party_id: str,
    request: Request,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    row = _party_or_404(db, party_id)
    before = _party_changelog_state(db, row)
    rid = row.id
    db.delete(row)
    db.flush()
    record_entity_changelog(
        db,
        entity_type="interested_party",
        entity_id=rid,
        action="deleted",
        before=before,
        after=None,
        user=user,
        request_method=request.method,
        request_path=str(request.url.path),
    )
    db.commit()
    return {"ok": True, "id": str(rid)}


@router.get("/v1/interested-parties/{party_id}/controls")
def interested_party_controls(
    party_id: str,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    row = _party_or_404(db, party_id)
    return {
        "id": str(row.id),
        "framework": row.framework_slug,
        "controls": [
            _control_out(c) for c in _party_controls(db, row.id, row.framework_slug)
        ],
    }


@router.patch("/v1/interested-parties/{party_id}/controls")
def update_interested_party_controls(
    party_id: str,
    payload: ControlLinksPayload,
    request: Request,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    row = _party_or_404(db, party_id)
    before = _party_changelog_state(db, row)
    _replace_control_links(
        db, row, _controls_for_payload(db, payload.controls, row.framework_slug)
    )
    row.updated_at = _utcnow()
    db.add(row)
    record_entity_changelog(
        db,
        entity_type="interested_party",
        entity_id=row.id,
        action="updated",
        before=before,
        after=_party_changelog_state(db, row),
        user=user,
        request_method=request.method,
        request_path=str(request.url.path),
    )
    db.commit()
    db.refresh(row)
    return _party_out(db, row)


@router.get("/v1/interested-parties/{party_id}/communications")
def interested_party_communications(
    party_id: str,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    row = _party_or_404(db, party_id)
    return {
        "id": str(row.id),
        "items": [_communication_out(c) for c in list(row.communications or [])],
    }


@router.patch("/v1/interested-parties/{party_id}/communications")
def update_interested_party_communications(
    party_id: str,
    payload: CommunicationsPayload,
    request: Request,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    row = _party_or_404(db, party_id)
    before = _party_changelog_state(db, row)
    _replace_communications(db, row, payload.items)
    row.updated_at = _utcnow()
    db.add(row)
    record_entity_changelog(
        db,
        entity_type="interested_party",
        entity_id=row.id,
        action="updated",
        before=before,
        after=_party_changelog_state(db, row),
        user=user,
        request_method=request.method,
        request_path=str(request.url.path),
    )
    db.commit()
    db.refresh(row)
    return _party_out(db, row)


@router.get("/v1/interested-parties/{party_id}/changelog")
def interested_party_changelog(
    party_id: str,
    limit: int = 50,
    offset: int = 0,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    row = _party_or_404(db, party_id)
    return list_entity_changelogs(
        db,
        entity_type="interested_party",
        entity_id=row.id,
        limit=limit,
        offset=offset,
    )


@router.get("/v1/controls/{control_id}/interested-parties")
def control_interested_parties(
    control_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    cid = _try_uuid(control_id)
    if not cid:
        raise HTTPException(status_code=400, detail="control_id must be a UUID")
    control = (
        db.query(ControlItem)
        .filter(ControlItem.id == cid, ControlItem.framework_slug == fw)
        .one_or_none()
    )
    if not control:
        raise HTTPException(status_code=404, detail="Control not found")
    rows = (
        db.query(InterestedParty)
        .join(
            InterestedPartyControlLink,
            InterestedPartyControlLink.interested_party_id == InterestedParty.id,
        )
        .join(InterestedPartyName, InterestedPartyName.id == InterestedParty.name_id)
        .join(
            InterestedPartyNature, InterestedPartyNature.id == InterestedParty.nature_id
        )
        .filter(
            InterestedPartyControlLink.control_item_id == control.id,
            InterestedPartyControlLink.framework_slug == fw,
            InterestedParty.framework_slug == fw,
        )
        .order_by(
            func.lower(InterestedPartyNature.name), func.lower(InterestedPartyName.name)
        )
        .all()
    )
    return {
        "control": _control_out(control),
        "framework": fw,
        "items": [
            _party_out(db, r, include_controls=False, include_communications=True)
            for r in rows
        ],
    }
