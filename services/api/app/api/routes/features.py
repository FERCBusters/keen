"""Administrator-only installation feature settings."""

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.errors import json_object
from app.api.routing import Router
from app.db.session import get_db
from app.security.auth import require_admin
from app.services import features

router = Router()


@router.get("/v1/admin/features")
def get_features(user=Depends(require_admin), db: Session = Depends(get_db)):
    return features.state(db).admin()


@router.put("/v1/admin/features")
async def update_features(
    request: Request, user=Depends(require_admin), db: Session = Depends(get_db)
):
    payload = await json_object(request)
    return features.update(db, user, payload.get("version"), payload.get("changes"))
