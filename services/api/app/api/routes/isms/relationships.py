"""ISMS relationships; see docs/maintainability-review.md for module boundaries."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.utils import try_uuid as _try_uuid
from app.core.config import settings
from app.db.models import (
    ControlItem,
    IsmsEffectivenessMeasure,
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
)
from app.db.session import get_db
from app.services.entity_changelog import list_entity_changelogs

from .access import (
    require_isms_read,
)
from .common import (
    _clean_framework,
)
from .constants import (
    ENTITY_TYPES,
)
from .serializers import (
    _effectiveness_measure_out,
    _entity_out,
)

router = APIRouter()


@router.get("/v1/isms/{entity_type}/{entity_id}/changelog")
def isms_entity_changelog(
    entity_type: str,
    entity_id: str,
    limit: int = 50,
    offset: int = 0,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    key = (entity_type or "").strip().lower().replace("-", "_")
    if key not in ENTITY_TYPES:
        raise HTTPException(status_code=400, detail="Unknown ISMS entity type")
    _, changelog_type = ENTITY_TYPES[key]
    eid = _try_uuid(entity_id)
    if not eid:
        raise HTTPException(status_code=400, detail="entity_id must be a UUID")
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
        "objectives": [],
        "documents": [],
        "org_nodes": [],
        "assets": [],
        "effectiveness_measures": [],
        "meetings": [],
    }
    for link in links:
        et = link.entity_type
        if et not in ENTITY_TYPES:
            continue
        row = (
            db.query(ENTITY_TYPES[et][0])
            .filter(ENTITY_TYPES[et][0].id == link.entity_id)
            .one_or_none()
        )
        if not row:
            continue
        if et in {
            "application_configuration",
            "access_control_matrix",
            "effectiveness_metric",
        }:
            continue
        if entity_side == "clause" and et == "effectiveness_measure":
            # Effectiveness measures are control-scoped. Older builds allowed
            # direct clause links, but they should not surface on clause pages.
            continue
        key = {
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


@router.get("/v1/controls/{control_id}/effectiveness-measures")
def control_effectiveness_measures(
    control_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    cid = _try_uuid(control_id)
    if not cid:
        raise HTTPException(status_code=400, detail="control_id must be a UUID")
    exists = (
        db.query(ControlItem.id)
        .filter(ControlItem.id == cid, ControlItem.framework_slug == fw)
        .first()
    )
    if not exists:
        raise HTTPException(status_code=404, detail="Control not found")
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


@router.get("/v1/controls/{control_id}/isms")
def control_isms(
    control_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    cid = _try_uuid(control_id)
    if not cid:
        raise HTTPException(status_code=400, detail="control_id must be a UUID")
    return {"framework": fw, **_reverse_refs_for("control", cid, fw, db)}


@router.get("/v1/clauses/{clause_id}/isms")
def clause_isms(
    clause_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    cid = _try_uuid(clause_id)
    if not cid:
        raise HTTPException(status_code=400, detail="clause_id must be a UUID")
    return {"framework": fw, **_reverse_refs_for("clause", cid, fw, db)}
