from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.payloads import (
    AdminSetPasswordPayload,
    UserCreatePayload,
    UserAccessOut,
    UserAccessUpdatePayload,
    UserOut,
    UserUpdatePayload,
)
from app.db.models import Group, Permission, User, user_groups, user_permissions
from app.db.session import get_db
from app.security.auth import ROLE_ADMIN, ROLE_INHERIT, ROLE_NORMAL, create_user
from app.security.permissions import (
    get_effective_permission_codes,
    normalize_permission_codes,
)
from app.security.passwords import hash_password
from app.security.roles import (
    compute_effective_role,
    count_effective_admins,
    normalize_role,
)

router = APIRouter()


def _bump_user_authz(db: Session, user_ids) -> None:
    ids = sorted({uid for uid in (user_ids or []) if uid})
    if not ids:
        return
    db.query(User).filter(User.id.in_(ids)).update(
        {User.authz_version: User.authz_version + 1},
        synchronize_session=False,
    )


def _clean_email(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if (
        len(value) > 256
        or "@" not in value
        or value.startswith("@")
        or value.endswith("@")
    ):
        raise HTTPException(status_code=400, detail="Invalid email address")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise HTTPException(status_code=400, detail="Invalid email address")
    return value


@router.get("/v1/users", response_model=list[UserOut])
def list_users(request: Request, db: Session = Depends(get_db)) -> list[UserOut]:
    u = getattr(request.state, "user", None)
    eff = getattr(u, "effective_role", None) if isinstance(u, User) else None
    if not isinstance(u, User) or (eff or u.role or "") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")

    users = db.query(User).order_by(User.username.asc()).all()

    # Compute effective roles without an N+1 query.
    inherited_ids = [
        x.id for x in users if (x.role or "") == ROLE_INHERIT and x.is_active
    ]
    inherited_roles: dict[uuid.UUID, set[str]] = {}
    if inherited_ids:
        rows = (
            db.query(user_groups.c.user_id, Group.role)
            .join(Group, Group.id == user_groups.c.group_id)
            .filter(user_groups.c.user_id.in_(inherited_ids))
            .all()
        )
        for uid, gr in rows:
            nr = normalize_role(gr)
            if nr:
                inherited_roles.setdefault(uid, set()).add(nr)

    return [
        UserOut(
            id=x.id,
            username=x.username,
            email=x.email,
            role=x.role,
            effective_role=(
                ROLE_ADMIN
                if (x.role or "") == ROLE_ADMIN
                else (
                    ROLE_NORMAL
                    if (x.role or "") == ROLE_NORMAL
                    else (
                        ROLE_ADMIN
                        if (x.role or "") == ROLE_INHERIT
                        and ROLE_ADMIN in (inherited_roles.get(x.id) or set())
                        else ROLE_NORMAL
                    )
                )
            ),
            is_active=x.is_active,
            created_at=x.created_at,
            updated_at=x.updated_at,
            last_login_at=x.last_login_at,
        )
        for x in users
    ]


@router.post("/v1/users", response_model=UserOut)
def create_user_admin(
    payload: UserCreatePayload, request: Request, db: Session = Depends(get_db)
) -> UserOut:
    u = getattr(request.state, "user", None)
    eff = getattr(u, "effective_role", None) if isinstance(u, User) else None
    if not isinstance(u, User) or (eff or u.role or "") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")

    try:
        created = create_user(
            db,
            username=payload.username,
            password=payload.password,
            role=payload.role,
            email=_clean_email(payload.email),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    created_eff = created.role or ""
    if (created_eff or "") == ROLE_INHERIT:
        # With no group membership yet, 'inherit' defaults to normal.
        created_eff = ROLE_NORMAL

    return UserOut(
        id=created.id,
        username=created.username,
        email=created.email,
        role=created.role,
        effective_role=created_eff,
        is_active=created.is_active,
        created_at=created.created_at,
        updated_at=created.updated_at,
        last_login_at=created.last_login_at,
    )


@router.patch("/v1/users/{user_id}", response_model=UserOut)
def update_user_admin(
    user_id: uuid.UUID,
    payload: UserUpdatePayload,
    request: Request,
    db: Session = Depends(get_db),
) -> UserOut:
    u = getattr(request.state, "user", None)
    eff = getattr(u, "effective_role", None) if isinstance(u, User) else None
    if not isinstance(u, User) or (eff or u.role or "") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")

    target = db.query(User).filter(User.id == user_id).one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent self-deactivation
    if target.id == u.id and payload.is_active is False:
        raise HTTPException(
            status_code=400, detail="You cannot deactivate your own account"
        )

    # Prevent lockout: don't remove the last *effective* admin.
    current_is_admin = compute_effective_role(db, target) == ROLE_ADMIN

    if target.is_active and current_is_admin:
        will_be_active = (
            target.is_active if payload.is_active is None else bool(payload.is_active)
        )

        new_role_raw = target.role if payload.role is None else payload.role
        new_role = normalize_role(new_role_raw) or ROLE_NORMAL

        # Compute future effective role with unchanged group membership.
        old_role = target.role
        try:
            target.role = new_role
            future_is_admin = will_be_active and (
                compute_effective_role(db, target) == ROLE_ADMIN
            )
        finally:
            target.role = old_role

        if not future_is_admin:
            if count_effective_admins(db, excluding_user_id=target.id) <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot remove the last effective admin user",
                )

    if payload.role is not None:
        role = (payload.role or "").strip()
        if role not in (ROLE_ADMIN, ROLE_NORMAL, ROLE_INHERIT):
            raise HTTPException(
                status_code=400, detail="role must be 'admin', 'normal', or 'inherit'"
            )
        target.role = role

    if payload.is_active is not None:
        target.is_active = bool(payload.is_active)

    if payload.email is not None:
        target.email = _clean_email(payload.email)

    db.add(target)
    _bump_user_authz(db, [target.id])
    db.commit()
    db.refresh(target)

    return UserOut(
        id=target.id,
        username=target.username,
        email=target.email,
        role=target.role,
        effective_role=compute_effective_role(db, target),
        is_active=target.is_active,
        created_at=target.created_at,
        updated_at=target.updated_at,
        last_login_at=target.last_login_at,
    )


@router.delete("/v1/users/{user_id}")
def delete_user_admin(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    u = getattr(request.state, "user", None)
    eff = getattr(u, "effective_role", None) if isinstance(u, User) else None
    if not isinstance(u, User) or (eff or u.role or "") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")

    target = db.query(User).filter(User.id == user_id).one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == u.id:
        raise HTTPException(
            status_code=400, detail="You cannot delete your own account"
        )

    if target.is_active and compute_effective_role(db, target) == ROLE_ADMIN:
        if count_effective_admins(db, excluding_user_id=target.id) <= 0:
            raise HTTPException(
                status_code=400, detail="Cannot delete the last effective admin user"
            )

    db.execute(user_groups.delete().where(user_groups.c.user_id == target.id))
    db.execute(user_permissions.delete().where(user_permissions.c.user_id == target.id))
    db.delete(target)
    db.commit()
    return {"ok": True}


@router.post("/v1/users/{user_id}/password")
def set_user_password_admin(
    user_id: uuid.UUID,
    payload: AdminSetPasswordPayload,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    u = getattr(request.state, "user", None)
    eff = getattr(u, "effective_role", None) if isinstance(u, User) else None
    if not isinstance(u, User) or (eff or u.role or "") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")

    target = db.query(User).filter(User.id == user_id).one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        target.password_hash = hash_password(payload.new_password or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.add(target)
    _bump_user_authz(db, [target.id])
    db.commit()
    return {"ok": True}


@router.get("/v1/users/{user_id}/access", response_model=UserAccessOut)
def get_user_access_admin(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> UserAccessOut:
    u = getattr(request.state, "user", None)
    eff = getattr(u, "effective_role", None) if isinstance(u, User) else None
    if not isinstance(u, User) or (eff or u.role or "") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")

    target = db.query(User).filter(User.id == user_id).one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    group_ids = [
        gid
        for (gid,) in db.query(user_groups.c.group_id)
        .filter(user_groups.c.user_id == user_id)
        .all()
    ]
    explicit_codes = [
        code
        for (code,) in (
            db.query(Permission.code)
            .join(user_permissions, Permission.id == user_permissions.c.permission_id)
            .filter(user_permissions.c.user_id == user_id)
            .order_by(Permission.code.asc())
            .all()
        )
    ]

    eff = get_effective_permission_codes(db, target)
    effective_codes = sorted([c for c in eff if c and c != "*"])
    if "*" in eff:
        effective_codes = ["*"]

    return UserAccessOut(
        user_id=target.id,
        group_ids=group_ids,
        explicit_permission_codes=explicit_codes,
        effective_permission_codes=effective_codes,
    )


@router.put("/v1/users/{user_id}/access", response_model=UserAccessOut)
def set_user_access_admin(
    user_id: uuid.UUID,
    payload: UserAccessUpdatePayload,
    request: Request,
    db: Session = Depends(get_db),
) -> UserAccessOut:
    u = getattr(request.state, "user", None)
    eff = getattr(u, "effective_role", None) if isinstance(u, User) else None
    if not isinstance(u, User) or (eff or u.role or "") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")

    target = db.query(User).filter(User.id == user_id).one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Groups
    if payload.group_ids is not None:
        gids = sorted(set(payload.group_ids or []))
        if gids:
            found = {
                gid for (gid,) in db.query(Group.id).filter(Group.id.in_(gids)).all()
            }
            missing = [str(gid) for gid in gids if gid not in found]
            if missing:
                raise HTTPException(
                    status_code=400, detail={"missing_group_ids": missing}
                )

        db.execute(user_groups.delete().where(user_groups.c.user_id == user_id))
        if gids:
            rows = [{"user_id": user_id, "group_id": gid} for gid in gids]
            db.execute(user_groups.insert(), rows)

    # Explicit permissions
    if payload.explicit_permission_codes is not None:
        codes = normalize_permission_codes(payload.explicit_permission_codes)
        perm_ids: list[uuid.UUID] = []
        if codes:
            perms = db.query(Permission).filter(Permission.code.in_(codes)).all()
            by_code = {p.code: p for p in perms}
            missing = [c for c in codes if c not in by_code]
            if missing:
                raise HTTPException(
                    status_code=400, detail={"missing_permission_codes": missing}
                )
            perm_ids = [by_code[c].id for c in codes]

        db.execute(
            user_permissions.delete().where(user_permissions.c.user_id == user_id)
        )
        if perm_ids:
            rows = [{"user_id": user_id, "permission_id": pid} for pid in perm_ids]
            db.execute(user_permissions.insert(), rows)

    # Prevent lockout: ensure at least one effective admin remains.
    if count_effective_admins(db) <= 0:
        db.rollback()
        raise HTTPException(
            status_code=400, detail="Cannot remove the last effective admin"
        )

    _bump_user_authz(db, [user_id])
    db.commit()
    return get_user_access_admin(user_id, request, db)
