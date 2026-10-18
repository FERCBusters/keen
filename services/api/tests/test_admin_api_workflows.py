"""Administrator workflows through the assembled application.

Requests use real password sign-in, CSRF, the admin-namespace middleware and
persisted SQL state (PostgreSQL when KEEN_CONTRACT_TEST_DATABASE_URL is set).
Worker dispatch and rule evaluation are replaced at their owning modules.
"""

import io
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import (
    Artifact,
    AuditLog,
    ControlClauseLink,
    ControlItem,
    EntityChangelog,
    Event,
    Framework,
    FrameworkClause,
    Group,
    IngestionCursor,
    Mapping,
    Permission,
    User,
    user_groups,
)

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

HOSTILE = '<img src=x onerror="alert(1)">'


@pytest.fixture
def admin(keen, monkeypatch, tmp_path):  # noqa: F811 - fixture imported above
    monkeypatch.setattr(settings, "artifact_storage_backend", "local")
    monkeypatch.setattr(settings, "artifact_local_dir", str(tmp_path))
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 0)
    db = keen.db
    db.add_all([Framework(slug="A"), Framework(slug="B")])
    db.commit()
    user = keen.user(role="admin")
    client = keen.client(user)

    def call(method, path, body=None, status=200, **kwargs):
        headers = unsafe(client) if method != "GET" else {}
        response = client.request(
            method,
            path,
            headers=headers,
            **({"json": body} if body is not None else {}),
            **kwargs,
        )
        assert response.status_code == status, response.text
        return response.json() if response.content else None

    return SimpleNamespace(keen=keen, db=db, user=user, client=client, call=call)


def _control(db, ref="A.5.1", framework="A", **kw):
    row = ControlItem(
        framework_slug=framework,
        ref=ref,
        title=f"{ref} title",
        type="annex_control",
        **kw,
    )
    db.add(row)
    db.commit()
    return row


def _clause(db, ref="4.1", framework="A", parent=None):
    row = FrameworkClause(
        framework_slug=framework,
        ref=ref,
        title=f"Clause {ref}",
        parent_clause_id=parent.id if parent else None,
        sort_order=0,
    )
    db.add(row)
    db.commit()
    return row


# --- Authorisation boundary ---------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/v1/admin/groups"),
        ("POST", "/v1/admin/groups"),
        ("GET", "/v1/admin/permissions"),
        ("GET", "/v1/admin/ingest/status"),
        ("POST", "/v1/admin/ingest/github/run"),
        ("POST", "/v1/admin/remap/run"),
        ("POST", "/v1/admin/diary"),
        ("PATCH", f"/v1/admin/controls/{uuid.uuid4()}/justification"),
    ],
)
def test_admin_routes_reject_non_administrators(keen, method, path):  # noqa: F811
    reader = keen.client(keen.user("audittrail.read", "isms.manage"))
    response = reader.request(method, path, headers=unsafe(reader), json={})
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin role required"
    anonymous = keen.client()
    assert anonymous.request(method, path, json={}).status_code in (401, 403)


def test_audit_views_need_audit_permission_or_administrator(keen):  # noqa: F811
    keen.db.add_all(
        [
            AuditLog(
                ts=datetime(2026, 10, 1, 10, 0),
                username="dana",
                method="POST",
                path="/v1/risks",
                query_string="q=1",
                status_code=201,
                duration_ms=5,
            ),
            AuditLog(
                ts=datetime(2026, 10, 1, 11, 0),
                username="sam",
                method="GET",
                path="/v1/events",
                status_code=500,
                duration_ms=9,
            ),
        ]
    )
    keen.db.commit()
    plain = keen.client(keen.user("isms.read"))
    assert plain.get("/v1/admin/audit").status_code == 403
    assert plain.get("/v1/admin/entity-changelog").status_code == 403

    auditor = keen.client(keen.user("audittrail.read"))
    response = auditor.get(
        "/v1/admin/audit",
        params={
            "username": "DAN",
            "method": "post",
            "path": "risks",
            "status_min": 200,
            "status_max": 299,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["query_string"] == "q=1"
    paged = auditor.get("/v1/admin/audit", params={"limit": 999, "offset": -5}).json()
    assert paged["limit"] == 200
    assert paged["offset"] == 0
    failures = auditor.get("/v1/admin/audit", params={"status_min": 500}).json()
    assert {row["username"] for row in failures["items"]} >= {"sam"}
    assert all(row["status_code"] >= 500 for row in failures["items"])
    # Auditing remains read-only.
    assert (
        auditor.post(
            "/v1/admin/groups", headers=unsafe(auditor), json={"name": "x"}
        ).status_code
        == 403
    )


def test_entity_changelog_filters_are_validated(admin):
    admin.call(
        "GET", "/v1/admin/entity-changelog", status=400, params={"entity_type": "user"}
    )
    admin.call(
        "GET",
        "/v1/admin/entity-changelog",
        status=400,
        params={"entity_id": "not-a-uuid"},
    )
    control = _control(admin.db)
    admin.call(
        "PATCH",
        f"/v1/admin/controls/{control.id}/justification",
        {"justification": "Best Practice"},
    )
    found = admin.call(
        "GET",
        "/v1/admin/entity-changelog",
        params={"entity_type": "CONTROL", "entity_id": str(control.id), "q": "A.5.1"},
    )
    assert found["total"] == 1
    assert found["items"][0]["entity_type"] == "control"
    none = admin.call(
        "GET", "/v1/admin/entity-changelog", params={"username": "nobody-here"}
    )
    assert none["total"] == 0


# --- Controls and clauses -------------------------------------------------------


def test_control_import_creates_updates_and_mirrors_clauses(admin):
    payload = {
        "framework": "IMP",
        "framework_name": "Imported framework",
        "framework_version": "2026",
        "framework_url": "https://standards.example.test/imp",
        "items": [
            {"type": "clause", "ref": "4", "title": "Context"},
            {
                "type": "clause",
                "ref": "4.1",
                "title": "Understanding",
                "metadata": {"parent_ref": "4"},
            },
            {"type": "annex_control", "ref": "A.1", "title": HOSTILE, "tags": {"x": 1}},
            {"type": "", "ref": "bad"},
        ],
    }
    result = admin.call("POST", "/v1/admin/controls/import", payload)
    assert result["created"] == 3
    assert result["updated"] == 0
    assert result["errors"] == [{"ref": "bad", "error": "type/ref required"}]
    framework = admin.db.query(Framework).filter_by(slug="IMP").one()
    assert framework.name == "Imported framework"
    child = (
        admin.db.query(FrameworkClause).filter_by(framework_slug="IMP", ref="4.1").one()
    )
    parent = (
        admin.db.query(FrameworkClause).filter_by(framework_slug="IMP", ref="4").one()
    )
    assert child.parent_clause_id == parent.id
    control = (
        admin.db.query(ControlItem).filter_by(framework_slug="IMP", ref="A.1").one()
    )
    assert control.title == HOSTILE

    payload["framework_name"] = "Renamed"
    payload["items"] = [
        {"type": "annex_control", "ref": "A.1", "title": "Plain", "in_scope": False},
        {"type": "clause", "ref": "4.1", "title": "Understanding v2", "metadata": {}},
    ]
    second = admin.call("POST", "/v1/admin/controls/import", payload)
    assert second == {"created": 0, "updated": 2, "errors": []}
    admin.db.expire_all()
    assert admin.db.query(Framework).filter_by(slug="IMP").one().name == "Renamed"
    control = (
        admin.db.query(ControlItem).filter_by(framework_slug="IMP", ref="A.1").one()
    )
    assert (control.title, control.in_scope) == ("Plain", False)
    child = (
        admin.db.query(FrameworkClause).filter_by(framework_slug="IMP", ref="4.1").one()
    )
    assert (child.title, child.parent_clause_id) == ("Understanding v2", None)


def test_control_import_reports_refused_items_and_keeps_the_rest(admin):
    payload = {
        "framework": "IMP",
        "items": [
            {"type": "clause", "ref": "5", "title": "Leadership"},
            {"type": "annex_control", "ref": "A.2", "title": "x" * 300},
            {
                "type": "clause",
                "ref": "5.1",
                "title": "Commitment",
                "metadata": {"parent": "5"},
            },
        ],
    }
    result = admin.call("POST", "/v1/admin/controls/import", payload)
    assert (result["created"], result["updated"]) == (2, 0)
    # Database messages are not echoed back.
    assert result["errors"] == [{"ref": "A.2", "error": "The item could not be stored"}]
    assert (
        admin.db.query(ControlItem).filter_by(framework_slug="IMP", ref="A.2").count()
        == 0
    )
    child = (
        admin.db.query(FrameworkClause).filter_by(framework_slug="IMP", ref="5.1").one()
    )
    assert child.parent.ref == "5"


def test_control_import_stops_on_database_failures(admin, monkeypatch):
    from app.services import admin as admin_service
    from sqlalchemy.exc import OperationalError

    real = admin_service._import_control_item

    def flaky(db, framework_slug, item):
        if item.ref == "A.2":
            raise OperationalError("SELECT 1", {}, Exception("connection lost"))
        return real(db, framework_slug, item)

    monkeypatch.setattr(admin_service, "_import_control_item", flaky)
    payload = {
        "framework": "IMP",
        "items": [
            {"type": "annex_control", "ref": "A.1", "title": "First"},
            {"type": "annex_control", "ref": "A.2", "title": "Second"},
        ],
    }
    response = admin.client.post(
        "/v1/admin/controls/import", json=payload, headers=unsafe(admin.client)
    )
    # Infrastructure failures abandon the whole import.
    assert response.status_code == 500
    assert "connection lost" not in response.text
    assert admin.db.query(ControlItem).filter_by(framework_slug="IMP").count() == 0
    assert admin.db.query(Framework).filter_by(slug="IMP").one_or_none() is None


@pytest.mark.parametrize("slug", ["ISO 27001", "a/b", HOSTILE])
def test_control_import_needs_a_usable_framework_slug(admin, slug):
    response = admin.call(
        "POST",
        "/v1/admin/controls/import",
        {
            "framework": slug,
            "items": [{"type": "annex_control", "ref": "A.1", "title": "x"}],
        },
        status=400,
    )
    assert response["detail"].startswith("Framework slugs use up to 64 letters")
    assert admin.db.query(Framework).filter_by(slug=slug).one_or_none() is None


def test_control_import_rejects_unsafe_framework_urls(admin):
    for url in ["javascript:alert(1)", "ftp://example.test/x", "https://" + "a" * 2050]:
        admin.call(
            "POST",
            "/v1/admin/controls/import",
            {"framework": "X", "framework_url": url, "items": []},
            status=400,
        )
    assert admin.db.query(Framework).filter_by(slug="X").one_or_none() is None


def test_control_clause_links_skip_intermediate_parents_and_record_history(admin):
    db = admin.db
    control = _control(db)
    top = _clause(db, "7")
    middle = _clause(db, "7.5", parent=top)
    leaf = _clause(db, "7.5.1", parent=middle)
    other = _clause(db, "8")
    _clause(db, "9", framework="B")

    result = admin.call(
        "PATCH",
        f"/v1/admin/controls/{control.id}/clauses",
        {
            "clauses": [
                {"clause_id": str(middle.id), "applicability": "applicable"},
                {"clause_id": str(leaf.id), "applicability": "partially_applicable"},
                {"ref": " 8 ", "applicability": "applicable"},
                {"clause_id": str(leaf.id), "applicability": "applicable"},
            ]
        },
    )
    assert result == {"ok": True, "count": 2}
    links = {
        (row.clause_id, row.applicability)
        for row in db.query(ControlClauseLink).filter_by(control_item_id=control.id)
    }
    assert links == {(leaf.id, "partially_applicable"), (other.id, "applicable")}
    history = db.query(EntityChangelog).filter_by(entity_id=control.id).all()
    assert len(history) == 1
    assert {
        row.entity_id
        for row in db.query(EntityChangelog).filter_by(entity_type="clause")
    } == {leaf.id, other.id}

    # Clauses from another framework and unknown applicability are rejected.
    admin.call(
        "PATCH",
        f"/v1/admin/controls/{control.id}/clauses",
        {"clauses": [{"ref": "9", "applicability": "applicable"}]},
        status=400,
    )
    admin.call(
        "PATCH",
        f"/v1/admin/controls/{control.id}/clauses",
        {"clauses": [{"ref": "8", "applicability": "maybe"}]},
        status=400,
    )
    admin.call(
        "PATCH", "/v1/admin/controls/not-a-uuid/clauses", {"clauses": []}, status=400
    )
    admin.call(
        "PATCH",
        f"/v1/admin/controls/{uuid.uuid4()}/clauses",
        {"clauses": []},
        status=404,
    )
    # The failed requests left the links unchanged.
    assert (
        db.query(ControlClauseLink).filter_by(control_item_id=control.id).count() == 2
    )


def test_clause_control_links_replace_and_clear_intermediate_parents(admin):
    db = admin.db
    c1, c2 = _control(db, "A.1"), _control(db, "A.2")
    _control(db, "B.1", framework="B")
    top = _clause(db, "5")
    parent = _clause(db, "5.1", parent=top)
    leaf = _clause(db, "5.1.1", parent=parent)
    result = admin.call(
        "PATCH",
        f"/v1/admin/clauses/{leaf.id}/controls",
        {
            "controls": [
                {"control_id": str(c1.id), "applicability": "applicable"},
                {"ref": "A.2", "applicability": "partially_applicable"},
                {"ref": "A.2", "applicability": "applicable"},
            ]
        },
    )
    assert result == {"ok": True, "count": 2}
    assert {
        row.control_item_id
        for row in db.query(ControlClauseLink).filter_by(clause_id=leaf.id)
    } == {c1.id, c2.id}
    admin.call(
        "PATCH",
        f"/v1/admin/clauses/{leaf.id}/controls",
        {"controls": [{"ref": "B.1", "applicability": "applicable"}]},
        status=400,
    )
    admin.call(
        "PATCH",
        f"/v1/admin/clauses/{leaf.id}/controls",
        {"controls": [{"ref": "A.1", "applicability": ""}]},
        status=400,
    )

    # Saving links on an intermediate parent clears any legacy direct link and
    # skips the requested ones.
    db.add(
        ControlClauseLink(
            control_item_id=c1.id, clause_id=parent.id, applicability="applicable"
        )
    )
    db.commit()
    cleared = admin.call(
        "PATCH",
        f"/v1/admin/clauses/{parent.id}/controls",
        {"controls": [{"ref": "A.2", "applicability": "applicable"}]},
    )
    assert cleared["skipped_intermediate_parent"] is True
    assert cleared["count"] == 0
    assert db.query(ControlClauseLink).filter_by(clause_id=parent.id).count() == 0
    again = admin.call(
        "PATCH", f"/v1/admin/clauses/{parent.id}/controls", {"controls": []}
    )
    assert again["skipped_intermediate_parent"] is True
    admin.call("PATCH", "/v1/admin/clauses/x/controls", {"controls": []}, status=400)
    admin.call(
        "PATCH",
        f"/v1/admin/clauses/{uuid.uuid4()}/controls",
        {"controls": []},
        status=404,
    )


def test_control_justification_and_upstream_urls(admin):
    db = admin.db
    control = _control(db, meta={"soa_justification": "old"})
    clause = _clause(db)
    saved = admin.call(
        "PATCH",
        f"/v1/admin/controls/{control.id}/justification",
        {"justification": "Best Practice"},
    )
    assert saved == {"ok": True, "justification": "Best Practice"}
    cleared = admin.call(
        "PATCH", f"/v1/admin/controls/{control.id}/justification", {"justification": ""}
    )
    assert cleared["justification"] in (None, "")
    db.expire_all()
    assert "soa_justification" not in (db.get(ControlItem, control.id).meta or {})

    url = "https://standards.example.test/a.5.1"
    assert (
        admin.call(
            "PATCH",
            f"/v1/admin/controls/{control.id}/upstream-url",
            {"upstream_url": url},
        )["upstream_url"]
        == url
    )
    db.expire_all()
    assert db.get(ControlItem, control.id).meta["upstream_url"] == url
    admin.call(
        "PATCH", f"/v1/admin/controls/{control.id}/upstream-url", {"upstream_url": None}
    )
    db.expire_all()
    assert "upstream_url" not in db.get(ControlItem, control.id).meta

    admin.call(
        "PATCH", f"/v1/admin/clauses/{clause.id}/upstream-url", {"upstream_url": url}
    )
    db.expire_all()
    assert db.get(FrameworkClause, clause.id).meta["upstream_url"] == url
    for bad in ["javascript:alert(1)", "/relative", "data:text/html,x"]:
        admin.call(
            "PATCH",
            f"/v1/admin/clauses/{clause.id}/upstream-url",
            {"upstream_url": bad},
            status=400,
        )
        admin.call(
            "PATCH",
            f"/v1/admin/controls/{control.id}/upstream-url",
            {"upstream_url": bad},
            status=400,
        )
    for path in [
        "controls/{}/justification",
        "controls/{}/upstream-url",
        "clauses/{}/upstream-url",
        "clauses/{}/evidence-urls",
    ]:
        admin.call("PATCH", "/v1/admin/" + path.format("nope"), {}, status=400)
        admin.call("PATCH", "/v1/admin/" + path.format(uuid.uuid4()), {}, status=404)


def test_clause_evidence_mappings_are_validated_and_canonicalised(admin):
    db = admin.db
    clause = _clause(db)
    clause.meta = {
        "evidence_mappings": [{"title": "Old", "url": "https://old.example"}],
        "upstream_url": "https://standards.example/9.2",
    }
    db.commit()
    saved = admin.call(
        "PATCH",
        f"/v1/admin/clauses/{clause.id}/evidence-urls",
        {
            "evidence_mappings": [
                {"title": " Minutes ", "url": "https://docs.example.test/minutes"},
                {"title": "", "url": "/isms.html?tab=documents"},
                {"title": "Duplicate", "url": "https://docs.example.test/minutes"},
                {"title": "Blank", "url": "  "},
            ]
        },
    )
    assert saved["evidence_mappings"] == [
        {"title": "Minutes", "url": "https://docs.example.test/minutes"},
        {"title": "/isms.html?tab=documents", "url": "/isms.html?tab=documents"},
    ]
    assert saved["evidence_count"] == 2
    db.expire_all()
    meta = db.get(FrameworkClause, clause.id).meta
    # The list replaced the earlier mappings and left other metadata alone.
    assert meta == {
        "evidence_mappings": saved["evidence_mappings"],
        "upstream_url": "https://standards.example/9.2",
    }
    empty = admin.call(
        "PATCH",
        f"/v1/admin/clauses/{clause.id}/evidence-urls",
        {"evidence_mappings": []},
    )
    assert empty["evidence_count"] == 0
    db.expire_all()
    assert "evidence_mappings" not in db.get(FrameworkClause, clause.id).meta

    for bad in [
        "javascript:alert(1)",
        "//evil.example/x",
        "/\\evil.example",
        "/ok\tpath",
        "mailto:a@example.test",
        "DATA:text/html,x",
    ]:
        admin.call(
            "PATCH",
            f"/v1/admin/clauses/{clause.id}/evidence-urls",
            {"evidence_mappings": [{"title": "x", "url": bad}]},
            status=400,
        )
    too_many = [{"url": f"https://e.example/{i}"} for i in range(51)]
    admin.call(
        "PATCH",
        f"/v1/admin/clauses/{clause.id}/evidence-urls",
        {"evidence_mappings": too_many},
        status=400,
    )
    fifty = admin.call(
        "PATCH",
        f"/v1/admin/clauses/{clause.id}/evidence-urls",
        {"evidence_mappings": too_many[:50]},
    )
    assert fifty["evidence_count"] == 50


# --- Groups and permissions -----------------------------------------------------


def test_group_lifecycle_updates_members_permissions_and_authorisation_versions(admin):
    db = admin.db
    member = admin.keen.user()
    before = member.authz_version
    db.add(Permission(code="reports.view", description="View reports"))
    db.commit()

    created = admin.call(
        "POST",
        "/v1/admin/groups",
        {"name": " Auditors ", "description": "Read only", "role": "normal"},
    )
    gid = created["id"]
    assert created["name"] == "Auditors"
    assert created["role"] == "normal"
    admin.call("POST", "/v1/admin/groups", {"name": "Auditors"}, status=400)
    admin.call(
        "POST", "/v1/admin/groups", {"name": "Other", "role": "superuser"}, status=400
    )
    admin.call(
        "POST", "/v1/admin/groups", {"name": "Other", "role": "inherit"}, status=400
    )
    admin.call("POST", "/v1/admin/groups", {"name": "   "}, status=400)
    plain = admin.call("POST", "/v1/admin/groups", {"name": HOSTILE})
    assert plain["role"] is None

    members = admin.call(
        "PUT", f"/v1/admin/groups/{gid}/members", {"user_ids": [str(member.id)]}
    )
    assert members["user_ids"] == [str(member.id)]
    admin.call(
        "PUT",
        f"/v1/admin/groups/{gid}/members",
        {"user_ids": [str(uuid.uuid4())]},
        status=400,
    )
    perms = admin.call(
        "PUT",
        f"/v1/admin/groups/{gid}/permissions",
        {"permission_codes": ["reports.view"]},
    )
    assert perms["permission_codes"] == ["reports.view"]
    missing = admin.client.put(
        f"/v1/admin/groups/{gid}/permissions",
        headers=unsafe(admin.client),
        json={"permission_codes": ["no.such.code"]},
    )
    assert missing.status_code == 400
    assert missing.json()["detail"] == {"missing_permission_codes": ["no.such.code"]}

    listed = {row["name"]: row for row in admin.call("GET", "/v1/admin/groups")}
    assert listed["Auditors"]["member_count"] == 1
    assert listed["Auditors"]["permission_count"] == 1
    assert listed[HOSTILE]["member_count"] == 0

    updated = admin.call(
        "PATCH",
        f"/v1/admin/groups/{gid}",
        {"name": "Internal auditors", "description": "", "role": ""},
    )
    assert updated["name"] == "Internal auditors"
    assert updated["role"] is None
    admin.call("PATCH", f"/v1/admin/groups/{gid}", {"name": HOSTILE}, status=400)
    admin.call("PATCH", f"/v1/admin/groups/{gid}", {"name": " "}, status=400)
    admin.call("PATCH", f"/v1/admin/groups/{gid}", {"role": "owner"}, status=400)

    db.expire_all()
    assert db.get(User, member.id).authz_version > before
    detail = admin.call("GET", f"/v1/admin/groups/{gid}")
    assert detail["permission_codes"] == ["reports.view"]
    admin.call("DELETE", f"/v1/admin/groups/{gid}")
    admin.call("GET", f"/v1/admin/groups/{gid}", status=404)
    for method, suffix, body in [
        ("GET", "", None),
        ("PATCH", "", {}),
        ("DELETE", "", None),
        ("PUT", "/members", {"user_ids": []}),
        ("PUT", "/permissions", {"permission_codes": []}),
    ]:
        admin.call(method, f"/v1/admin/groups/not-a-uuid{suffix}", body, status=400)
        admin.call(method, f"/v1/admin/groups/{uuid.uuid4()}{suffix}", body, status=404)


def test_the_last_effective_administrator_cannot_be_removed_through_groups(admin):
    db = admin.db
    # The signed-in administrator becomes admin only through a group.
    group = Group(name="Admins", role="admin")
    db.add(group)
    db.flush()
    db.execute(user_groups.insert().values(user_id=admin.user.id, group_id=group.id))
    for row in db.query(User).filter(User.role == "admin").all():
        row.role = "inherit"
    db.commit()
    admin.call("PATCH", f"/v1/admin/groups/{group.id}", {"role": "normal"}, status=400)
    admin.call(
        "PUT", f"/v1/admin/groups/{group.id}/members", {"user_ids": []}, status=400
    )
    admin.call("DELETE", f"/v1/admin/groups/{group.id}", status=400)
    db.expire_all()
    assert db.get(Group, group.id).role == "admin"
    assert db.query(user_groups).filter(user_groups.c.group_id == group.id).count() == 1


def test_permissions_are_created_once_with_valid_codes(admin):
    created = admin.call(
        "POST",
        "/v1/admin/permissions",
        {"code": " reports.export ", "description": HOSTILE},
    )
    assert created["code"] == "reports.export"
    assert created["description"] == HOSTILE
    admin.call("POST", "/v1/admin/permissions", {"code": "reports.export"}, status=400)
    admin.call("POST", "/v1/admin/permissions", {"code": "has space"}, status=400)
    admin.call("POST", "/v1/admin/permissions", {"code": "   "}, status=400)
    codes = [row["code"] for row in admin.call("GET", "/v1/admin/permissions")]
    assert "reports.export" in codes
    assert codes == sorted(codes)


# --- Ingestion controls -----------------------------------------------------------


def test_ingest_runs_queue_enabled_sources_and_refuse_disabled_or_paused(
    admin, monkeypatch
):
    from app.services import ingestion_pause
    from app.worker import tasks

    queued = []

    class Task:
        def __init__(self, name):
            self.name = name

        def apply_async(self, **kwargs):
            queued.append((self.name, kwargs))
            return SimpleNamespace(id="task-" + self.name)

    sources = [
        "loki",
        "cloudwatch_logs",
        "github",
        "forgejo",
        "jenkins",
        "taiga",
        "bookstack",
        "rss",
        "google_workspace",
        "gitea",
        "gitlab",
        "redmine",
        "riskledger",
    ]
    for source in sources:
        monkeypatch.setattr(tasks, f"ingest_{source}_all_task", Task(source))
        monkeypatch.setattr(settings, f"{source}_enabled", True)
    monkeypatch.setattr(tasks, "dispatch_imap_task", Task("imap"))
    monkeypatch.setattr(settings, "imap_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", False)

    for source in sources + ["imap"]:
        result = admin.call("POST", f"/v1/admin/ingest/{source}/run", status=202)
        assert result["queued"] is True
        assert result["task_id"] == "task-" + source
    assert [name for name, _ in queued] == sources + ["imap"]
    assert queued[-1][1] == {"expires": 240}

    status = admin.call("GET", "/v1/admin/ingest/status")["enabled"]
    assert status["github"] is True and status["imap"] is True

    monkeypatch.setattr(settings, "github_enabled", False)
    assert (
        admin.call("POST", "/v1/admin/ingest/github/run", status=409)["detail"]
        == "Source type is disabled"
    )
    monkeypatch.setattr(
        settings, "demo_expires_at", datetime(2999, 1, 1, tzinfo=timezone.utc)
    )
    monkeypatch.setattr(settings, "demo_mode", True)
    assert (
        admin.call("POST", "/v1/admin/ingest/redmine/run", status=409)["detail"]
        == "Source type is unavailable in demo mode"
    )
    assert (
        admin.call("POST", "/v1/admin/ingest/imap/run", status=409)["detail"]
        == "IMAP collection is disabled"
    )
    status = admin.call("GET", "/v1/admin/ingest/status")["enabled"]
    assert (
        status["redmine"],
        status["riskledger"],
        status["imap"],
        status["github"],
    ) == (False, False, False, False)
    monkeypatch.setattr(settings, "demo_mode", False)

    monkeypatch.setattr(
        ingestion_pause, "is_paused", lambda db, source: source in {"jenkins", "imap"}
    )
    assert (
        admin.call("POST", "/v1/admin/ingest/jenkins/run", status=409)["detail"]
        == "Ingestion is paused"
    )
    assert (
        admin.call("POST", "/v1/admin/ingest/imap/run", status=409)["detail"]
        == "IMAP ingestion is paused"
    )
    assert len(queued) == len(sources) + 1


def test_loki_cursor_reset_moves_checkpoints_forward(admin, monkeypatch):
    from app.ingest import connections as connection_module
    from app.services import admin as admin_service

    db = admin.db
    connection = SimpleNamespace(id="loki-main")

    class Scope:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        connection_module, "connections", lambda _db, kind: [connection]
    )
    monkeypatch.setattr(connection_module, "connection_scope", lambda _c: Scope())
    monkeypatch.setattr(
        connection_module, "namespace", lambda name: f"loki-main:{name}"
    )
    monkeypatch.setattr(
        admin_service,
        "load_loki_queries",
        lambda _path: {"queries": [{"name": "auth"}, {"title": "unnamed"}]},
    )
    db.add(
        IngestionCursor(
            name="loki-main:loki:legacy",
            last_ts=datetime(2026, 1, 1),
            meta={"keep": True},
        )
    )
    db.add(
        IngestionCursor(name="other:loki:auth", last_ts=datetime(2026, 1, 1), meta={})
    )
    db.commit()

    reset = admin.call("POST", "/v1/admin/ingest/loki/reset")
    assert reset["cursors"] == ["loki-main:loki:auth", "loki-main:loki:legacy"]
    assert reset["count"] == 2
    db.expire_all()
    legacy = db.query(IngestionCursor).filter_by(name="loki-main:loki:legacy").one()
    assert legacy.meta["keep"] is True
    assert legacy.meta["manual_reset_by"] == admin.user.username
    assert legacy.meta["manual_reset_previous_cursor"].startswith("2026-01-01T00:00:00")
    assert legacy.last_ts.year >= 2026
    untouched = db.query(IngestionCursor).filter_by(name="other:loki:auth").one()
    assert untouched.last_ts == datetime(2026, 1, 1)

    single = admin.call(
        "POST",
        "/v1/admin/ingest/loki/reset",
        params={"query_name": "loki:errors", "connection_id": "loki-main"},
    )
    assert single["cursors"] == ["loki-main:loki:errors"]
    bare = admin.call(
        "POST", "/v1/admin/ingest/loki/reset", params={"query_name": "warnings"}
    )
    assert bare["cursors"] == ["loki-main:loki:warnings"]
    admin.call(
        "POST",
        "/v1/admin/ingest/loki/reset",
        params={"connection_id": "unknown"},
        status=404,
    )


# --- Remapping and diary evidence -------------------------------------------------


def _event(db, **kw):
    defaults = dict(
        source="jenkins",
        system="ci",
        action="deploy",
        outcome="success",
        severity=2,
        summary="Deploy",
        timestamp=datetime(2026, 3, 1, 12, 0),
        external_id=uuid.uuid4().hex,
        raw_pointer={},
        normalized_payload={},
    )
    defaults.update(kw)
    row = Event(**defaults)
    db.add(row)
    db.commit()
    return row


def test_remap_applies_rules_to_filtered_events(admin, monkeypatch):
    from app.services import admin as admin_service

    db = admin.db
    target = _event(db)
    failing = _event(db, summary="Breaks rules")
    _event(db, source="github", summary="Other source")
    _event(db, timestamp=datetime(2025, 1, 1), summary="Too old")
    seen = []

    def rules(_db, event):
        seen.append(event.summary)
        if event.id == failing.id:
            raise RuntimeError("bad rule")
        return 1

    monkeypatch.setattr(admin_service, "apply_rules", rules)
    monkeypatch.setattr(
        admin_service, "apply_bookstack_config_mappings", lambda _db, _e: 0
    )
    monkeypatch.setattr(
        admin_service,
        "rebuild_framework_event_stats",
        lambda _db, clear_cache: {"A": 1},
    )
    monkeypatch.setattr(admin_service, "clear_stats_caches", lambda: 3)

    result = admin.call(
        "POST",
        "/v1/admin/remap/run",
        params={
            "source": " jenkins ",
            "from_date": "2026-01-01",
            "to_date": "2026-12-31T23:00:00+10:00",
            "system": "ci",
            "action": "deploy",
            "outcome": "success",
            "limit": 0,
        },
    )
    assert sorted(seen) == ["Breaks rules", "Deploy"]
    assert result["processed_events"] == 2
    assert result["created_mappings"] == 1
    assert result["rule_mappings_created"] == 1
    assert result["stats_rebuilt"] == {"A": 1}
    assert result["cache_keys_deleted"] == 3
    assert result["filters"] == {
        "source": "jenkins",
        "system": "ci",
        "action": "deploy",
        "outcome": "success",
    }
    assert result["limit"] == 0

    # Already-mapped events are skipped unless include_mapped is set.
    control = _control(db)
    db.add(
        Mapping(
            event_id=target.id,
            control_item_id=control.id,
            confidence=1.0,
            method="rule",
        )
    )
    db.commit()
    seen.clear()
    admin.call("POST", "/v1/admin/remap/run", params={"source": "jenkins", "limit": 1})
    assert seen == ["Breaks rules"]
    seen.clear()
    mapped = admin.call(
        "POST",
        "/v1/admin/remap/run",
        params={"source": "jenkins", "include_mapped": True, "from_date": "nonsense"},
    )
    assert sorted(seen) == ["Breaks rules", "Deploy", "Too old"]
    assert mapped["include_mapped"] is True


def test_diary_entries_map_controls_and_store_attachments(admin):
    db = admin.db
    control = _control(db, "A.5.1")
    files = [
        ("attachments", ("screen.png", io.BytesIO(b"\x89PNG data"), "image/png")),
        (
            "attachments",
            ("notes.extremelylongextension", io.BytesIO(b"text"), "text/plain"),
        ),
        ("attachments", ("empty.bin", io.BytesIO(b""), "application/octet-stream")),
    ]
    response = admin.client.post(
        "/v1/admin/diary",
        headers=unsafe(admin.client),
        data={
            "summary": " Quarterly access review ",
            "details": json.dumps({"reviewed": 12}),
            "timestamp": "2026-10-01T09:30:00Z",
            "controls": ["A.5.1", "A.9.9", "A.5.1"],
            "framework": "A",
            "links": json.dumps(
                [{"url": "https://tickets.example.test/1", "text": "Ticket"}]
            ),
        },
        files=files,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["manual_mappings_created"] == 2
    assert body["attachments_created"] == 2
    event = db.query(Event).filter(Event.id == uuid.UUID(body["event_id"])).one()
    assert event.source == "diary"
    assert event.actor == admin.user.username
    assert event.summary == "diary: Quarterly access review"
    assert event.normalized_payload["links"] == [
        {"url": "https://tickets.example.test/1", "text": "Ticket"}
    ]
    mapped = {m.control_item_id for m in db.query(Mapping).filter_by(event_id=event.id)}
    stub = db.query(ControlItem).filter_by(framework_slug="A", ref="A.9.9").one()
    assert mapped == {control.id, stub.id}
    assert stub.type == "annex_control"
    kinds = sorted(a.kind for a in db.query(Artifact).filter_by(event_id=event.id))
    assert kinds == ["diary_attachment", "diary_entry", "diary_image"]
    storage = sorted(
        a.storage_uri
        for a in db.query(Artifact).filter_by(event_id=event.id)
        if a.kind != "diary_entry"
    )
    assert storage[0].endswith("attachment-1.png")
    # Long extensions are capped at twelve characters, including the dot.
    assert storage[1].endswith("attachment-2.extremelylo")

    # JSON entries use the server-side author and ignore a browser-supplied one.
    json_entry = admin.call(
        "POST",
        "/v1/admin/diary",
        {
            "summary": "Policy signed",
            "author": "someone-else",
            "controls": "A.5.1, 6.1",
            "framework": "A",
            "link": [{"url": "https://docs.example.test"}],
        },
    )
    second = db.query(Event).filter(Event.id == uuid.UUID(json_entry["event_id"])).one()
    assert second.actor == admin.user.username
    assert (
        db.query(ControlItem).filter_by(framework_slug="A", ref="6.1").one().type
        == "clause"
    )
    admin.call("POST", "/v1/admin/diary", {"summary": "  "}, status=400)


def test_diary_choices_name_the_exact_control_or_clause(admin):
    db = admin.db

    def row(ref, kind, framework="A"):
        item = ControlItem(framework_slug=framework, ref=ref, title=HOSTILE, type=kind)
        db.add(item)
        db.commit()
        return item

    clause, control = row("5.1", "clause"), row("5.1", "annex_control")
    row("7.1", "annex_control"), row("7.1", "custom")
    elsewhere = row("5.1", "annex_control", framework="B")

    def mapped(event_id):
        db.expire_all()
        return {
            m.control_item_id
            for m in db.query(Mapping).filter_by(event_id=uuid.UUID(event_id))
        }

    def post(controls, summary="Policy approved", status=200):
        body = {"summary": summary, "controls": controls, "framework": "A"}
        return admin.call("POST", "/v1/admin/diary", body, status=status)

    # A clause and a control sharing a reference are separate choices by id.
    by_id = post([str(clause.id), str(control.id)])
    assert mapped(by_id["event_id"]) == {clause.id, control.id}
    # A reference names the control when a clause shares it.
    assert mapped(post(["5.1"], summary="By reference")["event_id"]) == {control.id}
    edited = admin.call(
        "PATCH",
        f"/v1/admin/diary/{by_id['event_id']}",
        {"controls": [str(clause.id)], "framework": "A"},
    )
    assert edited["manual_mappings_set"] == 1
    assert mapped(by_id["event_id"]) == {clause.id}

    # Refused choices are reported before anything is written.
    events = db.query(Event).count()
    for controls, detail in (
        ([str(uuid.uuid4())], "Unknown control or clause for A"),
        ([str(elsewhere.id)], "Unknown control or clause for A"),
        (
            ["7.1"],
            "More than one control in A has the reference 7.1; use the control id",
        ),
    ):
        assert post(controls, summary="Refused", status=400)["detail"] == detail
        response = admin.call(
            "PATCH",
            f"/v1/admin/diary/{by_id['event_id']}",
            {"summary": "Changed", "controls": controls, "framework": "A"},
            status=400,
        )
        assert response["detail"] == detail
    db.expire_all()
    assert db.query(Event).count() == events
    assert (
        db.get(Event, uuid.UUID(by_id["event_id"])).summary == "diary: Policy approved"
    )
    assert mapped(by_id["event_id"]) == {clause.id}


def test_diary_edits_replace_fields_mappings_and_keep_history(admin):
    db = admin.db
    _control(db, "A.5.1")
    created = admin.call(
        "POST",
        "/v1/admin/diary",
        {"summary": "Original", "controls": ["A.5.1"], "framework": "A"},
    )
    eid = created["event_id"]
    edited = admin.call(
        "PATCH",
        f"/v1/admin/diary/{eid}",
        {
            "summary": "Edited",
            "details": "Plain text details",
            "links": [{"url": "https://docs.example.test/x", "text": HOSTILE}],
            "timestamp": "2026-05-05T05:05:05Z",
            "controls": "A.5.2",
            "framework": "A",
        },
    )
    assert edited["artifact_created"] is True
    assert edited["manual_mappings_set"] == 1
    db.expire_all()
    event = db.get(Event, uuid.UUID(eid))
    assert event.summary == "diary: Edited"
    assert event.timestamp.replace(tzinfo=None) == datetime(2026, 5, 5, 5, 5, 5)
    refs = {
        db.get(ControlItem, m.control_item_id).ref
        for m in db.query(Mapping).filter_by(event_id=event.id)
    }
    assert refs == {"A.5.2"}
    edit_artifacts = (
        db.query(Artifact).filter_by(event_id=event.id, kind="diary_entry_edit").all()
    )
    assert edit_artifacts[0].meta["edited_by"] == admin.user.username

    cleared = admin.call(
        "PATCH", f"/v1/admin/diary/{eid}", {"links": [], "controls": []}
    )
    assert cleared["manual_mappings_set"] == 0
    db.expire_all()
    assert "links" not in db.get(Event, uuid.UUID(eid)).normalized_payload
    assert db.query(Mapping).filter_by(event_id=uuid.UUID(eid)).count() == 0

    admin.call("PATCH", f"/v1/admin/diary/{eid}", {"summary": ""}, status=400)
    admin.call(
        "PATCH", f"/v1/admin/diary/{eid}", {"timestamp": "yesterday"}, status=400
    )
    admin.call("PATCH", "/v1/admin/diary/not-a-uuid", {}, status=400)
    admin.call("PATCH", f"/v1/admin/diary/{uuid.uuid4()}", {}, status=404)
    other = _event(db)
    admin.call("PATCH", f"/v1/admin/diary/{other.id}", {"summary": "x"}, status=400)
