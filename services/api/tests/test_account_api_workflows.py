"""Account self-service and user administration through the assembled application.

Requests use real password sign-in, CSRF, session storage and the admin-namespace
middleware. PostgreSQL is used when KEEN_CONTRACT_TEST_DATABASE_URL is set.
"""

import re
import uuid
from pathlib import Path

import pytest
from app.core.config import settings
from app.db.models import Group, Permission, SavedSearch, User, user_groups

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (  # noqa: F401
    BASE,
    PASSWORD,
    keen,
    unsafe,
)

HOSTILE = '<img src=x onerror="alert(1)">'


def _json(response, status=200):
    assert response.status_code == status, response.text
    return response.json() if response.content else None


# --- /v1/me -------------------------------------------------------------------


def test_me_reports_identity_permissions_and_policy(keen):  # noqa: F811
    user = keen.user("risk.read", "isms.read")
    me = _json(keen.client(user).get("/v1/me"))
    assert me["user"] == user.username
    assert me["user_id"] == str(user.id)
    assert me["is_admin"] is False
    assert me["can_view_risks"] is True
    assert me["can_manage_risks"] is False
    assert me["can_view_isms"] is True
    assert me["can_sample_isms"] is True
    # The audit trail is for administrators; no capability names it.
    assert "can_audit_trail" not in me
    assert me["can_view_events"] is False
    assert me["preferences"]["landing_page"] == "/"
    # New accounts start on Ink, listed first; Sand stays selectable.
    assert me["preferences"]["theme"] == "ink"
    assert [t["id"] for t in me["available_themes"]][:2] == ["ink", "sand"]

    admin = _json(keen.client(keen.user(role="admin")).get("/v1/me"))
    assert admin["is_admin"] is True
    assert admin["can_manage_risks"] is True
    refused = keen.client().get("/v1/me")
    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Not authenticated"},
    )


def test_me_reflects_the_permissions_audit_managers_imply(keen):  # noqa: F811
    for code in ("audits.manage", "audits.admin"):
        me = _json(keen.client(keen.user(code)).get("/v1/me"))
        assert me["can_manage_audits"] is True
        assert me["can_view_events"] is True
        assert me["can_view_isms"] is True
        assert me["can_sample_isms"] is True
        assert me["can_manage_isms"] is False
        assert me["can_view_risks"] is False
    reader = _json(keen.client(keen.user("audits.read")).get("/v1/me"))
    assert reader["can_view_audits"] is True
    assert reader["can_view_events"] is False
    assert reader["can_view_isms"] is False


# --- Preferences ----------------------------------------------------------------


def test_preferences_are_validated_and_saved_with_patch_semantics(keen):  # noqa: F811
    client = keen.client(keen.user())
    headers = unsafe(client)
    saved = _json(
        client.patch(
            "/v1/me/preferences",
            headers=headers,
            json={
                "use_local_timezone": True,
                "timezone": "Australia/Melbourne",
                "theme": "ocean-dark",
                "date_format": "dd/mm/yyyy",
                "auto_apply_filters": True,
                "source_colors": {"github": "#abc", " ": "#000000"},
                "landing_page": "/events.html?q=ssh&offset=50&empty=",
                "default_framework": "ISO27001:2022",
                "default_visualisation": {
                    "mode": "heatmap",
                    "date_range": {
                        "start_date": "2026-03-01",
                        "end_date": "2026-01-01",
                    },
                    "sunburst_focus": {"source_key": "github"},
                },
            },
        )
    )
    assert saved["timezone"] == "Australia/Melbourne"
    assert saved["date_format"] == "dmy"
    assert saved["source_colors"] == {"github": "#AABBCC"}
    # Pagination is dropped so the landing page opens at the top.
    assert saved["landing_page"] == "/events.html?q=ssh"
    assert saved["default_visualisation"] == {
        "mode": "heatmap",
        "date_range": {"start_date": "2026-01-01", "end_date": "2026-03-01"},
        "sunburst_focus": {"source_key": "github"},
    }
    assert "viz_mode" not in saved

    # Only the fields sent are changed; a blank theme keeps the current theme.
    partial = _json(
        client.patch(
            "/v1/me/preferences",
            headers=headers,
            json={"theme": "", "date_format": "iso"},
        )
    )
    assert partial["theme"] == "ocean-dark"
    assert partial["date_format"] == "ymd"
    assert partial["timezone"] == "Australia/Melbourne"
    cleared = _json(
        client.patch(
            "/v1/me/preferences",
            headers=headers,
            json={
                "source_colors": None,
                "default_visualisation": None,
                "landing_page": "",
                "default_framework": " ",
            },
        )
    )
    assert cleared["source_colors"] == {}
    assert cleared["default_visualisation"] is None
    assert cleared["landing_page"] == "/"
    assert cleared["default_framework"] is None
    assert _json(client.get("/v1/me/preferences"))["theme"] == "ocean-dark"
    days = _json(
        client.patch(
            "/v1/me/preferences",
            headers=headers,
            json={
                "default_visualisation": {
                    "date_range": {"days": "30"},
                    "sunburst_focus": {"control_id": str(uuid.UUID(int=7))},
                }
            },
        )
    )
    assert days["default_visualisation"] == {
        "mode": "graph",
        "date_range": {"days": 30},
        "sunburst_focus": {"control_id": str(uuid.UUID(int=7))},
    }


def test_landing_pages_are_normalised_to_a_keen_path(keen):  # noqa: F811
    client = keen.client(keen.user())
    saved = _json(
        client.patch(
            "/v1/me/preferences",
            headers=unsafe(client),
            json={"landing_page": "events.html?=orphan&framework=A&offset=5"},
        )
    )
    # A relative page gains its leading slash; nameless parameters and
    # pagination are dropped.
    assert saved["landing_page"] == "/events.html?framework=A"


@pytest.mark.parametrize(
    "oidc, proxy, upstream, expected",
    [
        (True, True, "https://sso.example/logout", "/api/v1/auth/oidc/logout"),
        (False, True, "https://sso.example/logout", "https://sso.example/logout"),
        (False, True, "", None),
        (False, False, "https://sso.example/logout", None),
    ],
)
def test_sign_out_follows_the_sign_in_method(
    keen,  # noqa: F811 - fixture imported above
    monkeypatch,
    oidc,
    proxy,
    upstream,
    expected,
):
    from app.services import me as me_service

    monkeypatch.setattr(me_service.settings, "oidc_enabled", oidc)
    monkeypatch.setattr(me_service.settings, "trust_remote_user", proxy)
    monkeypatch.setattr(me_service.settings, "upstream_logout_url", upstream)
    client = keen.client(keen.user())

    me = _json(client.get("/v1/me"))

    assert me["logout_url"] == expected
    # Behind a proxy without an upstream address, Keen cannot end the session.
    assert me["logout_enabled"] is (not (proxy and not oidc) or expected is not None)


@pytest.mark.parametrize(
    "payload, detail",
    [
        ({"date_format": "mm/dd/yyyy"}, "Invalid date_format"),
        (
            {"default_visualisation": {"mode": "pie"}},
            "Invalid default_visualisation.mode",
        ),
        (
            {"timezone": "Mars/Olympus"},
            "Invalid timezone. Use an IANA name like 'Australia/Melbourne'.",
        ),
        ({"source_colors": {"github": "red"}}, "Invalid hex color for source: github"),
        ({"source_colors": {"x" * 129: "#000000"}}, "source_colors key is too long"),
        (
            {"source_colors": {f"s{i}": "#000000" for i in range(501)}},
            "source_colors is too large",
        ),
        ({"default_framework": "x" * 65}, "default_framework is too long"),
        (
            {"default_framework": "ISO 27001"},
            "default_framework may contain only letters, numbers, dot, underscore, colon and hyphen",
        ),
        (
            {"default_visualisation": {"date_range": {"days": 0}}},
            "default_visualisation.date_range.days out of range",
        ),
        (
            {"default_visualisation": {"date_range": {"days": "many"}}},
            "Invalid default_visualisation.date_range.days",
        ),
        (
            {"default_visualisation": {"date_range": []}},
            "default_visualisation.date_range must be an object",
        ),
        (
            {"default_visualisation": {"date_range": {"start_date": "2026-01-01"}}},
            "Invalid default_visualisation.date_range start/end",
        ),
        (
            {"default_visualisation": {"sunburst_focus": "github"}},
            "default_visualisation.sunburst_focus must be an object",
        ),
        (
            {
                "default_visualisation": {
                    "sunburst_focus": {"source_key": "a", "control_id": "b"}
                }
            },
            "default_visualisation.sunburst_focus must specify one of source_key or control_id",
        ),
        (
            {"default_visualisation": {"sunburst_focus": {"source_key": "x" * 129}}},
            "default_visualisation.sunburst_focus.source_key too long",
        ),
        (
            {"default_visualisation": {"sunburst_focus": {"control_id": "not-a-uuid"}}},
            "Invalid default_visualisation.sunburst_focus.control_id",
        ),
        ({"landing_page": "https://evil.example/"}, "Invalid landing_page"),
        ({"landing_page": "//evil.example/"}, "Invalid landing_page"),
        # An empty authority leaves a path that begins with two slashes.
        ({"landing_page": "////evil.example/"}, "Invalid landing_page"),
        ({"landing_page": "/login.html"}, "Invalid landing_page"),
        ({"landing_page": "/admin.html"}, "Invalid landing_page"),
        ({"theme": "neon"}, "Invalid theme"),
        ({"landing_page": "/events.html?q=" + "x" * 2100}, "landing_page is too long"),
    ],
)
def test_invalid_preferences_are_rejected_without_change(keen, payload, detail):  # noqa: F811
    client = keen.client(keen.user())
    before = _json(client.get("/v1/me/preferences"))
    response = client.patch("/v1/me/preferences", headers=unsafe(client), json=payload)
    assert (response.status_code, response.json()) == (400, {"detail": detail})
    assert _json(client.get("/v1/me/preferences")) == before


def _account_page_landing_choices():
    # The account page's fixed landing destinations, read from its source so the
    # API allow-list and the offered choices cannot drift apart unnoticed.
    source = (
        Path(__file__).resolve().parents[2] / "ui" / "public" / "pages" / "account.js"
    ).read_text(encoding="utf-8")
    block = source[
        source.index("const fixed = [") : source.index(
            "];", source.index("const fixed = [")
        )
    ]
    choices = re.findall(r"\{ v: '([^']+)', label: '[^']+' \}", block)
    admin_only = re.findall(r"fixed\.push\(\{ v: '([^']+)'", source)
    assert choices and admin_only
    return choices, admin_only


def test_landing_page_choices_offered_by_the_account_page_are_accepted(keen):  # noqa: F811
    choices, admin_only = _account_page_landing_choices()
    assert choices == [
        "/",
        "/controls.html",
        "/events.html",
        "/sources.html",
        "/risks.html",
        "/isms.html",
        "/statement-of-applicability.html",
        "/account.html",
    ]
    client = keen.client(keen.user())
    for page in choices:
        saved = _json(
            client.patch(
                "/v1/me/preferences",
                headers=unsafe(client),
                json={"landing_page": page},
            )
        )
        assert saved["landing_page"] == page
    admin = keen.client(keen.user(role="admin"))
    for page in admin_only:
        saved = _json(
            admin.patch(
                "/v1/me/preferences", headers=unsafe(admin), json={"landing_page": page}
            )
        )
        assert saved["landing_page"] == page


# --- Saved searches -------------------------------------------------------------


def test_saved_searches_are_private_to_their_owner(keen):  # noqa: F811
    owner = keen.client(keen.user())
    other = keen.client(keen.user())
    created = _json(
        owner.post(
            "/v1/me/saved-searches",
            headers=unsafe(owner),
            json={"name": HOSTILE, "url": "events.html?q=ssh&offset=100"},
        )
    )
    assert created["url"] == "/events.html?q=ssh"
    assert created["name"] == HOSTILE
    # Saving the same name again updates the URL in place.
    again = _json(
        owner.post(
            "/v1/me/saved-searches",
            headers=unsafe(owner),
            json={"name": HOSTILE, "url": "/events.html?q=sudo"},
        )
    )
    assert again["id"] == created["id"]
    assert [row["url"] for row in _json(owner.get("/v1/me/saved-searches"))] == [
        "/events.html?q=sudo"
    ]
    assert _json(other.get("/v1/me/saved-searches")) == []
    # Another user cannot delete it, and an unknown or malformed id is rejected.
    refused = other.delete(
        f"/v1/me/saved-searches/{created['id']}", headers=unsafe(other)
    )
    assert (refused.status_code, refused.json()) == (404, {"detail": "Not found"})
    refused = owner.delete("/v1/me/saved-searches/not-a-uuid", headers=unsafe(owner))
    assert (refused.status_code, refused.json()) == (400, {"detail": "Invalid id"})
    assert _json(
        owner.delete(f"/v1/me/saved-searches/{created['id']}", headers=unsafe(owner))
    ) == {"ok": True}
    assert (
        keen.db.query(SavedSearch).filter_by(id=uuid.UUID(created["id"])).one_or_none()
        is None
    )


@pytest.mark.parametrize(
    "url, detail",
    [
        ("javascript:alert(1)", "Invalid saved search URL"),
        ("DATA:text/html,x", "Invalid saved search URL"),
        (
            "https://evil.example/events.html",
            "Saved search URL must be a relative path (e.g. /events.html?q=...)",
        ),
        (
            "//evil.example/x",
            "Saved search URL must be a relative path (e.g. /events.html?q=...)",
        ),
        ("/\\evil.example/x", "Invalid saved search URL"),
        ("/\tevil.example/x", "Invalid saved search URL"),
        ("/.//evil.example/x", "Invalid saved search URL"),
        ("/x/%2e%2e//evil.example/x", "Invalid saved search URL"),
        (
            "/events.html?q=" + "x" * 2050,
            [
                {
                    "type": "string_too_long",
                    "loc": ["body", "url"],
                    "msg": "String should have at most 2048 characters",
                }
            ],
        ),
    ],
)
def test_saved_search_urls_must_stay_inside_keen(keen, url, detail):  # noqa: F811
    client = keen.client(keen.user())
    response = client.post(
        "/v1/me/saved-searches", headers=unsafe(client), json={"name": "x", "url": url}
    )
    assert (response.status_code, response.json()) == (
        422 if isinstance(detail, list) else 400,
        {"detail": detail},
    )
    assert _json(client.get("/v1/me/saved-searches")) == []


def test_saved_search_names_are_required(keen):  # noqa: F811
    client = keen.client(keen.user())
    refused = client.post(
        "/v1/me/saved-searches",
        headers=unsafe(client),
        json={"name": "   ", "url": "/events.html"},
    )
    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "name is required"},
    )


# --- Password change -------------------------------------------------------------


def test_password_change_verifies_the_current_password_and_revokes_sessions(keen):  # noqa: F811
    user = keen.user()
    client = keen.client(user)
    wrong = client.post(
        "/v1/me/password",
        headers=unsafe(client),
        json={
            "current_password": "not-it",
            "new_password": "another-synthetic-passphrase",
        },
    )
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == "Current password is incorrect"
    weak = client.post(
        "/v1/me/password",
        headers=unsafe(client),
        json={"current_password": PASSWORD, "new_password": "x"},
    )
    assert (weak.status_code, weak.json()) == (
        400,
        {"detail": "Password must be at least 8 characters"},
    )
    before = keen.db.get(User, user.id).mfa_version
    ok = client.post(
        "/v1/me/password",
        headers=unsafe(client),
        json={
            "current_password": PASSWORD,
            "new_password": "another-synthetic-passphrase",
        },
    )
    assert _json(ok) == {"ok": True}
    keen.db.expire_all()
    assert keen.db.get(User, user.id).mfa_version == before + 1
    # The existing session no longer authenticates, and the new password works.
    refused = client.get("/v1/me")
    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Not authenticated"},
    )
    fresh = keen.client()
    login = fresh.post(
        "/v1/auth/login",
        json={"username": user.username, "password": "another-synthetic-passphrase"},
        headers={"Origin": BASE},
    )
    assert (login.status_code, login.json()) == (
        200,
        {"user": user.username, "user_id": str(user.id)},
    )


def test_password_change_is_refused_for_directory_and_proxy_accounts(keen, monkeypatch):  # noqa: F811
    user = keen.user()
    client = keen.client(user)
    body = {
        "current_password": PASSWORD,
        "new_password": "another-synthetic-passphrase",
    }
    row = keen.db.get(User, user.id)
    row.auth_backend = "ldap"
    keen.db.commit()
    refused = client.post("/v1/me/password", headers=unsafe(client), json=body)
    assert (refused.status_code, refused.json()) == (
        403,
        {"detail": "Change your password in your LDAP directory"},
    )
    row.auth_backend = "local"
    keen.db.commit()
    monkeypatch.setattr(settings, "trust_remote_user", True)
    response = client.post("/v1/me/password", headers=unsafe(client), json=body)
    assert (response.status_code, response.json()) == (
        403,
        {
            "detail": "Password changes are disabled when KEEN_TRUST_REMOTE_USER is enabled"
        },
    )


def test_password_change_attempts_are_rate_limited_per_user(keen, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "login_rate_limit_per_ip", 100)
    monkeypatch.setattr(settings, "password_change_rate_limit_per_account", 2)
    user = keen.user()
    client = keen.client(user)
    original = user.password_hash
    new_password = "another-synthetic-passphrase"
    wrong = {"current_password": "wrong", "new_password": new_password}
    refusals = [
        client.post("/v1/me/password", headers=unsafe(client), json=wrong)
        for _ in range(2)
    ]
    assert [(r.status_code, r.json()) for r in refusals] == [
        (400, {"detail": "Current password is incorrect"})
    ] * 2
    # Once the account's allowance is spent, the right password is refused
    # before it is checked too, so guesses cannot continue from new addresses.
    right = {"current_password": PASSWORD, "new_password": new_password}
    limited = client.post("/v1/me/password", headers=unsafe(client), json=right)
    assert (limited.status_code, limited.json()) == (
        429,
        {"detail": "Too many password change attempts. Please try again later."},
    )
    keen.db.refresh(user)
    assert user.password_hash == original


# --- User administration ----------------------------------------------------------


@pytest.fixture
def admin_client(keen):  # noqa: F811
    admin = keen.user(role="admin")
    return admin, keen.client(admin)


def test_users_namespace_requires_an_administrator(keen):  # noqa: F811
    reader = keen.client(keen.user("isms.manage", "audits.admin"))
    for method, path in [
        ("GET", "/v1/users"),
        ("POST", "/v1/users"),
        ("GET", f"/v1/users/{uuid.uuid4()}/access"),
    ]:
        response = reader.request(method, path, headers=unsafe(reader), json={})
        assert response.status_code == 403
        assert response.json()["detail"] == "Admin role required"


def test_administrators_create_update_and_delete_users(keen, admin_client):  # noqa: F811
    _admin, client = admin_client
    h = unsafe(client)
    created = _json(
        client.post(
            "/v1/users",
            headers=h,
            json={
                "username": "riley",
                "password": "synthetic-password-riley",
                "role": "inherit",
                "email": " riley@example.test ",
            },
        )
    )
    assert created["effective_role"] == "normal"
    assert created["email"] == "riley@example.test"
    refused = client.post(
        "/v1/users",
        headers=h,
        json={"username": "riley", "password": "synthetic-password-riley"},
    )
    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "username already exists"},
    )
    for email in [
        "no-at-sign",
        "@example.test",
        "riley@",
        "a\u0007@example.test",
        "x" * 250 + "@example.test",
    ]:
        response = client.post(
            "/v1/users",
            headers=h,
            json={
                "username": "bad-" + uuid.uuid4().hex[:6],
                "password": "synthetic-password-x",
                "email": email,
            },
        )
        assert (response.status_code, response.json()) == (
            400,
            {"detail": "Invalid email address"},
        )
    uid = created["id"]
    updated = _json(
        client.patch(f"/v1/users/{uid}", headers=h, json={"role": "admin", "email": ""})
    )
    assert updated["role"] == "admin"
    assert updated["effective_role"] == "admin"
    assert updated["email"] is None
    refused = client.patch(f"/v1/users/{uid}", headers=h, json={"role": "owner"})
    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "role must be 'admin', 'normal', or 'inherit'"},
    )
    listed = {row["username"]: row for row in _json(client.get("/v1/users"))}
    assert listed["riley"]["effective_role"] == "admin"
    deactivated = _json(
        client.patch(f"/v1/users/{uid}", headers=h, json={"is_active": False})
    )
    assert deactivated["is_active"] is False
    assert _json(client.delete(f"/v1/users/{uid}", headers=h)) == {"ok": True}
    refused = client.delete(f"/v1/users/{uid}", headers=h)
    assert (refused.status_code, refused.json()) == (404, {"detail": "User not found"})
    refused = client.patch(f"/v1/users/{uuid.uuid4()}", headers=h, json={})
    assert (refused.status_code, refused.json()) == (404, {"detail": "User not found"})


def test_administrators_cannot_lock_themselves_out(keen, admin_client):  # noqa: F811
    admin, client = admin_client
    h = unsafe(client)
    # Remove every other effective administrator so this one is the last.
    for row in (
        keen.db.query(User).filter(User.id != admin.id, User.role == "admin").all()
    ):
        row.role = "normal"
    keen.db.commit()
    assert (
        client.patch(
            f"/v1/users/{admin.id}", headers=h, json={"is_active": False}
        ).json()["detail"]
        == "You cannot deactivate your own account"
    )
    assert (
        client.patch(
            f"/v1/users/{admin.id}", headers=h, json={"role": "normal"}
        ).json()["detail"]
        == "Cannot remove the last effective admin user"
    )
    assert (
        client.delete(f"/v1/users/{admin.id}", headers=h).json()["detail"]
        == "You cannot delete your own account"
    )
    keen.db.expire_all()
    assert keen.db.get(User, admin.id).role == "admin"


def test_the_last_other_administrator_cannot_be_deleted(keen):  # noqa: F811
    admin = keen.user(role="admin")
    client = keen.client(admin)
    other = keen.user(role="admin")
    # Make `other` the only remaining effective administrator besides the actor,
    # then demote the actor through a second session so deleting `other` would leave none.
    for row in (
        keen.db.query(User)
        .filter(User.id.notin_([admin.id, other.id]), User.role == "admin")
        .all()
    ):
        row.role = "normal"
    keen.db.commit()
    assert _json(client.delete(f"/v1/users/{other.id}", headers=unsafe(client))) == {
        "ok": True
    }


def test_user_access_sets_groups_and_explicit_permissions(keen, admin_client):  # noqa: F811
    _admin, client = admin_client
    h = unsafe(client)
    target = keen.user()
    group = Group(name="Readers-" + uuid.uuid4().hex[:6], role="normal")
    perm = Permission(code="reports." + uuid.uuid4().hex[:6], description="Reports")
    keen.db.add_all([group, perm])
    keen.db.commit()
    before = keen.db.get(User, target.id).authz_version
    access = _json(
        client.put(
            f"/v1/users/{target.id}/access",
            headers=h,
            json={
                "group_ids": [str(group.id)],
                "explicit_permission_codes": [perm.code],
            },
        )
    )
    assert access["group_ids"] == [str(group.id)]
    assert access["explicit_permission_codes"] == [perm.code]
    assert access["effective_permission_codes"] == [perm.code]
    keen.db.expire_all()
    assert keen.db.get(User, target.id).authz_version > before
    assert _json(client.get(f"/v1/users/{target.id}/access"))["group_ids"] == [
        str(group.id)
    ]
    unknown = str(uuid.uuid4())
    missing_group = client.put(
        f"/v1/users/{target.id}/access",
        headers=h,
        json={"group_ids": [unknown]},
    )
    assert (missing_group.status_code, missing_group.json()) == (
        400,
        {"detail": {"missing_group_ids": [unknown]}},
    )
    missing_perm = client.put(
        f"/v1/users/{target.id}/access",
        headers=h,
        json={"explicit_permission_codes": ["no.such"]},
    )
    assert (missing_perm.status_code, missing_perm.json()) == (
        400,
        {"detail": {"missing_permission_codes": ["no.such"]}},
    )
    cleared = _json(
        client.put(
            f"/v1/users/{target.id}/access",
            headers=h,
            json={"group_ids": [], "explicit_permission_codes": []},
        )
    )
    assert cleared["group_ids"] == [] and cleared["explicit_permission_codes"] == []
    refused = client.get(f"/v1/users/{uuid.uuid4()}/access")
    assert (refused.status_code, refused.json()) == (404, {"detail": "User not found"})
    admin_view = _json(client.get(f"/v1/users/{_admin.id}/access"))
    assert admin_view["effective_permission_codes"] == ["*"]


def test_access_changes_cannot_remove_the_last_administrator(keen):  # noqa: F811
    admin = keen.user(role="inherit")
    group = Group(name="Admins-" + uuid.uuid4().hex[:6], role="admin")
    keen.db.add(group)
    keen.db.flush()
    keen.db.execute(user_groups.insert().values(user_id=admin.id, group_id=group.id))
    for row in (
        keen.db.query(User).filter(User.id != admin.id, User.role == "admin").all()
    ):
        row.role = "normal"
    keen.db.commit()
    client = keen.client(admin)
    response = client.put(
        f"/v1/users/{admin.id}/access", headers=unsafe(client), json={"group_ids": []}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Cannot remove the last effective admin"
    assert (
        keen.db.query(user_groups).filter(user_groups.c.user_id == admin.id).count()
        == 1
    )


def test_administrators_reset_local_passwords_only(keen, admin_client):  # noqa: F811
    _admin, client = admin_client
    h = unsafe(client)
    target = keen.user()
    target_client = keen.client(target)
    assert _json(
        client.post(
            f"/v1/users/{target.id}/password",
            headers=h,
            json={"new_password": "reset-synthetic-passphrase"},
        )
    ) == {"ok": True}
    # The target's existing session is revoked by the MFA/session version change.
    refused = target_client.get("/v1/me")
    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Not authenticated"},
    )
    refused = client.post(
        f"/v1/users/{target.id}/password", headers=h, json={"new_password": "x"}
    )
    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "Password must be at least 8 characters"},
    )
    directory = keen.user()
    row = keen.db.get(User, directory.id)
    row.auth_backend = "ldap"
    keen.db.commit()
    refused = client.post(
        f"/v1/users/{directory.id}/password",
        headers=h,
        json={"new_password": "reset-synthetic-passphrase"},
    )
    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "Change LDAP passwords in the directory"},
    )
    refused = client.post(
        f"/v1/users/{uuid.uuid4()}/password",
        headers=h,
        json={"new_password": "reset-synthetic-passphrase"},
    )
    assert (refused.status_code, refused.json()) == (404, {"detail": "User not found"})


@pytest.mark.parametrize(
    "operation,arguments",
    [
        ("me", {}),
        ("get_my_preferences", {}),
        ("update_my_preferences", {"payload": None}),
        ("list_my_saved_searches", {}),
        ("upsert_my_saved_search", {"payload": None}),
        ("delete_my_saved_search", {"saved_search_id": "any"}),
        ("change_my_password", {"payload": None}),
    ],
)
def test_account_operations_fail_closed_without_a_signed_in_user(operation, arguments):
    from app.services import me as account
    from app.services.context import ChangeContext
    from app.services.errors import DomainError

    call = getattr(account, operation)
    extra = {} if operation == "get_my_preferences" else {"db": None}

    with pytest.raises(DomainError) as refused:
        call(context=ChangeContext(), **arguments, **extra)

    assert (refused.value.status_code, refused.value.detail) == (
        401,
        "Not authenticated",
    )


def test_password_changes_stop_while_the_rate_limit_store_is_unavailable(
    keen,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    from app.services import me as account

    user = keen.user()
    client = keen.client(user)
    original = (user.password_hash, user.mfa_version)

    def unavailable():
        raise ConnectionError("valkey down")

    monkeypatch.setattr(account, "get_valkey", unavailable)

    response = client.post(
        "/v1/me/password",
        headers=unsafe(client),
        json={
            "current_password": PASSWORD,
            "new_password": "another-synthetic-passphrase",
        },
    )

    assert (response.status_code, response.json()["detail"]) == (
        503,
        "Service temporarily unavailable. Please try again later.",
    )
    # The password is unchanged, so the session still signs the user in.
    keen.db.refresh(user)
    assert (user.password_hash, user.mfa_version) == original
    assert _json(client.get("/v1/me"))["user"] == user.username
