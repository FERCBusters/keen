"""Account self-service: saved default views that name only part of a view,
the account summary outside the request middleware, and the per-account
password-change limit, which counts only attempts the address limit allowed.

Requests use real password sign-in, CSRF and session storage through the
assembled application; the account summary is also called directly, as a
caller without the middleware's role resolution would.
"""

import uuid

import pytest
from app.core.config import settings
from app.db.models import Group, user_groups
from app.services.context import ChangeContext
from app.services.me import me as account_summary

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401


@pytest.mark.parametrize(
    "view,saved",
    [
        ({"mode": "heatmap"}, {"mode": "heatmap"}),
        ({"mode": "graph", "date_range": {}}, {"mode": "graph"}),
        (
            {"date_range": {"days": " ", "start_date": "", "end_date": ""}},
            {"mode": "graph"},
        ),
        (
            {"date_range": {"from": "2026-01-01", "to": "2026-03-01"}},
            {
                "mode": "graph",
                "date_range": {"start_date": "2026-01-01", "end_date": "2026-03-01"},
            },
        ),
        (
            {
                "mode": "heatmap",
                "sunburst_focus": {"source_key": " ", "control_id": ""},
            },
            {"mode": "heatmap"},
        ),
    ],
    ids=["mode-only", "empty-range", "blank-range", "ordered-range", "empty-focus"],
)
def test_a_default_view_keeps_only_the_parts_it_names(keen, view, saved):  # noqa: F811
    client = keen.client(keen.user())

    response = client.patch(
        "/v1/me/preferences",
        json={"default_visualisation": view},
        headers=unsafe(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["default_visualisation"] == saved
    me = client.get("/v1/me").json()
    assert me["preferences"]["default_visualisation"] == saved


def test_the_summary_works_out_the_role_when_no_session_supplied_it(keen):  # noqa: F811
    group = Group(name="admins-" + uuid.uuid4().hex[:8], role="admin")
    keen.db.add(group)
    keen.db.flush()
    inherited, plain = keen.user(role="inherit"), keen.user(role="inherit")
    keen.db.execute(
        user_groups.insert().values(user_id=inherited.id, group_id=group.id)
    )
    keen.db.commit()

    for account, role in ((inherited, "admin"), (plain, "normal")):
        keen.db.refresh(account)
        assert account.effective_role is None
        summary = account_summary(context=ChangeContext(actor=account), db=keen.db)
        assert (summary["role"], summary["effective_role"]) == ("inherit", role)
        assert summary["is_admin"] is (role == "admin")
        assert summary["can_manage_risks"] is (role == "admin")


def test_attempts_refused_by_the_address_limit_spend_no_account_allowance(
    keen,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    monkeypatch.setattr(settings, "login_rate_limit_per_ip", 2)
    monkeypatch.setattr(settings, "password_change_rate_limit_per_account", 5)
    user = keen.user()
    client = keen.client(user)
    body = {"current_password": "wrong", "new_password": "another-synthetic-passphrase"}

    codes = [
        client.post("/v1/me/password", headers=unsafe(client), json=body).status_code
        for _ in range(4)
    ]

    assert codes == [400, 400, 429, 429]
    # Only the attempts the address allowed count against the account, so a
    # busy address cannot use up the account's own allowance.
    assert keen.store.get(f"keen:rl:password_change:user:{user.id}") == "2"
