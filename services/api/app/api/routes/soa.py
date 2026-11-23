from __future__ import annotations

from fastapi import Depends
from mosp.api.routing import Router
from mosp.security.auth import require_authenticated
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.services import soa as service

router = Router()


@router.get("/v1/statement-of-applicability")
def statement_of_applicability(
    framework: str = settings.default_framework_slug,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    return service.statement_of_applicability(framework=framework, user=user, db=db)
