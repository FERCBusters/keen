"""Map HTTP adapters: the records a reader can open and how they relate."""

from fastapi import Depends, HTTPException, Query, Request
from mosp.api.context import change_context
from mosp.api.routing import Router
from mosp.core.valkey import get_valkey
from mosp.security.auth import require_authenticated
from mosp.security.rate_limit import fixed_window_allow
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.services import record_map as service

router = Router()

# Building the graph reads every kind of record, so each account has an
# allowance of requests a minute; the page asks once to open and once for
# each record a person expands. Without Valkey the allowance is not counted.
REQUESTS_PER_MINUTE = 60


def _allowance(request: Request) -> None:
    user = require_authenticated(request)
    allowed, retry = fixed_window_allow(
        get_valkey(), f"keen:map:{user.id}", REQUESTS_PER_MINUTE, 60
    )
    if not allowed:
        raise HTTPException(
            429,
            "The Map was asked for too often; try again shortly.",
            headers={"Retry-After": str(retry)},
        )


@router.get("/v1/map")
def overview(
    request: Request,
    framework: str = settings.default_framework_slug,
    evidence: bool = False,
    _: None = Depends(_allowance),
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
    _: None = Depends(_allowance),
    db: Session = Depends(get_db),
):
    "One record with every record related to it, as `kind:id` names it.\n\n    A control's neighbours include its clauses and the sources that supply its\n    evidence; a source's are the controls it supplies.\n"
    return service.neighbours(
        context=change_context(request), framework=framework, node=node, db=db
    )
