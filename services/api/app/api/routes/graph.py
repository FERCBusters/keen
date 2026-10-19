from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.context import change_context
from app.api.routing import Router
from app.core.config import settings
from app.db.session import get_db
from app.services import graph as service
from app.services.event_filters import parse_event_filters

router = Router()


def _scope(
    framework: str,
    source: Optional[str],
    control: Optional[str],
    clause: Optional[str],
    rule: Optional[str],
    q: Optional[str],
    conditions: list[str],
    unmapped: bool,
):
    """The Events page filters a chart follows; the time window is separate."""
    return parse_event_filters(
        framework=framework,
        q=q,
        source=source,
        control=control,
        clause=clause,
        rule=rule,
        conditions=conditions,
        unmapped=unmapped,
    )


@router.get("/v1/graph/control_source")
def graph_control_source(
    request: Request,
    framework: str = settings.default_framework_slug,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    source: Optional[str] = None,
    control: Optional[str] = None,
    clause: Optional[str] = None,
    rule: Optional[str] = None,
    q: Optional[str] = None,
    conditions: list[str] = Query(default=[], alias="f"),
    unmapped: bool = False,
    db: Session = Depends(get_db),
):
    "Graph for visualisation: Sources -> Controls.\n\n    Counts are **distinct events**, not number of mappings. One event can map to\n    multiple controls, so counting mappings would inflate totals.\n"
    return service.graph_control_source(
        context=change_context(request),
        framework=framework,
        start_date=start_date,
        end_date=end_date,
        scope=_scope(framework, source, control, clause, rule, q, conditions, unmapped),
        db=db,
    )


@router.get("/v1/graph/clause_source")
def graph_clause_source(
    request: Request,
    framework: str = settings.default_framework_slug,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    source: Optional[str] = None,
    control: Optional[str] = None,
    clause: Optional[str] = None,
    rule: Optional[str] = None,
    q: Optional[str] = None,
    conditions: list[str] = Query(default=[], alias="f"),
    unmapped: bool = False,
    db: Session = Depends(get_db),
):
    "Graph for visualisation: Sources -> Clauses by mapped-event evidence.\n\n    This is materially more expensive than the source -> control graph because it\n    has to fan mapped controls out through clause applicability links. Cache it\n    with the same short-lived aggregate cache used by other UI count endpoints,\n    while keeping the cache scoped to the current user visibility preferences.\n"
    return service.graph_clause_source(
        context=change_context(request),
        framework=framework,
        start_date=start_date,
        end_date=end_date,
        scope=_scope(framework, source, control, clause, rule, q, conditions, unmapped),
        db=db,
    )


@router.get("/v1/graph/clause_control")
def graph_clause_control(
    framework: str = settings.default_framework_slug,
    db: Session = Depends(get_db),
):
    "Static relationship graph: Clauses -> Controls."
    return service.graph_clause_control(framework=framework, db=db)


@router.get("/v1/graph/risk_control")
def graph_risk_control(
    request: Request,
    framework: str = settings.default_framework_slug,
    db: Session = Depends(get_db),
):
    "Static relationship graph: Risks -> Controls for the selected framework.\n\n    Risks are global, but their mitigating-control assignments are scoped by\n    framework_slug, because a DVSTF control set and an iso_27001_2022 control set\n    are different catalogues even when the underlying risk is the same.\n"
    return service.graph_risk_control(
        context=change_context(request), framework=framework, db=db
    )
