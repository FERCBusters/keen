from __future__ import annotations

import uuid

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.context import change_context
from app.api.routing import Router
from app.db.session import get_db
from app.schemas import (
    AdminSetPasswordPayload,
    UserAccessOut,
    UserAccessUpdatePayload,
    UserCreatePayload,
    UserOut,
    UserUpdatePayload,
)
from app.services import users as service

router = Router()


@router.get("/v1/users", response_model=list[UserOut])
def list_users(request: Request, db: Session = Depends(get_db)) -> list[UserOut]:
    return service.list_users(context=change_context(request), db=db)


@router.post("/v1/users", response_model=UserOut)
def create_user_admin(
    payload: UserCreatePayload, request: Request, db: Session = Depends(get_db)
) -> UserOut:
    return service.create_user_admin(
        payload=payload, context=change_context(request), db=db
    )


@router.patch("/v1/users/{user_id}", response_model=UserOut)
def update_user_admin(
    user_id: uuid.UUID,
    payload: UserUpdatePayload,
    request: Request,
    db: Session = Depends(get_db),
) -> UserOut:
    return service.update_user_admin(
        user_id=user_id, payload=payload, context=change_context(request), db=db
    )


@router.delete("/v1/users/{user_id}")
def delete_user_admin(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    return service.delete_user_admin(
        user_id=user_id, context=change_context(request), db=db
    )


@router.post("/v1/users/{user_id}/password")
def set_user_password_admin(
    user_id: uuid.UUID,
    payload: AdminSetPasswordPayload,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    return service.set_user_password_admin(
        user_id=user_id, payload=payload, context=change_context(request), db=db
    )


@router.get("/v1/users/{user_id}/access", response_model=UserAccessOut)
def get_user_access_admin(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> UserAccessOut:
    return service.get_user_access_admin(
        user_id=user_id, context=change_context(request), db=db
    )


@router.put("/v1/users/{user_id}/access", response_model=UserAccessOut)
def set_user_access_admin(
    user_id: uuid.UUID,
    payload: UserAccessUpdatePayload,
    request: Request,
    db: Session = Depends(get_db),
) -> UserAccessOut:
    return service.set_user_access_admin(
        user_id=user_id, payload=payload, context=change_context(request), db=db
    )
