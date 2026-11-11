"""HTTP adapters for the records a risk or PESTLE(E) factor can name as affected."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.dependencies.isms import require_isms_read
from app.api.routing import Router
from app.db.session import get_db
from app.services import affected_links as service

router = Router()


@router.get("/v1/affected/options")
def list_affected_options(
    q: str = "",
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    "People, vendors and assets a risk or PESTLE(E) factor can name as affected."
    return service.affected_options(q=q, user=user, db=db)
