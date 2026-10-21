"""User administration enforces the administrator role in the service itself,
so a caller that reaches it without the route's check still changes nothing."""

import pytest
from app.db.models import User
from app.schemas import (
    AdminSetPasswordPayload,
    UserAccessUpdatePayload,
    UserCreatePayload,
    UserUpdatePayload,
)
from app.services import users
from app.services.context import ChangeContext
from app.services.errors import DomainError


@pytest.fixture
def people(contract_db):
    target = User(username="target", password_hash="x", role="normal", is_active=True)
    reader = User(username="reader", password_hash="x", role="normal", is_active=True)
    contract_db.add_all([target, reader])
    contract_db.commit()
    return contract_db, target, reader


def _operations(target_id):
    return {
        "list": lambda ctx, db: users.list_users(context=ctx, db=db),
        "create": lambda ctx, db: users.create_user_admin(
            payload=UserCreatePayload(username="new", password="Secret-Pass-123"),
            context=ctx,
            db=db,
        ),
        "update": lambda ctx, db: users.update_user_admin(
            user_id=target_id,
            payload=UserUpdatePayload(role="admin"),
            context=ctx,
            db=db,
        ),
        "delete": lambda ctx, db: users.delete_user_admin(
            user_id=target_id, context=ctx, db=db
        ),
        "read access": lambda ctx, db: users.get_user_access_admin(
            user_id=target_id, context=ctx, db=db
        ),
        "set access": lambda ctx, db: users.set_user_access_admin(
            user_id=target_id,
            payload=UserAccessUpdatePayload(explicit_permission_codes=["risk.manage"]),
            context=ctx,
            db=db,
        ),
        "set password": lambda ctx, db: users.set_user_password_admin(
            user_id=target_id,
            payload=AdminSetPasswordPayload(new_password="Another-Pass-456"),
            context=ctx,
            db=db,
        ),
    }


@pytest.mark.parametrize(
    "operation",
    ["list", "create", "update", "delete", "read access", "set access", "set password"],
)
@pytest.mark.parametrize("actor", ["normal user", "no user"])
def test_every_operation_requires_an_administrator(people, operation, actor):
    db, target, reader = people
    context = ChangeContext(actor=reader if actor == "normal user" else None)

    with pytest.raises(DomainError) as refused:
        _operations(target.id)[operation](context, db)

    assert refused.value.status_code == 403
    assert refused.value.detail == "Admin role required"
    db.rollback()
    db.expire_all()
    assert db.query(User).filter_by(username="new").count() == 0
    kept = db.get(User, target.id)
    assert (kept.role, kept.password_hash, kept.is_active) == ("normal", "x", True)
