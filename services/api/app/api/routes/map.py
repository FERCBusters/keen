"""Map HTTP adapters: the records a reader can open and how they relate."""

from fastapi import Depends, Query, Request
from mosp.api.context import change_context
from mosp.api.routing import Router
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.services import record_map as service

router = Router()


@router.get("/v1/map")
def overview(
    request: Request,
    framework: str = settings.default_framework_slug,
    evidence: bool = False,
    db: Session = Depends(get_db),
):
    "Records and their relationships as a graph, without events.\n\n    Each node is a record the caller can read and each link joins two of them.\n    Controls and clauses appear when a record refers to them. With `evidence`,\n    sources join the graph, weighted by the events they supply to each control.\n"
    return service.overview(
        context=change_context(request), framework=framework, evidence=evidence, db=db
    )


@router.get("/v1/map/neighbours")
def neighbours(
    request: Request,
    node: str = Query(..., min_length=3, max_length=320),
    framework: str = settings.default_framework_slug,
    db: Session = Depends(get_db),
):
    "One record with every record related to it, as `kind:id` names it.\n\n    A control's neighbours include its clauses and the sources that supply its\n    evidence; a source's are the controls it supplies.\n"
    return service.neighbours(
        context=change_context(request), framework=framework, node=node, db=db
    )
