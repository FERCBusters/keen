"""Worker task contracts: session pairing, fanout payloads and backfill flow."""

import contextlib
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.worker import tasks as wt

RESULT = [{"ok": True}]

INGEST_TASKS = [
    ("ingest_elasticsearch_all_task", "ingest_elasticsearch_all"),
    ("ingest_loki_all_task", "ingest_loki_all"),
    ("ingest_cloudwatch_logs_all_task", "ingest_cloudwatch_logs_all"),
    ("ingest_github_all_task", "ingest_github_all"),
    ("ingest_forgejo_all_task", "ingest_forgejo_all"),
    ("ingest_jenkins_all_task", "ingest_jenkins_all"),
    ("ingest_taiga_all_task", "ingest_taiga_all"),
    ("ingest_bookstack_all_task", "ingest_bookstack_all"),
    ("ingest_rss_all_task", "ingest_rss_all"),
    ("ingest_google_workspace_all_task", "ingest_google_workspace_all"),
    ("ingest_gitea_all_task", "ingest_gitea_all"),
    ("ingest_gitlab_all_task", "ingest_gitlab_all"),
    ("ingest_redmine_all_task", "ingest_redmine_all"),
    ("ingest_riskledger_all_task", "ingest_riskledger_all"),
]

MATERIALISE_TASKS = [
    ("materialize_scheduled_audits_task", "materialize_due_scheduled_audits"),
    (
        "materialize_missing_monthly_zero_metrics_task",
        "materialize_missing_monthly_zero_metrics",
    ),
]

THREAD = MagicMock(
    id="7f28e6a8-1111-4a2b-9c3d-5e6f70819203",
    event_id="9c01be44-4444-4d5e-af6a-819203456778",
    status="open",
    created_by_user_id="550e8400-e29b-41d4-a716-446655440001",
    created_at=datetime(2026, 10, 9, 12, 0),
    updated_at=datetime(2026, 10, 9, 13, 0),
    last_admin_reply_at=datetime(2026, 10, 9, 14, 0),
)
POST = MagicMock(
    id="aa11ac22-2222-4b3c-8d4e-6f7081920345",
    thread_id="7f28e6a8-1111-4a2b-9c3d-5e6f70819203",
    author_user_id="bb22ad33-3333-4c4d-9e5f-708192034560",
    body="what happened?",
)
EVENT = MagicMock(
    id="e1",
    timestamp=datetime(2026, 10, 9, 12, 30),
    source="taiga",
    system="prod",
    actor="alice",
    action="build",
    outcome="info",
    severity=3,
    summary="A thing happened",
)


USER = MagicMock(username="alice")


def control(c_ref="A.9.1"):
    return MagicMock(
        framework_slug="KEEN-AF:1.0",
        type="annex_control",
        ref=c_ref,
        title="T",
        in_scope=True,
    )


@contextlib.contextmanager
def running_session(dbs):
    """Patch SessionLocal to hand out pre-made fake sessions.

    The fake sessions support both `SessionLocal()` and
    `with SessionLocal() as db:` call sites used by the tasks.
    """

    created: list = []
    pool = list(dbs)

    def make_session():
        db = pool.pop(0) if pool else MagicMock()
        db.__enter__.return_value = db
        db.__exit__.return_value = False
        created.append(db)
        return db

    with patch.object(wt, "SessionLocal", make_session):
        yield created


def make_db(model_results, *, join_rows=None):
    """Mock db whose .query(Model) chains resolve per-model first() values.

    model_results maps a model class name to either a scalar `.first()`
    value or a list consumed in call order; `join_rows` feeds the joined
    attachment/artifact query; `ControlItem_rows` feeds both control queries.
    """
    consumed: dict[str, int] = {}

    def query(model, *extra_models):
        chain = MagicMock()
        name = getattr(model, "__name__", None)
        if extra_models or (name and "Attachment" in name):
            joined = chain.join.return_value.filter.return_value.order_by
            joined.return_value.all.return_value = join_rows or []
            return chain
        result = model_results.get(name)
        import sys as _sys

        print("Q", name, "got", bool(result), file=_sys.stderr)
        if isinstance(result, list):
            index = consumed.setdefault(name, 0)
            consumed[name] += 1
            value = result[min(index, len(result) - 1)]
        else:
            value = result
        chain.filter.return_value.first.return_value = value
        if name == "ControlItem":
            control_rows = model_results.get("ControlItem_rows") or []
            base = chain.join.return_value.filter.return_value
            base.filter.return_value.order_by.return_value.all.return_value = (
                control_rows
            )
            base.filter.return_value.filter.return_value.order_by.return_value.all.return_value = control_rows
            base.order_by.return_value.all.return_value = control_rows
        return chain

    db = MagicMock()
    db.query.side_effect = query
    return db


def fake_webhook_config(enabled=True):
    config = MagicMock()
    config.any_enabled.return_value = enabled
    return config


@contextlib.contextmanager
def webhook_harness(config):
    """Patch from_settings and expose capture slots to callers."""
    stack = contextlib.ExitStack()
    stack.enter_context(
        patch.object(
            wt.OutboundWebhookConfig,
            "from_settings",
            classmethod(lambda cls, **kw: config),
        )
    )
    links = stack.enter_context(
        patch.object(wt, "build_question_links", return_value={"event": "/e"})
    )
    exported = {"links": links, "config": config}
    with stack:
        yield exported


def feature_gate(enabled=True):
    state_obj = MagicMock()
    state_obj.enabled.return_value = enabled
    return state_obj


# Tasks using `with SessionLocal() as db:` close via __exit__; the rest
# close explicitly in a finally block.
WITH_BLOCK_TASKS = {
    "ingest_elasticsearch_all_task",
    "ingest_gitea_all_task",
    "ingest_gitlab_all_task",
    "ingest_redmine_all_task",
    "ingest_riskledger_all_task",
}


@pytest.mark.parametrize("task_name,fn_name", INGEST_TASKS)
def test_ingest_tasks_bound_sessions(task_name, fn_name):
    with (
        running_session([]) as sessions,
        patch.object(wt, fn_name, return_value=RESULT) as ingest,
    ):
        task = getattr(wt, task_name)
        output = task()
    assert output == RESULT
    ingest.assert_called_once_with(sessions[0])
    if task_name in WITH_BLOCK_TASKS:
        assert sessions[0].__exit__.call_count == 1
    else:
        sessions[0].close.assert_called_once()


@pytest.mark.parametrize("task_name,fn_name", MATERIALISE_TASKS)
def test_materialise_tasks_close_session(task_name, fn_name):
    with (
        running_session([]) as sessions,
        patch.object(wt, fn_name, return_value={"created": 1}),
    ):
        task = getattr(wt, task_name)
        assert task() == {"created": 1}
    sessions[0].close.assert_called_once()


def test_refresh_event_counter_cache_task():
    with (
        running_session([]) as sessions,
        patch.object(
            wt, "refresh_framework_event_counts_cache", return_value=["F1"]
        ) as refresh,
    ):
        assert wt.refresh_event_counter_cache_task() == {"frameworks": ["F1"]}
    refresh.assert_called_once_with(sessions[0])


def test_send_question_webhooks_disabled_early():
    with webhook_harness(fake_webhook_config(enabled=False)):
        assert wt.send_question_webhooks_task("t", "p") == {"enabled": False}
        assert wt.send_question_reply_webhooks_task("t", "p") == {"enabled": False}


def test_send_question_webhooks_invalid_ids():
    with webhook_harness(fake_webhook_config(enabled=True)):
        for task in (
            wt.send_question_webhooks_task,
            wt.send_question_reply_webhooks_task,
        ):
            assert task("not-a-uuid", "p") == {
                "enabled": False,
                "error": "invalid ids",
            }
            assert task(None, "p") == {"enabled": False, "error": "invalid ids"}


def test_send_question_webhooks_task_full_flow():
    config = fake_webhook_config()
    feature_state = feature_gate(True)
    user = MagicMock(username="alice")
    db = make_db(
        {
            "EventQuestionThread": THREAD,
            "EventQuestionPost": POST,
            "Event": EVENT,
            "User": [user],
            "ControlItem_rows": [control()],
        },
        join_rows=[
            (
                MagicMock(post_id="p1", created_at=datetime(2026, 10, 1)),
                MagicMock(
                    id="a1",
                    meta={"filename": "report.pdf"},
                    content_type="application/pdf",
                    size_bytes=120,
                ),
            )
        ],
    )
    built: dict = {}

    with (
        running_session([db]),
        patch("app.services.features.state", lambda d: feature_state),
        webhook_harness(config),
        patch.object(
            wt, "send_question_created_webhooks", return_value={"enabled": True}
        ) as send_created,
        patch.object(
            wt, "build_generic_payload", side_effect=lambda **kw: built.update(kw) or kw
        ),
    ):
        result = wt.send_question_webhooks_task(THREAD.id, POST.id)
    assert result == {"enabled": True}
    send_created.assert_called_once_with(config, built)
    assert built["event"]["summary"] == "A thing happened"
    assert built["event"]["source"] == "taiga"
    assert built["thread"]["created_by_username"] == "alice"
    assert built["thread"]["status"] == "open"
    assert built["post"]["body"] == "what happened?"
    assert built["post"]["author_username"] == "alice"
    assert built["post"]["attachments"] == [
        {
            "artifact_id": "a1",
            "filename": "report.pdf",
            "content_type": "application/pdf",
            "size_bytes": 120,
            "download_url": "/api/v1/artifacts/a1/download",
        }
    ]
    assert built["controls"] == [
        {
            "framework_slug": "KEEN-AF:1.0",
            "type": "annex_control",
            "ref": "A.9.1",
            "title": "T",
            "in_scope": True,
        }
    ]
    assert built["links"] == {"event": "/e"}
    db.close.assert_called_once()


def test_send_question_reply_webhooks_task_full_flow():
    config = fake_webhook_config()
    feature_state = feature_gate(True)
    poster = MagicMock(username="bob", role="admin")
    db = make_db(
        {
            "EventQuestionThread": THREAD,
            "EventQuestionPost": POST,
            "Event": EVENT,
            "User": [USER, poster],
            "ControlItem_rows": [],
        },
        join_rows=[],
    )
    built: dict = {}

    with (
        running_session([db]),
        patch("app.services.features.state", lambda d: feature_state),
        webhook_harness(config),
        patch.object(
            wt, "send_question_reply_webhooks", return_value={"enabled": True}
        ) as send_reply,
        patch.object(
            wt, "build_generic_payload", side_effect=lambda **kw: built.update(kw) or kw
        ),
    ):
        result = wt.send_question_reply_webhooks_task(THREAD.id, POST.id)
    assert result == {"enabled": True}
    assert built["event_type"] == "keen.question.replied"
    assert built["extra"]["reply"] == {"as": "admin"}
    assert built["post"]["author_username"] == "bob"
    assert built["thread"]["created_by_username"] == "alice"
    assert built["thread"]["last_admin_reply_at"].startswith("2026-10-09T14:")
    send_reply.assert_called_once()


def test_send_question_webhooks_reply_role_falls_back_to_author():
    config = fake_webhook_config()
    feature_state = feature_gate(True)
    poster = MagicMock(username="bob", role="COPYIST")
    db = make_db(
        {
            "EventQuestionThread": THREAD,
            "EventQuestionPost": POST,
            "Event": EVENT,
            "User": [MagicMock(username="alice"), poster],
            "ControlItem_rows": [],
        },
        join_rows=[],
    )
    built: dict = {}
    with (
        running_session([db]),
        patch("app.services.features.state", lambda d: feature_state),
        webhook_harness(config),
        patch.object(wt, "send_question_reply_webhooks", return_value={}),
        patch.object(
            wt, "build_generic_payload", side_effect=lambda **kw: built.update(kw) or kw
        ),
    ):
        wt.send_question_reply_webhooks_task(THREAD.id, POST.id)
    assert built["extra"]["reply"] == {"as": "author"}


def test_send_question_webhooks_features_gate():
    config = fake_webhook_config()
    feature_state = feature_gate(False)
    db = make_db({"EventQuestionThread": None})
    with (
        running_session([db]),
        patch("app.services.features.state", lambda d: feature_state),
        webhook_harness(config),
    ):
        for task in (
            wt.send_question_webhooks_task,
            wt.send_question_reply_webhooks_task,
        ):
            assert task(THREAD.id, POST.id) == {"enabled": False}


def test_send_question_webhooks_missing_thread_or_post():
    config = fake_webhook_config()
    feature_state = feature_gate(True)
    db = make_db({"EventQuestionThread": THREAD, "EventQuestionPost": None})
    with (
        running_session([db]),
        patch("app.services.features.state", lambda d: feature_state),
        webhook_harness(config),
    ):
        assert wt.send_question_webhooks_task(THREAD.id, POST.id) == {
            "enabled": False,
            "error": "thread/post not found",
        }


def test_send_question_webhooks_fanout_failures_are_best_effort():
    config = fake_webhook_config()
    feature_state = feature_gate(True)
    for task, send_attr in (
        (wt.send_question_webhooks_task, "send_question_created_webhooks"),
        (wt.send_question_reply_webhooks_task, "send_question_reply_webhooks"),
    ):
        db = make_db(
            {"EventQuestionThread": THREAD, "EventQuestionPost": POST, "Event": EVENT}
        )
        with (
            running_session([db]),
            patch("app.services.features.state", lambda d: feature_state),
            webhook_harness(config),
            patch.object(wt, send_attr, side_effect=OSError("down")),
        ):
            result = task(THREAD.id, POST.id)
        assert result == {
            "enabled": True,
            "error": "Webhook delivery failed; check connectivity and configuration",
        }


def test_backfill_rule_task_short_batch_completion():
    dbs = [MagicMock()]
    with (
        running_session(dbs),
        patch("app.worker.rule_backfill.process_batch", return_value=False) as batch,
        patch.object(wt.backfill_rule_task, "delay") as requeue,
    ):
        wt.backfill_rule_task("11111111-1111-1111-1111-111111111111")
    batch.assert_called_once()
    requeue.assert_not_called()
    dbs[0].get.assert_not_called()


def test_backfill_rule_task_requeues_after_batches():
    dbs = [MagicMock()]
    calls = {"count": 0}

    def batch(_id):
        calls["count"] += 1
        return True

    with (
        running_session(dbs),
        patch("app.worker.rule_backfill.process_batch", side_effect=batch),
        patch.object(wt.backfill_rule_task, "delay") as requeue,
    ):
        wt.backfill_rule_task("11111111-1111-1111-1111-111111111111")
    assert calls["count"] == 20
    requeue.assert_called_once_with("11111111-1111-1111-1111-111111111111")


def test_backfill_rule_task_failures_mark_the_job_failed():
    dbs = [MagicMock()]
    job = MagicMock(status="running")
    dbs[0].get.return_value = job
    with (
        running_session(dbs),
        patch(
            "app.worker.rule_backfill.process_batch",
            side_effect=RuntimeError("rule exploded"),
        ),
        patch.object(wt.backfill_rule_task, "delay") as requeue,
    ):
        wt.backfill_rule_task("11111111-1111-1111-1111-111111111111")
    assert job.status == "failed"
    assert "check the saved rule" in job.error
    dbs[0].commit.assert_called_once()
    requeue.assert_not_called()

    # Terminal jobs are left alone.
    dbs2 = [MagicMock()]
    done_job = MagicMock(status="completed")
    dbs2[0].get.return_value = done_job
    with (
        running_session(dbs2),
        patch(
            "app.worker.rule_backfill.process_batch", side_effect=RuntimeError("boom")
        ),
    ):
        wt.backfill_rule_task("11111111-1111-1111-1111-111111111111")
    assert done_job.status == "completed"
    dbs2[0].commit.assert_not_called()


def test_recover_rule_backfills_task_requeues():
    with (
        patch.object(wt.backfill_rule_task, "delay") as requeue,
        patch(
            "app.worker.rule_backfill.recover_jobs",
            lambda *a, **kw: [uuid.UUID("22222222-2222-2222-2222-222222222222")],
        ),
    ):
        wt.recover_rule_backfills_task()
    requeue.assert_called_once_with("22222222-2222-2222-2222-222222222222")


def test_integration_tasks_delegate():
    with patch("app.integrations.runtime.run_job") as run_job:
        wt.integration_run_task("run-1")
    run_job.assert_called_once_with("run-1")
    with patch("app.integrations.runtime.tick") as tick:
        wt.integration_tick_task()
    tick.assert_called_once()


class FakeSelfTask:
    def __init__(self):
        self.calls = []

    def apply_async(self, **kwargs):
        self.calls.append(kwargs)


def test_evidence_retention_task_self_schedules_only_on_progress():
    for run_result, expected in ((True, [{"countdown": 1}]), (False, [])):
        with (
            running_session([]),
            patch.object(wt.evidence_retention_task, "apply_async") as requeue,
            patch(
                "app.services.evidence_retention.run_retention", return_value=run_result
            ),
        ):
            wt.evidence_retention_task()
        assert [c.kwargs for c in requeue.call_args_list] == expected


def test_security_notifications_task():
    with patch(
        "app.services.security_notifications.deliver_pending", return_value=[1]
    ) as deliver:
        assert wt.security_notifications_task() == [1]
    deliver.assert_called_once()


def test_ingest_source_connection_task_routes_and_validates():
    dbs = [MagicMock()]
    with (
        running_session(dbs),
        patch.object(
            wt.ingest_imap_connection_task, "delay", return_value=MagicMock(id="task-9")
        ) as imap_delay,
    ):
        assert wt.ingest_source_connection_task("imap", "conn-1") == {
            "task_id": "task-9"
        }
    imap_delay.assert_called_once_with("conn-1")

    dbs = [MagicMock()]
    with (
        running_session(dbs),
        patch("app.ingest.taiga.ingest_taiga_all", return_value=RESULT) as taiga_ingest,
    ):
        assert wt.ingest_source_connection_task("taiga", "conn-2") == RESULT
    taiga_ingest.assert_called_once_with(dbs[0], connection_id="conn-2")

    with pytest.raises(ValueError):
        wt.ingest_source_connection_task("kermit", "conn-1")


def test_dispatch_imap_task_gates_and_queues():
    with (
        patch.object(wt.settings, "imap_enabled", False),
        patch.object(wt.settings, "demo_mode", False),
    ):
        assert wt.dispatch_imap_task() == {"queued": 0}

    dbs = [MagicMock()]
    with (
        patch.object(wt.settings, "imap_enabled", True),
        patch.object(wt.settings, "demo_mode", True),
    ):
        assert wt.dispatch_imap_task() == {"queued": 0}

    with (
        patch.object(wt.settings, "imap_enabled", True),
        patch.object(wt.settings, "demo_mode", False),
        patch("app.services.ingestion_pause.is_paused", lambda db, source: True),
        running_session(dbs),
    ):
        assert wt.dispatch_imap_task() == {"queued": 0, "paused": True}

    from app.ingest.connections import Connection

    manageable = Connection("conn-1", "imap", "Primary", {}, {}, None, True, "database")
    environment = Connection(
        "conn-2", "imap", "Managed", {}, {}, None, True, "environment"
    )

    dbs = [MagicMock()]
    with (
        patch.object(wt.settings, "imap_enabled", True),
        patch.object(wt.settings, "demo_mode", False),
        patch("app.services.ingestion_pause.is_paused", lambda db, source: False),
        patch(
            "app.ingest.connections.connections", return_value=[manageable, environment]
        ) as connections,
        patch("app.ingest.connections.deployment_settings.demo_mode", False),
        patch("app.ingest.connections.deployment_settings.imap_enabled", True),
        running_session(dbs),
        patch.object(wt.ingest_imap_connection_task, "delay") as imap_delay,
    ):
        assert wt.dispatch_imap_task() == {"queued": 1}
    imap_delay.assert_called_once_with("conn-1")
    # Environment-managed connections never get queued.
    assert [call.args[0] for call in imap_delay.call_args_list] == ["conn-1"]
