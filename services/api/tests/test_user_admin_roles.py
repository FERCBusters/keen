"""User administration: effective roles as listed and created, and the
last-administrator rule for administrators who stay administrators or
who lose the role while their request waits.

Requests use real password sign-in, CSRF and the users-namespace middleware
through the assembled application. The concurrent demotion is reproduced by
calling the operation with the session's stale role, as the request would
carry it after another administrator's change committed.
"""

import uuid

import pytest
from app.db.models import Group, User, user_groups
from app.schemas import UserAccessUpdatePayload
from app.security.roles import count_effective_admins
from app.services import users
from app.services.context import ChangeContext
from app.services.errors import DomainError

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401


def _member_of(app, group):
    account = app.user(role="inherit")
    app.db.execute(user_groups.insert().values(user_id=account.id, group_id=group.id))
    app.db.commit()
    return account


def test_inherited_roles_come_only_from_groups_that_have_one(keen):  # noqa: F811
    roleless = Group(name="readers-" + uuid.uuid4().hex[:8], role=None)
    admins = Group(name="admins-" + uuid.uuid4().hex[:8], role="admin")
    keen.db.add_all([roleless, admins])
    keen.db.commit()
    in_roleless = _member_of(keen, roleless)
    in_both = _member_of(keen, roleless)
    keen.db.execute(user_groups.insert().values(user_id=in_both.id, group_id=admins.id))
    keen.db.commit()
    client = keen.client(keen.user(role="admin"))

    listed = client.get("/v1/users")

    assert listed.status_code == 200, listed.text
    roles = {row["id"]: row["effective_role"] for row in listed.json()}
    assert roles[str(in_roleless.id)] == "normal"
    assert roles[str(in_both.id)] == "admin"


@pytest.mark.parametrize("role,effective", [("admin", "admin"), (None, "normal")])
def test_a_new_account_reports_the_role_it_was_given(keen, role, effective):  # noqa: F811
    client = keen.client(keen.user(role="admin"))
    body = {"username": "new-" + uuid.uuid4().hex[:8], "password": "Synthetic-Pass-123"}
    if role:
        body["role"] = role

    created = client.post("/v1/users", json=body, headers=unsafe(client))

    assert created.status_code == 200, created.text
    assert (created.json()["role"], created.json()["effective_role"]) == (
        role or "normal",
        effective,
    )
    stored = keen.db.query(User).filter_by(username=body["username"]).one()
    assert stored.role == (role or "normal")


def test_the_only_administrator_can_change_their_account_and_stay_one(keen):  # noqa: F811
    only = keen.user(role="admin")
    assert count_effective_admins(keen.db) == 1
    client = keen.client(only)
    path = f"/v1/users/{only.id}"

    emailed = client.patch(
        path, json={"email": "admin@example.test"}, headers=unsafe(client)
    )
    kept = client.patch(path, json={"role": "admin"}, headers=unsafe(client))

    assert emailed.status_code == 200, emailed.text
    assert emailed.json()["email"] == "admin@example.test"
    assert (kept.status_code, kept.json()["effective_role"]) == (200, "admin")
    # Giving up the role would leave no administrator.
    demoted = client.patch(path, json={"role": "normal"}, headers=unsafe(client))
    assert (demoted.status_code, demoted.json()["detail"]) == (
        400,
        "Cannot remove the last effective admin user",
    )


def test_an_administrator_demoted_mid_request_cannot_delete_the_last_one(keen):  # noqa: F811
    last = keen.user(role="admin")
    demoted = keen.user(role="normal")
    # The request authenticated while its session still named the account an
    # administrator; another administrator's change has since committed.
    demoted.effective_role = "admin"
    context = ChangeContext(method="DELETE", path=f"/v1/users/{last.id}", actor=demoted)

    with pytest.raises(DomainError) as refused:
        users.delete_user_admin(user_id=last.id, context=context, db=keen.db)

    assert (refused.value.status_code, refused.value.detail) == (
        400,
        "Cannot delete the last effective admin user",
    )
    keen.db.expire_all()
    assert keen.db.get(User, last.id).is_active is True
    # With another administrator in place, the same deletion goes ahead.
    other = keen.user(role="admin")
    real = ChangeContext(method="DELETE", path=f"/v1/users/{last.id}", actor=other)
    assert users.delete_user_admin(user_id=last.id, context=real, db=keen.db) == {
        "ok": True
    }
    assert keen.db.get(User, last.id) is None


def test_access_changes_for_an_unknown_account_are_refused(keen):  # noqa: F811
    admin = keen.client(keen.user(role="admin"))
    member = keen.client(keen.user("isms.manage"))
    path = f"/v1/users/{uuid.uuid4()}/access"
    body = {"explicit_permission_codes": []}

    missing = admin.put(path, json=body, headers=unsafe(admin))
    refused = member.put(path, json=body, headers=unsafe(member))

    assert (missing.status_code, missing.json()["detail"]) == (404, "User not found")
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        "Admin role required",
    )
    with pytest.raises(DomainError) as direct:
        users.set_user_access_admin(
            user_id=uuid.uuid4(),
            payload=UserAccessUpdatePayload(explicit_permission_codes=[]),
            context=ChangeContext(actor=keen.user(role="admin")),
            db=keen.db,
        )
    assert (direct.value.status_code, direct.value.detail) == (404, "User not found")
