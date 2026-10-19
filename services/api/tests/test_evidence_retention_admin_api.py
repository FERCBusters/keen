"""Evidence retention administration through the assembled application.

Administrators read the retention status, preview what a policy would remove,
queue and cancel purges and make waiting storage clean-up due again. Purge work
itself runs in the worker and is covered by test_evidence_retention.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import Event
from app.services import evidence_retention as retention
from sqlalchemy import text

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

PATH = "/v1/admin/evidence-retention"
PURGE_ALL = {"mode": "all", "confirmation": "PURGE EVIDENCE"}


@pytest.fixture
def lab(keen, monkeypatch, tmp_path):  # noqa: F811 - fixture imported above
    if not keen.postgres:
        pytest.skip("Retention SQL and advisory locks require PostgreSQL")
    db = keen.db
    webhooks = tmp_path / "webhooks.yml"
    webhooks.write_text("providers:\n  synthetic:\n    secret_header: X-Synthetic\n")
    monkeypatch.setattr(settings, "webhooks_path", str(webhooks))
    # The release seed supplies this row; contract fixtures skip the seed.
    db.execute(
        text(
            "INSERT INTO evidence_retention_policy (id, mode) VALUES (1, 'disabled') "
            "ON CONFLICT (id) DO NOTHING"
        )
    )
    db.commit()
    admin_user = keen.user(role="admin")
    admin = keen.client(admin_user)

    def call(method, path="", body=None, status=200):
        response = admin.request(method, PATH + path, json=body, headers=unsafe(admin))
        assert response.status_code == status, response.text
        return response.json()

    def event(source, days_old, **fields):
        stamp = retention.now() - timedelta(days=days_old)
        row = Event(
            source=source,
            summary=f"{source} event",
            external_id=uuid.uuid4().hex,
            timestamp=stamp,
            created_at=stamp,
            **fields,
        )
        db.add(row)
        db.commit()
        return row

    def sql(statement, **params):
        return [dict(r) for r in db.execute(text(statement), params).mappings()]

    return SimpleNamespace(
        keen=keen,
        db=db,
        admin=admin,
        username=admin_user.username,
        call=call,
        event=event,
        sql=sql,
    )


def test_status_reports_the_policy_choices_jobs_and_storage_queue(lab):
    lab.event("ci", 90)
    lab.event("ci", 1)
    lab.db.execute(
        text(
            "INSERT INTO evidence_retention_overrides (scope, key, mode, value) "
            "VALUES ('source', 'ci', 'age', 30)"
        )
    )
    lab.db.execute(
        text(
            "INSERT INTO evidence_object_cleanup (storage_uri, last_error) VALUES "
            "('local://local/events/a.json', 'Storage deletion failed (AccessDenied)'),"
            "('local://local/events/b.json', NULL)"
        )
    )
    lab.db.commit()

    status = lab.call("GET")

    assert (status["policy"]["mode"], status["policy"]["value"]) == ("disabled", None)
    assert status["overrides"] == [
        {"scope": "source", "key": "ci", "mode": "age", "value": 30}
    ]
    sources = {item["key"]: item["label"] for item in status["choices"]["source"]}
    # Exact source types: those collected, built-in ones and webhook providers.
    assert (
        sources["ci"] == "ci" and sources["webhook:synthetic"] == "Webhook: synthetic"
    )
    assert "webhooks" not in sources
    assert status["jobs"] == []
    storage = status["storage"]
    assert (storage["pending_objects"], storage["waiting_objects"]) == (2, 1)
    assert storage["errors"] == ["Storage deletion failed (AccessDenied)"]
    assert (status["total_events"], status["eligible_events"]) == (2, 0)
    assert status["ingestion_pause"] == {"paused": False, "resume_at": None}


def test_previews_count_only_what_a_policy_would_remove(lab):
    lab.event("ci", 90)
    lab.event("ci", 40)
    lab.event("loki", 2)
    # Diary entries are permanent records, whatever the policy.
    lab.event("diary", 400)

    age = lab.call("POST", "/preview", {"mode": "age", "value": 30})
    assert (age["total_events"], age["eligible_events"]) == (4, 2)
    count = lab.call("POST", "/preview", {"mode": "count", "value": 1})
    assert count["eligible_events"] == 2
    # An override narrows the policy for its source type.
    kept = lab.call(
        "POST",
        "/preview",
        {
            "mode": "age",
            "value": 30,
            "overrides": [{"scope": "source", "key": "ci", "mode": "disabled"}],
        },
    )
    assert kept["eligible_events"] == 0
    everything = lab.call("POST", "/preview-all")
    assert everything["eligible_events"] == 3

    invented = lab.call(
        "POST",
        "/preview",
        {
            "mode": "age",
            "value": 30,
            "overrides": [
                {"scope": "source", "key": "no-such-source", "mode": "disabled"}
            ],
        },
        status=422,
    )
    assert invented["detail"].startswith(
        "Choose an existing source type or mapping rule"
    )


def test_a_purge_needs_a_policy_runs_one_at_a_time_and_can_be_cancelled(lab):
    lab.event("ci", 90)
    unset = lab.call(
        "POST", "/purge", {"mode": "policy", "confirmation": "PURGE EVIDENCE"}, 400
    )
    assert unset["detail"] == "Save an age or count policy first"
    unconfirmed = lab.call(
        "POST", "/purge", {"mode": "all", "confirmation": "purge evidence"}, 422
    )
    assert unconfirmed["detail"]
    assert lab.sql("SELECT id FROM evidence_purge_jobs") == []

    queued = lab.call("POST", "/purge", PURGE_ALL, status=202)
    assert queued["status"] == "queued"
    (job,) = lab.sql("SELECT * FROM evidence_purge_jobs")
    assert (str(job["id"]), job["mode"], job["status"]) == (
        queued["job_id"],
        "all",
        "queued",
    )
    assert (job["automatic"], job["requested_by"]) == (False, lab.username)
    # A manual purge pauses collection until it finishes.
    assert lab.call("GET")["ingestion_pause"]["paused"] is True
    busy = lab.call("POST", "/purge", PURGE_ALL, status=409)
    assert busy["detail"].startswith("A purge is already queued or running")

    assert lab.call("POST", "/cancel") == {"ok": True}
    assert [
        row["status"] for row in lab.sql("SELECT status FROM evidence_purge_jobs")
    ] == ["cancelled"]
    paused = lab.call("GET")["ingestion_pause"]
    assert paused["paused"] is True and paused["resume_at"]


def test_a_policy_purge_uses_the_saved_policy_and_a_policy_change_stops_it(lab):
    lab.call("PUT", "", {"mode": "count", "value": 5, "overrides": []})
    queued = lab.call(
        "POST", "/purge", {"mode": "policy", "confirmation": "PURGE EVIDENCE"}, 202
    )
    (job,) = lab.sql("SELECT id, mode, value, status FROM evidence_purge_jobs")
    assert (str(job["id"]), job["mode"], job["value"]) == (queued["job_id"], "count", 5)

    lab.call("PUT", "", {"mode": "age", "value": 30})
    (job,) = lab.sql("SELECT status FROM evidence_purge_jobs")
    assert job["status"] == "cancelled"
    # A manual purge of everything is independent of the policy.
    lab.call("POST", "/purge", PURGE_ALL, status=202)
    lab.call("PUT", "", {"mode": "count", "value": 10})
    assert sorted(
        (row["mode"], row["status"])
        for row in lab.sql("SELECT mode, status FROM evidence_purge_jobs")
    ) == [("all", "queued"), ("count", "cancelled")]


def test_waiting_storage_clean_up_can_be_made_due_now(lab):
    later = retention.now() + timedelta(hours=6)
    lab.db.execute(
        text(
            "INSERT INTO evidence_object_cleanup (storage_uri, next_attempt_at, last_error) "
            "VALUES ('local://local/events/a.json', :later, 'Storage deletion failed')"
        ),
        {"later": later},
    )
    lab.db.commit()
    before = retention.now()

    assert lab.call("POST", "/retry-storage") == {"ok": True}

    (row,) = lab.sql("SELECT next_attempt_at, last_error FROM evidence_object_cleanup")
    assert before - timedelta(seconds=5) <= row["next_attempt_at"] <= retention.now()
    # The recorded error stays until a retry succeeds.
    assert row["last_error"] == "Storage deletion failed"


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "", None),
        ("PUT", "", {"mode": "disabled"}),
        ("POST", "/preview", {"mode": "count", "value": 5}),
        ("POST", "/preview-all", None),
        ("POST", "/purge", PURGE_ALL),
        ("POST", "/cancel", None),
        ("POST", "/retry-storage", None),
    ],
)
def test_retention_controls_need_an_administrator(lab, method, path, body):
    reader = lab.keen.client(
        lab.keen.user("events.read", "audits.admin", "risk.manage")
    )
    refused = reader.request(method, PATH + path, json=body, headers=unsafe(reader))
    assert refused.status_code == 403
    assert lab.keen.client().request(method, PATH + path, json=body).status_code == 401
    if method != "GET":
        # Administrators still need the CSRF token.
        response = lab.admin.request(method, PATH + path, json=body)
        assert response.status_code == 403 and "CSRF" in response.text
    assert lab.sql("SELECT id FROM evidence_purge_jobs") == []
    assert lab.sql("SELECT mode FROM evidence_retention_policy") == [
        {"mode": "disabled"}
    ]
