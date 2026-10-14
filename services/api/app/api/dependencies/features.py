"""Availability guard evaluated within the normal dependency/authentication path."""

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.security.feature_policy import requirements
from app.services.features import state


def enforce_features(request: Request, db: Session = Depends(get_db)):
    needed = requirements(
        request.scope["route"], request.path_params, request.query_params
    )
    if needed:
        state(db).require(*needed)
