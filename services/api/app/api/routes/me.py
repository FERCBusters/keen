from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.context import change_context
from app.api.routing import Router
from app.db.session import get_db
from app.schemas import (
    ChangePasswordPayload,
    PreferencesOut,
    PreferencesUpdatePayload,
    SavedSearchCreatePayload,
    SavedSearchOut,
)
from app.services import me as service

router = Router()


# -----------------------------------------------------------------------------
# Preference helpers
# -----------------------------------------------------------------------------


# Allowed visualisation modes (Visualisation page)


# Default Visualisation page state (mode + date range + optional sunburst focus)


@router.get("/v1/me")
def me(request: Request, db: Session = Depends(get_db)) -> dict:
    return service.me(context=change_context(request), db=db)


@router.get("/v1/me/preferences", response_model=PreferencesOut)
def get_my_preferences(request: Request) -> PreferencesOut:
    return service.get_my_preferences(context=change_context(request))


@router.patch("/v1/me/preferences", response_model=PreferencesOut)
def update_my_preferences(
    payload: PreferencesUpdatePayload, request: Request, db: Session = Depends(get_db)
) -> PreferencesOut:
    return service.update_my_preferences(
        payload=payload, context=change_context(request), db=db
    )


# -----------------------------------------------------------------------------
# Saved searches
# -----------------------------------------------------------------------------


@router.get("/v1/me/saved-searches", response_model=list[SavedSearchOut])
def list_my_saved_searches(
    request: Request, db: Session = Depends(get_db)
) -> list[SavedSearchOut]:
    return service.list_my_saved_searches(context=change_context(request), db=db)


@router.post("/v1/me/saved-searches", response_model=SavedSearchOut)
def upsert_my_saved_search(
    payload: SavedSearchCreatePayload, request: Request, db: Session = Depends(get_db)
) -> SavedSearchOut:
    return service.upsert_my_saved_search(
        payload=payload, context=change_context(request), db=db
    )


@router.delete("/v1/me/saved-searches/{saved_search_id}")
def delete_my_saved_search(
    saved_search_id: str, request: Request, db: Session = Depends(get_db)
) -> dict:
    return service.delete_my_saved_search(
        saved_search_id=saved_search_id, context=change_context(request), db=db
    )


@router.post("/v1/me/password")
def change_my_password(
    payload: ChangePasswordPayload, request: Request, db: Session = Depends(get_db)
) -> dict:
    return service.change_my_password(
        payload=payload, context=change_context(request), db=db
    )
