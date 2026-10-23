"""Mapping-rule maintenance through the assembled application.

These operations decide which evidence counts toward a control after rules
change: previewing a rule, reviewing and pruning stale rule mappings, queueing,
inspecting and cancelling backfills, deleting rules, restoring configuration
revisions, exporting rules and sampling event fields. Requests pass through the
production middleware, password sign-in, CSRF and the administrator check.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from app.db.models import ControlItem, Event, Framework, Mapping, RuleBackfillJob
from app.ingest import common
from app.worker import rule_backfill, tasks

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    BASE,
    keen,  # noqa: F401
    unsafe,
)

FW = "fw-lifecycle"
FALLBACK_REASON = (
    "A structured field, collection, label, pattern or BookStack selector "
    "condition did not match."
)
# Every non-administrator permission that touches evidence or its review.
BROAD_PERMISSIONS = ("events.read", "isms.manage", "risk.manage", "audits.admin")


def _rule(rule_id="deploys", *, source="ci", refs=("A.1",), **when):
    return {
        "id": rule_id,
        "when": {"source": source, **when},
        "map_to": [{"framework": FW, "ref": ref, "kind": "control"} for ref in refs],
        "confidence": 0.8,
    }


@pytest.fixture
def lab(keen, monkeypatch):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add(Framework(slug=FW))
    controls = {}
    for ref in ("A.1", "A.2"):
        controls[ref] = ControlItem(
            framework_slug=FW, type="annex_control", ref=ref, title=ref
        )
        db.add(controls[ref])
    db.commit()

    # Record queued backfills instead of reaching a broker; a test runs the
    # worker batch in its own session when it needs one.
    queued = []
    monkeypatch.setattr(tasks.backfill_rule_task, "delay", queued.append)

    @contextmanager
    def worker_session():
        yield db

    monkeypatch.setattr(rule_backfill, "SessionLocal", worker_session)
    monkeypatch.setattr(rule_backfill, "clear_stats_caches", Mock())

    admin = keen.client(keen.user(role="admin"))
    minutes = iter(range(1, 100_000))

    def version():
        return admin.get("/v1/admin/mapping-rules").json()["version"]

    def rule_ids():
        return [r["id"] for r in admin.get("/v1/admin/mapping-rules").json()["items"]]

    def put(rule, *, apply_existing=False):
        response = admin.put(
            f"/v1/admin/mapping-rules/{rule['id']}",
            json={
                "version": version(),
                "rule": rule,
                "apply_existing": apply_existing,
            },
            headers=unsafe(admin),
        )
        assert response.status_code == 200, response.text
        return response.json()

    def event(action="deploy", *, source="ci", commit=True, **columns):
        values = {
            "system": "build",
            "outcome": "success",
            "severity": 2,
            "summary": f"{action} on {source}",
            "raw_pointer": {},
            "normalized_payload": {},
            **columns,
        }
        row = Event(
            # Assigned here so uncommitted events can be mapped in bulk.
            id=uuid.uuid4(),
            source=source,
            action=action,
            timestamp=datetime(2026, 3, 1) + timedelta(minutes=next(minutes)),
            external_id=uuid.uuid4().hex,
            **values,
        )
        db.add(row)
        if commit:
            db.commit()
        return row

    def mapping(ev, ref, *, method="rule", rule_id="deploys", commit=True):
        row = Mapping(
            event_id=ev.id,
            control_item_id=controls[ref].id,
            method=method,
            mapped_by="system" if method == "rule" else "auditor",
            confidence=0.8,
            rationale=(
                f"auto by rule {rule_id} ({FW})"
                if method == "rule"
                else "Reviewed by an auditor"
            ),
        )
        db.add(row)
        if commit:
            db.commit()
        return row

    def mapping_ids():
        db.expire_all()
        return {row.id for row in db.query(Mapping).all()}

    def mapped(ev):
        db.expire_all()
        return {
            ref
            for (ref,) in db.query(ControlItem.ref)
            .join(Mapping, Mapping.control_item_id == ControlItem.id)
            .filter(Mapping.event_id == ev.id)
        }

    return SimpleNamespace(
        db=db,
        admin=admin,
        queued=queued,
        version=version,
        rule_ids=rule_ids,
        put=put,
        event=event,
        mapping=mapping,
        mapping_ids=mapping_ids,
        mapped=mapped,
    )


# --- Saving ----------------------------------------------------------------------

UNREADABLE = [
    ("misspelt-key", {**_rule("typo"), "wen": {}}, "Unsupported key wen"),
    (
        "paused-misspelt-key",
        {**_rule("typo"), "enabled": False, "wen": {}},
        "Unsupported key wen",
    ),
    (
        "text-enabled",
        {**_rule("typo"), "enabled": "yes"},
        "Enabled must be true or false",
    ),
    (
        "role-gate",
        {
            **_rule("typo"),
            "map_to": [{"framework": FW, "ref": "A.1", "roles_any": ["ops"]}],
        },
        "Unsupported target key roles_any",
    ),
    (
        "text-confidence",
        {**_rule("typo"), "confidence": "0.5"},
        "Confidence must be a number from 0 to 1",
    ),
    (
        # Resuming a paused rule must not reveal a problem it was saved with.
        "paused-text-confidence",
        {**_rule("typo"), "enabled": False, "confidence": "0.5"},
        "Confidence must be a number from 0 to 1",
    ),
    (
        "paused-misspelt-condition",
        {**_rule("typo", sourse="loki"), "enabled": False},
        "Unsupported match fields: sourse",
    ),
]


@pytest.mark.parametrize(
    "entry,detail",
    [(entry, detail) for _, entry, detail in UNREADABLE],
    ids=[name for name, _, _ in UNREADABLE],
)
def test_a_rule_collection_would_skip_is_refused_when_saved(lab, entry, detail):
    before = lab.version()

    refused = lab.admin.put(
        "/v1/admin/mapping-rules/typo",
        json={"version": before, "rule": entry, "apply_existing": False},
        headers=unsafe(lab.admin),
    )

    assert (refused.status_code, refused.json()["detail"]) == (400, detail)
    assert lab.version() == before
    assert "typo" not in lab.rule_ids()


# --- Stale rule mappings -------------------------------------------------------


@pytest.fixture
def stale(lab):
    """A supported rule mapping, two stale ones, a manual one and another source."""
    lab.put(_rule(action="deploy"))
    deploy = lab.event("deploy")
    rollback = lab.event("rollback")
    elsewhere = lab.event("deploy", source="elsewhere")
    return SimpleNamespace(
        deploy=deploy,
        rollback=rollback,
        supported=lab.mapping(deploy, "A.1"),
        # The rule targets A.1 only.
        wrong_control=lab.mapping(deploy, "A.2"),
        # The rule asks for deploy events.
        no_longer_matches=lab.mapping(rollback, "A.1"),
        manual=lab.mapping(rollback, "A.2", method="manual"),
        # No rule covers this source, but it lies outside a review of "ci".
        other_source=lab.mapping(elsewhere, "A.2"),
    )


def _review(lab, source="ci", **params):
    response = lab.admin.get(
        "/v1/admin/stale-rule-mappings", params={"source": source, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _prune(lab, ids, *, version=None, source="ci", client=None, headers=None):
    client = client or lab.admin
    return client.post(
        "/v1/admin/stale-rule-mappings/prune",
        json={
            "version": lab.version() if version is None else version,
            "source": source,
            "mapping_ids": [str(i) for i in ids],
        },
        headers=unsafe(client) if headers is None else headers,
    )


def test_stale_review_lists_only_rule_mappings_no_current_rule_supports(lab, stale):
    review = _review(lab)

    assert sorted(review["stale"], key=lambda item: item["ref"]) == [
        {
            "mapping_id": str(stale.no_longer_matches.id),
            "event_id": str(stale.rollback.id),
            "summary": "rollback on ci",
            "framework": FW,
            "ref": "A.1",
        },
        {
            "mapping_id": str(stale.wrong_control.id),
            "event_id": str(stale.deploy.id),
            "summary": "deploy on ci",
            "framework": FW,
            "ref": "A.2",
        },
    ]
    # Manual mappings and other sources are never examined.
    assert review["examined"] == 3
    assert review["version"] == lab.version()
    assert review["next_offset"] is None


def test_prune_removes_exactly_the_reviewed_stale_mappings(lab, stale):
    review = _review(lab)
    response = _prune(
        lab, [item["mapping_id"] for item in review["stale"]], version=review["version"]
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 2}
    assert lab.mapping_ids() == {
        stale.supported.id,
        stale.manual.id,
        stale.other_source.id,
    }
    assert _review(lab)["stale"] == []


def test_prune_rechecks_every_id_and_removes_nothing_when_one_is_still_supported(
    lab, stale
):
    # A client can submit any IDs; the server re-evaluates all of them first.
    before = lab.mapping_ids()
    response = _prune(lab, [stale.no_longer_matches.id, stale.supported.id])

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "At least one mapping is now valid. Preview again."
    )
    assert lab.mapping_ids() == before


@pytest.mark.parametrize("submitted", ["manual", "other_source", "unknown"])
def test_prune_never_removes_manual_or_out_of_scope_mappings(lab, stale, submitted):
    target = uuid.uuid4() if submitted == "unknown" else getattr(stale, submitted).id
    before = lab.mapping_ids()
    response = _prune(lab, [stale.no_longer_matches.id, target])

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Mappings changed since the review. Preview again."
    )
    assert lab.mapping_ids() == before


def test_prune_refuses_a_review_made_before_the_rules_changed(lab, stale):
    review = _review(lab)
    # The edit makes the rollback mapping valid again.
    lab.put(_rule(action="rollback"))
    before = lab.mapping_ids()

    response = _prune(
        lab, [item["mapping_id"] for item in review["stale"]], version=review["version"]
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Rules changed since the review. Preview again."
    assert lab.mapping_ids() == before


@pytest.mark.parametrize(
    "submit,source,detail",
    [
        (lambda s: [], "ci", "Review 1-500 mapping IDs for a source first"),
        (
            lambda s: [uuid.uuid4() for _ in range(501)],
            "ci",
            "Review 1-500 mapping IDs for a source first",
        ),
        (
            lambda s: [s.no_longer_matches.id],
            "   ",
            "Review 1-500 mapping IDs for a source first",
        ),
        (lambda s: ["not-a-uuid"], "ci", "Invalid mapping ID"),
        (lambda s: [s.no_longer_matches.id] * 2, "ci", "Duplicate mapping IDs"),
    ],
    ids=["none", "too-many", "blank-source", "malformed", "duplicate"],
)
def test_prune_rejects_malformed_requests_before_deleting(
    lab, stale, submit, source, detail
):
    before = lab.mapping_ids()

    response = _prune(lab, submit(stale), source=source)

    assert response.status_code == 400
    assert response.json()["detail"] == detail
    assert lab.mapping_ids() == before


@pytest.mark.parametrize(
    "params",
    [{"source": ""}, {"source": "x" * 129}, {"offset": -1}, {"offset": 100_001}],
    ids=["blank-source", "long-source", "negative-offset", "huge-offset"],
)
def test_stale_review_rejects_unbounded_requests(lab, params):
    response = lab.admin.get(
        "/v1/admin/stale-rule-mappings", params={"source": "ci", **params}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Choose a source and an offset between 0 and 100000"
    )


def test_stale_review_pages_through_every_mapping_without_overlap(lab):
    lab.put(_rule(action="deploy"))
    # 501 stale mappings: one more than a page.
    for _ in range(501):
        lab.mapping(lab.event("rollback", commit=False), "A.1", commit=False)
    lab.db.commit()

    first = _review(lab)
    assert (first["examined"], first["next_offset"]) == (500, 500)
    second = _review(lab, offset=first["next_offset"])
    assert (second["examined"], second["next_offset"]) == (1, None)

    seen = [item["mapping_id"] for item in first["stale"] + second["stale"]]
    assert len(seen) == len(set(seen)) == 501
    assert set(seen) == {str(i) for i in lab.mapping_ids()}


def test_stale_mapping_maintenance_requires_an_administrator(keen, lab, stale):  # noqa: F811 - fixture imported above
    reader = keen.client(keen.user(*BROAD_PERMISSIONS))
    review = reader.get("/v1/admin/stale-rule-mappings", params={"source": "ci"})
    assert review.status_code == 403
    before = lab.mapping_ids()

    assert _prune(lab, [stale.no_longer_matches.id], client=reader).status_code == 403
    # An administrator's session cookie alone cannot prune: the CSRF token and
    # the request origin are both checked.
    missing_token = _prune(lab, [stale.no_longer_matches.id], headers={"Origin": BASE})
    foreign_origin = _prune(
        lab,
        [stale.no_longer_matches.id],
        headers={**unsafe(lab.admin), "Origin": "https://attacker.example"},
    )
    for response in (missing_token, foreign_origin):
        assert response.status_code == 403
        assert response.json()["detail"] == "CSRF token missing or invalid"
    assert lab.mapping_ids() == before


# --- Backfill jobs -------------------------------------------------------------


def _start(lab, rule_id="deploys", client=None):
    client = client or lab.admin
    return client.post(
        f"/v1/admin/mapping-rules/{rule_id}/backfill", headers=unsafe(client)
    )


def _cancel(lab, job_id, client=None):
    client = client or lab.admin
    return client.post(
        f"/v1/admin/rule-backfills/{job_id}/cancel", headers=unsafe(client)
    )


def test_backfill_jobs_can_be_inspected_listed_and_cancelled_before_they_map(lab):
    lab.put(_rule(action="deploy"))
    deploy = lab.event("deploy")

    started = _start(lab).json()["backfill"]
    assert started["status"] == "queued"
    assert lab.queued == [started["id"]]
    # Starting again while the job waits reuses it rather than queueing twice.
    assert _start(lab).json()["backfill"]["id"] == started["id"]
    assert lab.queued == [started["id"]]

    job = lab.admin.get(f"/v1/admin/rule-backfills/{started['id']}").json()
    assert job == {
        "id": started["id"],
        "rule_id": "deploys",
        "source": "ci",
        "status": "queued",
        "total_estimate": 0,
        "examined": 0,
        "matched": 0,
        "created_mappings": 0,
        "error": None,
    }
    listed = lab.admin.get("/v1/admin/rule-backfills", params={"rule_id": "deploys"})
    assert [item["id"] for item in listed.json()["items"]] == [started["id"]]
    other = lab.admin.get("/v1/admin/rule-backfills", params={"rule_id": "rollbacks"})
    assert other.json()["items"] == []

    assert _cancel(lab, started["id"]).json() == {"status": "cancelled"}
    # The worker then leaves evidence untouched, even though the event matches.
    assert rule_backfill.process_batch(uuid.UUID(started["id"])) is False
    assert lab.mapped(deploy) == set()
    assert _cancel(lab, started["id"]).json() == {"status": "cancelled"}

    # A cancelled job no longer blocks a fresh run.
    restarted = _start(lab).json()["backfill"]
    assert restarted["status"] == "queued" and restarted["id"] != started["id"]
    assert lab.queued == [started["id"], restarted["id"]]


def test_a_completed_backfill_keeps_its_mappings_when_a_cancel_arrives_late(lab):
    lab.put(_rule(action="deploy"))
    deploy = lab.event("deploy")
    lab.event("rollback")
    job_id = _start(lab).json()["backfill"]["id"]

    assert rule_backfill.process_batch(uuid.UUID(job_id)) is False
    assert _cancel(lab, job_id).json() == {"status": "completed"}

    assert lab.mapped(deploy) == {"A.1"}
    job = lab.admin.get(f"/v1/admin/rule-backfills/{job_id}").json()
    assert {k: job[k] for k in ("status", "total_estimate", "examined")} == {
        "status": "completed",
        "total_estimate": 2,
        "examined": 2,
    }
    assert (job["matched"], job["created_mappings"]) == (1, 1)


def test_backfills_start_only_for_enabled_rules_with_evidence_to_examine(lab):
    lab.event("deploy")
    lab.put({**_rule("paused", action="deploy"), "enabled": False})
    lab.put(_rule("quiet", source="nothing-collected-yet"))

    paused = _start(lab, "paused")
    assert paused.status_code == 400
    assert paused.json()["detail"] == (
        "Enable this rule before applying it to older evidence"
    )
    missing = _start(lab, "missing")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Rule not found"
    quiet = _start(lab, "quiet")
    assert quiet.status_code == 200
    assert quiet.json() == {
        "backfill": {"status": "no_previous_evidence", "total_estimate": 0}
    }

    assert lab.db.query(RuleBackfillJob).count() == 0
    assert lab.queued == []


def test_unknown_backfill_jobs_are_reported_without_side_effects(lab):
    for job_id in ("not-a-uuid", str(uuid.uuid4())):
        response = lab.admin.get(f"/v1/admin/rule-backfills/{job_id}")
        assert response.status_code == 404
        assert response.json()["detail"] == "Backfill not found"
    malformed = _cancel(lab, "not-a-uuid")
    assert malformed.status_code == 400
    assert malformed.json()["detail"] == "Invalid job ID"
    assert _cancel(lab, uuid.uuid4()).status_code == 404


def test_backfill_controls_require_an_administrator(keen, lab):  # noqa: F811 - fixture imported above
    lab.put(_rule(action="deploy"))
    lab.event("deploy")
    job_id = _start(lab).json()["backfill"]["id"]
    reader = keen.client(keen.user(*BROAD_PERMISSIONS))

    assert reader.get(f"/v1/admin/rule-backfills/{job_id}").status_code == 403
    assert reader.get("/v1/admin/rule-backfills").status_code == 403
    assert _cancel(lab, job_id, client=reader).status_code == 403
    assert _start(lab, client=reader).status_code == 403

    lab.db.expire_all()
    assert lab.db.get(RuleBackfillJob, uuid.UUID(job_id)).status == "queued"
    assert lab.queued == [job_id]


# --- Previewing a rule ---------------------------------------------------------


def _preview(lab, rule, status=200):
    response = lab.admin.post(
        "/v1/admin/mapping-rules/preview",
        json={"version": lab.version(), "rule": rule},
        headers=unsafe(lab.admin),
    )
    assert response.status_code == status, response.text
    return response.json()


def test_preview_examines_only_the_rule_source_and_explains_each_mismatch(lab):
    by_bot = {lab.event("deploy", actor="ci-bot"), lab.event("deploy", actor="ci-bot")}
    failed = lab.event("rollback", outcome="failure")
    anonymous = lab.event("deploy")
    lab.event("deploy", source="elsewhere", actor="ci-bot")
    version = lab.version()

    body = _preview(lab, _rule(action="deploy", outcome="success", actor="ci-bot"))

    # The other source is never examined.
    assert (body["examined"], body["matched"]) == (4, 2)
    assert {item["event_id"] for item in body["examples"]} == {
        str(row.id) for row in by_bot
    }
    assert all(item["targets"] == {FW: ["A.1"]} for item in body["examples"])
    reasons = {item["event_id"]: item["reasons"] for item in body["nonmatches"]}
    assert reasons == {
        str(failed.id): [
            "Person/account: the rule asks for 'ci-bot'; "
            "this event has 'not supplied'.",
            "Activity: the rule asks for 'deploy'; this event has 'rollback'.",
            "Result: the rule asks for 'success'; this event has 'failure'.",
        ],
        str(anonymous.id): [
            "Person/account: the rule asks for 'ci-bot'; "
            "this event has 'not supplied'.",
        ],
    }
    # Previewing saves nothing, queues nothing and maps nothing.
    assert lab.version() == version and lab.rule_ids() == []
    assert lab.queued == [] and lab.mapping_ids() == set()


def test_preview_explains_structured_field_mismatches(lab):
    monitord = lab.event(
        "ossec.alert",
        normalized_payload={"fields": {"ossec.location": "ossec-monitord"}},
    )
    syscheck = lab.event(
        "ossec.alert", normalized_payload={"fields": {"ossec.location": "syscheck"}}
    )
    condition = {
        "path": ["fields", "ossec.location"],
        "operator": "equals",
        "value": "ossec-monitord",
    }

    body = _preview(lab, _rule(action="ossec.alert", fields=[condition]))

    assert [item["event_id"] for item in body["examples"]] == [str(monitord.id)]
    assert body["nonmatches"] == [
        {
            "event_id": str(syscheck.id),
            "summary": "ossec.alert on ci",
            "system": "build",
            "action": "ossec.alert",
            "outcome": "success",
            "reasons": [FALLBACK_REASON],
        }
    ]


def test_preview_of_a_paused_rule_examines_nothing(lab):
    lab.event("deploy")
    body = _preview(lab, {**_rule(action="deploy"), "enabled": False})
    assert body == {
        "examined": 0,
        "matched": 0,
        "examples": [],
        "nonmatches": [],
        "note": "Rule is paused; enable it to preview matches.",
    }


def test_preview_refuses_unknown_targets_without_creating_placeholder_controls(lab):
    lab.event("deploy")
    body = _preview(lab, _rule(action="deploy", refs=("Z.9",)), status=400)
    assert body["detail"] == f"Unknown control: {FW} / Z.9"
    refs = lab.db.query(ControlItem.ref).filter_by(framework_slug=FW)
    assert {ref for (ref,) in refs} == {"A.1", "A.2"}


# --- Deleting rules and restoring revisions -----------------------------------


def _delete(lab, rule_id, version):
    return lab.admin.delete(
        f"/v1/admin/mapping-rules/{rule_id}",
        params={"version": version},
        headers=unsafe(lab.admin),
    )


def test_deleting_a_rule_needs_the_current_version_and_stops_future_mapping(lab):
    lab.put(_rule("deploys", action="deploy"))
    lab.put(_rule("rollbacks", action="rollback", refs=("A.2",)))
    current = lab.version()

    assert _delete(lab, "deploys", current - 1).status_code == 409
    missing = _delete(lab, "missing", current)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Rule not found"
    assert lab.rule_ids() == ["deploys", "rollbacks"]

    deleted = _delete(lab, "deploys", current)
    assert deleted.status_code == 200 and deleted.json() == {"version": current + 1}
    assert lab.rule_ids() == ["rollbacks"]

    # New evidence follows the remaining rule only.
    deploy, rollback = lab.event("deploy"), lab.event("rollback")
    assert common.apply_rules(lab.db, deploy) == 0
    assert common.apply_rules(lab.db, rollback) == 1
    lab.db.commit()
    assert lab.mapped(deploy) == set() and lab.mapped(rollback) == {"A.2"}


def test_restoring_a_rules_revision_replaces_the_document_once_versions_agree(lab):
    lab.put(_rule("deploys", action="deploy"))
    first = lab.version()
    lab.put(_rule("rollbacks", action="rollback"))
    current = lab.version()

    def revisions():
        listing = lab.admin.get("/v1/admin/configuration-revisions/rules")
        return [item["version"] for item in listing.json()["items"]]

    def restore(revision, version, name="rules"):
        return lab.admin.post(
            f"/v1/admin/configuration-revisions/{name}/{revision}/restore",
            json={"version": version},
            headers=unsafe(lab.admin),
        )

    assert revisions() == [current, first]
    assert restore(first, current - 1).status_code == 409
    assert lab.rule_ids() == ["deploys", "rollbacks"]

    restored = restore(first, current)
    assert restored.status_code == 200 and restored.json() == {"version": current + 1}
    assert lab.rule_ids() == ["deploys"]
    # The restore is itself a revision, so it can be undone.
    assert revisions() == [current + 1, current, first]

    missing = restore(99, current + 1)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Revision not found"
    unknown = restore(1, current + 1, name="no-such-configuration")
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "Unknown configuration"
    listing = lab.admin.get("/v1/admin/configuration-revisions/no-such-configuration")
    assert listing.status_code == 404
    assert lab.version() == current + 1


# --- Export and event samples --------------------------------------------------


def _export(lab, source=None):
    params = {} if source is None else {"source": source}
    return lab.admin.get("/v1/admin/mapping-rules/export.yaml", params=params)


def test_rule_export_is_scoped_to_one_source(lab):
    lab.put(_rule("deploys", action="deploy"))
    lab.put(_rule("elsewhere", source="elsewhere"))

    scoped = _export(lab, "ci")
    assert scoped.status_code == 200
    assert [r["id"] for r in yaml.safe_load(scoped.text)["rules"]] == ["deploys"]
    assert scoped.headers["content-disposition"] == (
        'attachment; filename="keen-rules-ci.yaml"'
    )
    assert scoped.headers["cache-control"] == "no-store"

    everything = _export(lab)
    assert [r["id"] for r in yaml.safe_load(everything.text)["rules"]] == [
        "deploys",
        "elsewhere",
    ]
    assert everything.headers["content-disposition"] == (
        'attachment; filename="keen-rules-all.yaml"'
    )
    assert _export(lab, "x" * 257).status_code == 400


def test_rule_export_filename_cannot_carry_header_or_path_syntax(lab):
    hostile = _export(lab, 'ci"\r\nSet-Cookie: session=1; x=../../etc')

    assert hostile.status_code == 200
    assert hostile.headers["content-disposition"] == (
        'attachment; filename="keen-rules-ci___Set-Cookie__session_1__x_______etc.yaml"'
    )
    assert "session=1" not in hostile.headers.get("set-cookie", "")
    assert yaml.safe_load(hostile.text) == {"rules": []}


def test_event_samples_list_sources_then_expose_one_source_fields(lab):
    lab.event(
        "ossec.alert",
        normalized_payload={"fields": {"ossec.location": "ossec-monitord"}},
    )
    lab.event("deploy", source="elsewhere")

    catalogue = lab.admin.get("/v1/admin/evidence-event-samples").json()
    assert catalogue["items"] == []
    assert {"ci", "elsewhere"} <= set(catalogue["sources"])
    assert catalogue["sources"] == sorted(catalogue["sources"])

    response = lab.admin.get(
        "/v1/admin/evidence-event-samples", params={"source": "ci"}
    )
    (sample,) = response.json()["items"]
    assert (sample["source"], sample["action"]) == ("ci", "ossec.alert")
    assert sample["fields"] == [
        {"path": ["fields", "ossec.location"], "value": "ossec-monitord"}
    ]

    for collector in ("not json", '["rss", "feeds", "x"]'):
        response = lab.admin.get(
            "/v1/admin/evidence-event-samples",
            params={"source": "ci", "collector": collector},
        )
        assert response.status_code == 400, collector
        assert response.json()["detail"] == "Invalid collection item"


# --- Backfill queueing edge cases ----------------------------------------------


def test_saving_a_paused_rule_queues_no_backfill_even_when_asked(lab):
    """A paused rule maps nothing, so applying it to older evidence is skipped."""
    lab.event("deploy")

    saved = lab.put({**_rule(action="deploy"), "enabled": False}, apply_existing=True)

    assert saved["backfill"] is None
    assert lab.db.query(RuleBackfillJob).count() == 0
    assert lab.queued == []


def test_a_queued_backfill_survives_an_unavailable_broker(lab, monkeypatch):
    """The job row is committed before the broker is asked; beat retries it later."""
    lab.event("deploy")

    def unavailable(job_id):
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.backfill_rule_task, "delay", unavailable)

    saved = lab.put(_rule(action="deploy"), apply_existing=True)

    assert saved["backfill"]["status"] == "queued"
    job = lab.db.get(RuleBackfillJob, uuid.UUID(saved["backfill"]["id"]))
    assert (job.status, job.rule_id) == ("queued", "deploys")
    listed = lab.admin.get("/v1/admin/rule-backfills").json()["items"]
    assert [item["id"] for item in listed] == [saved["backfill"]["id"]]


def test_preview_samples_are_bounded_to_ten_matches_and_ten_mismatches(lab):
    """Previews examine up to 200 events and show ten examples of each outcome."""
    for _ in range(12):
        lab.event("deploy", commit=False)
        lab.event("rollback", commit=False)
    lab.db.commit()

    body = _preview(lab, _rule(action="deploy"))

    assert (body["examined"], body["matched"]) == (24, 12)
    assert len(body["examples"]) == 10
    assert len(body["nonmatches"]) == 10
    assert {item["action"] for item in body["nonmatches"]} == {"rollback"}


def test_rule_history_with_duplicate_ids_cannot_be_restored(lab):
    """Recorded snapshots holding one ID twice are resolved before a restore."""
    from app.db.models import ManagedConfigurationRevision

    lab.put(_rule("deploys", action="deploy"))
    current = lab.version()
    duplicated = {
        "rules": [
            {**_rule("deploys", action="deploy"), "enabled": True},
            {**_rule("deploys", action="rollback"), "enabled": True},
        ]
    }
    lab.db.add(
        ManagedConfigurationRevision(name="rules", version=0, document=duplicated)
    )
    lab.db.commit()

    history = lab.admin.get("/v1/admin/mapping-rules/deploys/revisions")
    restore = lab.admin.post(
        f"/v1/admin/mapping-rules/deploys/revisions/{current}/restore",
        json={"version": current},
        headers=unsafe(lab.admin),
    )

    for response in (history, restore):
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "Duplicate rule IDs in recorded history; resolve before restoring"
        )
    assert lab.version() == current
