"""ISMS relationships domain operations."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.references import try_uuid as _try_uuid
from app.db.models import (
    ControlItem,
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
    User,
)
from app.services.entity_changelog import list_entity_changelogs
from app.services.errors import DomainError
from app.services.features import state
from app.services.frameworks import framework_parameter

from .constants import ENTITY_TYPES
from .metric_values import threshold_status
from .serializers import _effectiveness_measure_out, _entity_out


def isms_entity_changelog(
    *,
    entity_type: str,
    entity_id: str,
    limit: int,
    offset: int,
    user: User,
    db: Session,
):
    key = (entity_type or "").strip().lower().replace("-", "_")
    if key not in ENTITY_TYPES:
        raise DomainError(status_code=400, detail="Unknown ISMS entity type")
    _, changelog_type = ENTITY_TYPES[key]
    eid = _try_uuid(entity_id)
    if not eid:
        raise DomainError(status_code=400, detail="entity_id must be a UUID")
    return list_entity_changelogs(
        db, entity_type=changelog_type, entity_id=eid, limit=limit, offset=offset
    )


def _reverse_refs_for(
    entity_side: str, entity_id: uuid.UUID, framework: str, db: Session
) -> dict[str, Any]:
    if entity_side == "control":
        links = (
            db.query(IsmsEntityControlLink)
            .filter(
                IsmsEntityControlLink.control_item_id == entity_id,
                IsmsEntityControlLink.framework_slug == framework,
            )
            .all()
        )
    else:
        links = (
            db.query(IsmsEntityClauseLink)
            .filter(
                IsmsEntityClauseLink.clause_id == entity_id,
                IsmsEntityClauseLink.framework_slug == framework,
            )
            .all()
        )
    grouped = {
        "swot": [],
        "objectives": [],
        "documents": [],
        "org_nodes": [],
        "assets": [],
        "effectiveness_measures": [],
        "meetings": [],
    }
    from app.services.features import ENTITY_FEATURES, state

    features = state(db)
    for link in links:
        et = link.entity_type
        if et in ENTITY_FEATURES and not features.enabled(ENTITY_FEATURES[et]):
            continue
        if et not in ENTITY_TYPES:
            continue
        row = (
            db.query(ENTITY_TYPES[et][0])
            .filter(ENTITY_TYPES[et][0].id == link.entity_id)
            .one_or_none()
        )
        if not row:
            continue
        if et in {"access_control_matrix", "effectiveness_metric"}:
            continue
        key = {
            "swot": "swot",
            "objective": "objectives",
            "document": "documents",
            "org_node": "org_nodes",
            "asset": "assets",
            "effectiveness_measure": "effectiveness_measures",
            "meeting": "meetings",
        }[et]
        grouped[key].append(_entity_out(db, et, row, framework, include_links=False))
    grouped["total"] = sum(len(v) for v in grouped.values() if isinstance(v, list))
    return grouped


def control_effectiveness_measures(
    *, control_id: str, framework: str, user: User, db: Session
):
    fw = framework_parameter(framework)
    cid = _try_uuid(control_id)
    if not cid:
        raise DomainError(status_code=400, detail="control_id must be a UUID")
    exists = (
        db.query(ControlItem.id)
        .filter(ControlItem.id == cid, ControlItem.framework_slug == fw)
        .first()
    )
    if not exists:
        raise DomainError(status_code=404, detail="Control not found")
    rows = (
        db.query(IsmsEffectivenessMeasure)
        .join(
            IsmsEntityControlLink,
            IsmsEntityControlLink.entity_id == IsmsEffectivenessMeasure.id,
        )
        .filter(
            IsmsEffectivenessMeasure.framework_slug == fw,
            IsmsEntityControlLink.entity_type == "effectiveness_measure",
            IsmsEntityControlLink.framework_slug == fw,
            IsmsEntityControlLink.control_item_id == cid,
        )
        .order_by(IsmsEffectivenessMeasure.updated_at.desc())
        .all()
    )
    return {
        "framework": fw,
        "control_id": str(cid),
        "items": [_effectiveness_measure_out(db, row, fw) for row in rows],
    }


def control_requirements(*, framework: str, user: User, db: Session):
    """What each control of a framework has towards its requirements.

    For every control with links: how many policies and processes it has, and
    how its effectiveness measures stand against their targets on their latest
    results. Controls without links are left out. Evidence is counted with the
    controls' statistics, for people who can read events.
    """
    fw = framework_parameter(framework)
    features = state(db)
    out: dict[str, Any] = {
        "framework": fw,
        "policies": features.enabled("documents"),
        "measures": features.enabled("effectiveness"),
        "items": {},
    }

    def item(control_id: uuid.UUID) -> dict[str, int]:
        return out["items"].setdefault(
            str(control_id),
            {"policies": 0, "measures": 0, "met": 0, "judged": 0, "unrecorded": 0},
        )

    link = IsmsEntityControlLink
    if out["policies"]:
        for control_id, count in (
            db.query(link.control_item_id, func.count(func.distinct(link.entity_id)))
            .join(IsmsDocument, IsmsDocument.id == link.entity_id)
            .filter(link.entity_type == "document", link.framework_slug == fw)
            .group_by(link.control_item_id)
        ):
            item(control_id)["policies"] = int(count)
    if out["measures"]:
        entry = IsmsEffectivenessMetricEntry
        # One latest result per measure, as the home page ranks them.
        ranked = (
            db.query(
                entry.measure_id.label("measure_id"),
                entry.metric_value.label("value"),
                entry.metric_unit.label("unit"),
                func.row_number()
                .over(
                    partition_by=entry.measure_id,
                    order_by=(
                        entry.recorded_at.desc(),
                        entry.created_at.desc(),
                        entry.id.desc(),
                    ),
                )
                .label("rank"),
            )
            .join(IsmsEffectivenessMeasure)
            .filter(IsmsEffectivenessMeasure.framework_slug == fw)
            .subquery()
        )
        rows = (
            db.query(
                link.control_item_id,
                IsmsEffectivenessMeasure,
                ranked.c.measure_id,
                ranked.c.value,
                ranked.c.unit,
            )
            .join(
                IsmsEffectivenessMeasure, IsmsEffectivenessMeasure.id == link.entity_id
            )
            .outerjoin(
                ranked,
                (ranked.c.measure_id == IsmsEffectivenessMeasure.id)
                & (ranked.c.rank == 1),
            )
            .filter(
                link.entity_type == "effectiveness_measure",
                link.framework_slug == fw,
                IsmsEffectivenessMeasure.framework_slug == fw,
            )
        )
        for control_id, measure, recorded, value, unit in rows:
            counts = item(control_id)
            counts["measures"] += 1
            if recorded is None:
                counts["unrecorded"] += 1
                continue
            status = threshold_status(
                value,
                measure.target_value,
                measure.threshold_operator,
                unit or "",
                measure.target_unit or "",
            )
            counts["judged"] += status is not None
            counts["met"] += status == "met"
    return out


def control_isms(*, control_id: str, framework: str, user: User, db: Session):
    fw = framework_parameter(framework)
    cid = _try_uuid(control_id)
    if not cid:
        raise DomainError(status_code=400, detail="control_id must be a UUID")
    return {"framework": fw, **_reverse_refs_for("control", cid, fw, db)}


def clause_isms(*, clause_id: str, framework: str, user: User, db: Session):
    fw = framework_parameter(framework)
    cid = _try_uuid(clause_id)
    if not cid:
        raise DomainError(status_code=400, detail="clause_id must be a UUID")
    return {"framework": fw, **_reverse_refs_for("clause", cid, fw, db)}
