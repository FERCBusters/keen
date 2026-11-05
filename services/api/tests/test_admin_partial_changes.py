"""Administrator changes that name only part of what they could change, and
diary changes whose files cannot be read or stored.

A change leaves everything it does not name as it was: a group keeps its
role, an imported framework keeps its details, a remap or audit search
without a filter reads everything. Group changes invalidate the permission
snapshots of the group's members only, and write nothing for a group without
members. Requests run through the assembled application as an
administrator, apart from the diary operation fed an unreadable file.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime

from app.db.models import (
    Artifact,
    AuditLog,
    ControlClauseLink,
    Event,
    Framework,
    Mapping,
    Permission,
    User,
)
from app.services import admin as admin_service
from app.services.uploads import Upload
from sqlalchemy import event as sql_event

# Imported fixtures: administrator requests through the assembled application.
from tests.test_admin_api_workflows import _clause, _control, _event, admin  # noqa: F401
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401

BUMP = "authz_version=(users.authz_version + "


@contextmanager
def _statements(db):
    seen = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    engine = db.get_bind().engine
    sql_event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        sql_event.remove(engine, "before_cursor_execute", record)


def _bumps(statements):
    return [s for s in statements if s.startswith("UPDATE users ") and BUMP in s]


# --- Groups -------------------------------------------------------------------


def test_changing_a_group_without_members_writes_no_permission_snapshots(admin):  # noqa: F811
    empty = admin.call("POST", "/v1/admin/groups", {"name": "Empty", "role": "normal"})
    path = f"/v1/admin/groups/{empty['id']}"

    with _statements(admin.db) as statements:
        described = admin.call("PATCH", path, {"description": "Nobody yet"})
        admin.call("PUT", path + "/members", {"user_ids": []})
        admin.call("PUT", path + "/permissions", {"permission_codes": []})
        admin.call("DELETE", path)

    assert _bumps(statements) == []
    # A change without a role keeps the group's role.
    assert (described["description"], described["role"]) == ("Nobody yet", "normal")
    admin.call("GET", path, status=404)


def test_a_group_change_invalidates_its_members_snapshots(admin):  # noqa: F811
    member, outsider = admin.keen.user(), admin.keen.user()
    before = {u.id: u.authz_version for u in (member, outsider)}
    group = admin.call("POST", "/v1/admin/groups", {"name": "Auditors"})
    path = f"/v1/admin/groups/{group['id']}"
    admin.call("PUT", path + "/members", {"user_ids": [str(member.id)]})
    admin.db.expire_all()
    joined = admin.db.get(User, member.id).authz_version

    with _statements(admin.db) as statements:
        admin.call("PATCH", path, {"description": "Internal audit"})

    assert len(_bumps(statements)) == 1
    admin.db.expire_all()
    assert joined > before[member.id]
    assert admin.db.get(User, member.id).authz_version == joined + 1
    assert admin.db.get(User, outsider.id).authz_version == before[outsider.id]


def test_clearing_a_groups_permissions_removes_every_one(admin):  # noqa: F811
    admin.db.add_all(
        [
            Permission(code="reports.view", description="View reports"),
            Permission(code="reports.edit", description="Edit reports"),
        ]
    )
    admin.db.commit()
    member = admin.keen.user()
    group = admin.call("POST", "/v1/admin/groups", {"name": "Reporters"})
    path = f"/v1/admin/groups/{group['id']}"
    admin.call("PUT", path + "/members", {"user_ids": [str(member.id)]})
    granted = admin.call(
        "PUT",
        path + "/permissions",
        {"permission_codes": ["reports.view", "reports.edit"]},
    )
    assert sorted(granted["permission_codes"]) == ["reports.edit", "reports.view"]
    admin.db.expire_all()
    before = admin.db.get(User, member.id).authz_version

    with _statements(admin.db) as statements:
        cleared = admin.call("PUT", path + "/permissions", {"permission_codes": []})

    assert cleared["permission_codes"] == []
    # With no codes there is nothing to look up before the grants are removed.
    assert not [s for s in statements if "permissions.code IN" in s]
    assert admin.call("GET", path)["permission_codes"] == []
    admin.db.expire_all()
    assert admin.db.get(User, member.id).authz_version == before + 1


# --- Catalogue ------------------------------------------------------------------


def test_an_import_without_framework_details_keeps_the_existing_ones(admin):  # noqa: F811
    framework = admin.db.query(Framework).filter_by(slug="A").one()
    framework.name, framework.version = "Annex A", "2022"
    framework.upstream_url = "https://standards.example.test/a"
    admin.db.commit()

    result = admin.call(
        "POST",
        "/v1/admin/controls/import",
        {"framework": "A", "items": [{"type": "annex_control", "ref": "A.9"}]},
    )

    assert result == {"created": 1, "updated": 0, "errors": []}
    admin.db.expire_all()
    kept = admin.db.query(Framework).filter_by(slug="A").one()
    assert (kept.name, kept.version, kept.upstream_url) == (
        "Annex A",
        "2022",
        "https://standards.example.test/a",
    )


def test_control_and_clause_links_name_an_id_or_a_reference(admin):  # noqa: F811
    control, clause = _control(admin.db), _clause(admin.db)

    unnamed_clause = admin.client.patch(
        f"/v1/admin/controls/{control.id}/clauses",
        json={"clauses": [{"applicability": "applicable"}]},
        headers=unsafe(admin.client),
    )
    unnamed_control = admin.client.patch(
        f"/v1/admin/clauses/{clause.id}/controls",
        json={"controls": [{"applicability": "applicable"}]},
        headers=unsafe(admin.client),
    )

    assert (unnamed_clause.status_code, unnamed_clause.json()["detail"]) == (
        400,
        "Unknown clause for framework",
    )
    assert (unnamed_control.status_code, unnamed_control.json()["detail"]) == (
        400,
        "Unknown control for framework",
    )
    assert admin.db.query(ControlClauseLink).count() == 0


# --- Remapping and the audit trail ----------------------------------------------


def test_a_remap_without_filters_reaches_every_unmapped_event(admin, monkeypatch):  # noqa: F811
    db = admin.db
    for source, when in (
        ("jenkins", datetime(2026, 3, 1)),
        ("github", datetime(2026, 2, 1)),
        ("rss", datetime(2019, 1, 1)),
    ):
        _event(db, source=source, timestamp=when, summary=f"{source} event")
    mapped = _event(db, summary="Already mapped")
    db.add(
        Mapping(
            event_id=mapped.id,
            control_item_id=_control(db).id,
            confidence=1.0,
            method="rule",
        )
    )
    db.commit()
    seen = []
    monkeypatch.setattr(
        admin_service, "apply_rules", lambda _db, event: seen.append(event.summary)
    )
    monkeypatch.setattr(
        admin_service, "apply_bookstack_config_mappings", lambda _db, _e: 0
    )

    result = admin.call(
        "POST",
        "/v1/admin/remap/run",
        params={"from_date": "   ", "to_date": "", "source": " "},
    )

    # Blank dates and filters limit nothing; mapped events are still skipped.
    assert sorted(seen) == ["github event", "jenkins event", "rss event"]
    assert result["processed_events"] == 3
    assert result["filters"] == {
        "source": None,
        "system": None,
        "action": None,
        "outcome": None,
    }


def test_a_blank_method_leaves_the_audit_trail_unfiltered(admin):  # noqa: F811
    who = "dana-" + uuid.uuid4().hex[:8]
    admin.db.add_all(
        [
            AuditLog(
                username=who,
                method=method,
                path="/v1/risks",
                status_code=200,
                duration_ms=3,
            )
            for method in ("POST", "GET")
        ]
    )
    admin.db.commit()

    def search(**params):
        return admin.call("GET", "/v1/admin/audit", params={"username": who, **params})

    assert search(method="   ")["total"] == 2
    assert search()["total"] == 2
    only = search(method=" post ")
    assert only["total"] == 1 and only["items"][0]["method"] == "POST"


# --- Diary files ------------------------------------------------------------------


def test_an_unreadable_diary_attachment_is_skipped_and_the_rest_kept(admin):  # noqa: F811
    def unreadable():
        raise OSError("request body file lost")

    result = admin_service.admin_diary(
        db=admin.db,
        author=admin.user.username,
        summary="Fire drill",
        details_obj=None,
        links=[],
        controls=[],
        framework="A",
        ts=None,
        uploads=[
            Upload(filename="lost.png", content_type="image/png", read=unreadable),
            Upload(filename="notes.txt", content_type="text/plain", read=lambda: b"ok"),
        ],
    )

    assert result["attachments_created"] == 1
    event_id = uuid.UUID(result["event_id"])
    files = admin.db.query(Artifact).filter_by(event_id=event_id).all()
    assert sorted(a.kind for a in files) == ["diary_attachment", "diary_entry"]
    (notes,) = [a for a in files if a.kind == "diary_attachment"]
    assert (notes.content_type, notes.size_bytes) == ("text/plain", 2)


def test_a_diary_edit_is_saved_when_its_history_copy_cannot_be_stored(
    admin,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    control = _control(admin.db, "A.5.1")
    created = admin.call(
        "POST",
        "/v1/admin/diary",
        {"summary": "Original", "controls": ["A.5.1"], "framework": "A"},
    )
    event_id = uuid.UUID(created["event_id"])

    def unavailable(**kwargs):
        raise OSError("object storage unavailable")

    monkeypatch.setattr(admin_service, "put_bytes", unavailable)
    edited = admin.call("PATCH", f"/v1/admin/diary/{event_id}", {"summary": "Edited"})

    # Without controls in the edit, the entry keeps its mappings.
    assert edited == {
        "event_id": str(event_id),
        "updated": True,
        "artifact_created": False,
        "manual_mappings_set": None,
    }
    admin.db.expire_all()
    assert admin.db.get(Event, event_id).summary == "diary: Edited"
    assert [
        m.control_item_id for m in admin.db.query(Mapping).filter_by(event_id=event_id)
    ] == [control.id]
    assert (
        admin.db.query(Artifact)
        .filter_by(event_id=event_id, kind="diary_entry_edit")
        .count()
        == 0
    )


def test_diary_edit_controls_of_another_type_name_no_controls(admin):  # noqa: F811
    _control(admin.db, "A.5.1")
    created = admin.call(
        "POST",
        "/v1/admin/diary",
        {"summary": "Original", "controls": ["A.5.1"], "framework": "A"},
    )
    event_id = uuid.UUID(created["event_id"])

    edited = admin.call("PATCH", f"/v1/admin/diary/{event_id}", {"controls": 7})

    # As when an entry is created, a value that is neither a list nor a
    # comma-separated string names no controls, so the entry keeps none.
    assert edited["manual_mappings_set"] == 0
    assert admin.db.query(Mapping).filter_by(event_id=event_id).count() == 0
