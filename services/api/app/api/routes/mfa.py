from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.context import change_context
from app.api.routing import Router
from app.db.session import get_db
from app.security import mfa
from app.security.auth import get_current_user_from_request, require_authenticated
from app.security.passwords import verify_password
from app.services import mfa_management as service
from app.services.mfa_management import Code, Credential, Password, Remove

router = Router()


def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/v1/me/mfa")
def status(
    request: Request, db: Session = Depends(get_db), user=Depends(require_authenticated)
):
    return service.status(context=change_context(request), db=db, user=user)


@router.post("/v1/auth/mfa/manage/start")
def start_management(
    payload: Password, request: Request, db: Session = Depends(get_db)
):
    mfa.check_origin(request)
    user = get_current_user_from_request(request, db)
    if not user:
        raise HTTPException(401, "Sign in before managing local account security")
    mfa.limit(request, user.id)
    if user.auth_backend == "ldap":
        from app.security.ldap import verify_ldap_user

        valid = verify_ldap_user(db, user, payload.password)
    else:
        valid = verify_password(payload.password, user.password_hash)
    if not valid:
        raise HTTPException(401, "Invalid password")
    return mfa.start(db, user, request, "manage")


@router.post("/v1/auth/mfa/state")
def state(request: Request, db: Session = Depends(get_db)):
    row, user = mfa.pending(db, request)
    return service.state(context=change_context(request), db=db, row=row, user=user)


@router.post("/v1/auth/mfa/verify-code")
def verify_code(payload: Code, request: Request, db: Session = Depends(get_db)):
    row, user = mfa.pending(db, request)
    mfa.limit(request, user.id)
    return service.verify_code(
        payload=payload, context=change_context(request), db=db, row=row, user=user
    )


@router.post("/v1/auth/mfa/totp/start")
def start_totp(request: Request, db: Session = Depends(get_db)):
    row, user = mfa.pending(db, request)
    mfa.limit(request, user.id)
    return service.start_totp(
        context=change_context(request), db=db, row=row, user=user
    )


@router.post("/v1/auth/mfa/totp/confirm")
def confirm_totp(payload: Code, request: Request, db: Session = Depends(get_db)):
    row, user = mfa.pending(db, request)
    mfa.limit(request, user.id)
    return service.confirm_totp(
        payload=payload, context=change_context(request), db=db, row=row, user=user
    )


@router.post("/v1/auth/mfa/webauthn/options")
def authentication_options(request: Request, db: Session = Depends(get_db)):
    row, user = mfa.pending(db, request)
    mfa.limit(request, user.id)
    return service.authentication_options(
        context=change_context(request), db=db, row=row, user=user
    )


@router.post("/v1/auth/mfa/webauthn/verify")
def authentication_verify(
    payload: Credential, request: Request, db: Session = Depends(get_db)
):
    row, user = mfa.pending(db, request)
    mfa.limit(request, user.id)
    return service.authentication_verify(
        payload=payload, context=change_context(request), db=db, row=row, user=user
    )


@router.post("/v1/auth/mfa/webauthn/register-options")
def registration_options(request: Request, db: Session = Depends(get_db)):
    row, user = mfa.pending(db, request)
    mfa.limit(request, user.id)
    return service.registration_options(
        context=change_context(request), db=db, row=row, user=user
    )


@router.post("/v1/auth/mfa/webauthn/register")
def registration_verify(
    payload: Credential, request: Request, db: Session = Depends(get_db)
):
    row, user = mfa.pending(db, request)
    mfa.limit(request, user.id)
    return service.registration_verify(
        payload=payload, context=change_context(request), db=db, row=row, user=user
    )


@router.post("/v1/auth/mfa/remove")
def remove(payload: Remove, request: Request, db: Session = Depends(get_db)):
    row, user = mfa.pending(db, request)
    return service.remove(
        payload=payload, context=change_context(request), db=db, row=row, user=user
    )


@router.post("/v1/auth/mfa/recovery/regenerate")
def regenerate(request: Request, db: Session = Depends(get_db)):
    row, user = mfa.pending(db, request)
    return service.regenerate(
        context=change_context(request), db=db, row=row, user=user
    )


@router.post("/v1/auth/mfa/finish")
def finish(request: Request, db: Session = Depends(get_db)):
    row, user = mfa.pending(db, request)
    verified_version, enabled = service.consume_challenge(db=db, row=row, user=user)
    from .auth import complete_local_login

    response = complete_local_login(
        db, user, request, mfa_verified=enabled, verified_version=verified_version
    )
    response.delete_cookie(mfa.COOKIE, path="/")
    return no_cache(response)


@router.post("/v1/auth/mfa/cancel")
def cancel(request: Request, db: Session = Depends(get_db)):
    from fastapi.responses import JSONResponse

    mfa.check_origin(request)
    return mfa.cancel(db, request, JSONResponse({"ok": True}))
