"""Outbound question webhooks: payload builders, fanout and signing."""

import contextlib
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from app.outbound import question_webhooks as qw

PAYLOAD = {
    "type": "keen.question.created",
    "event": {
        "summary": "Suspicious login",
        "source": "taiga",
        "timestamp": "2026-10-09T12:00:00",
    },
    "thread": {"created_by_username": "alice", "status": "open"},
    "post": {"body": "Why did this event fire twice?"},
    "controls": [{"ref": "A.9.1"}, {"ref": "A.5.3"}, None],
    "links": {"event": "https://keen.example/event.html?id=e1#questions"},
}


def test_split_urls():
    assert qw._split_urls("a\nb,  c\t d") == ["a", "b", "c", "d"]
    assert qw._split_urls("a, b ,a, c,x x") == ["a", "b", "c", "x"]
    assert qw._split_urls("") == []
    assert qw._split_urls(None) == []
    assert qw._split_urls("   ") == []


def test_safe_url_hint_keeps_secret_paths_out():
    assert qw._safe_url_hint("https://hooks.example/TOKEN/secret") == (
        "https://hooks.example"
    )
    assert qw._safe_url_hint("http://user:pass@bad.example/x?token=t") == (
        "http://bad.example"
    )
    assert qw._safe_url_hint("ftp://x") == "(url)"
    assert qw._safe_url_hint("not-a-url") == "(url)"
    assert qw._safe_url_hint("") == "(url)"
    assert qw._safe_url_hint("https://[::1/hook") == "(url)"


def test_parse_json_headers():
    assert qw._parse_json_headers('{"X-Token": "abc", "bad key": "x"}') == {
        "X-Token": "abc"
    }
    assert qw._parse_json_headers('{"n": 3, "none": null}') == {"n": "3"}
    assert qw._parse_json_headers('"junk"') == {}
    assert qw._parse_json_headers("{") == {}
    assert qw._parse_json_headers("") == {}
    assert qw._parse_json_headers(None) == {}


def test_hmac_signature_format():
    expected = hashlib.sha256(b"").hexdigest()
    mac = qw._hmac_sha256("secret", b"")
    assert mac == f"sha256={hmac.new(b'secret', b'', hashlib.sha256).hexdigest()}"
    assert mac.split("=", 1)[0] == "sha256"
    assert expected != mac.split("=", 1)[1]


def test_abs_url_and_question_links():
    with patch.object(qw.settings, "public_base_url", "https://keen.example"):
        links = qw.build_question_links(event_id="e1", thread_id="t-1")
    assert links == {
        "event": "https://keen.example/event.html?id=e1&thread=t-1#questions",
        "admin_queue": "https://keen.example/questions.html",
    }


def test_from_settings_created_uses_primary_urls():
    with contextlib.ExitStack() as stack:
        for attr, value in {
            "question_webhook_slack_urls": "https://a.slack \n https://b.slack",
            "question_webhook_teams_urls": "https://teams.example/one , https://teams.example/one",
            "question_webhook_google_chat_urls": "https://chat.example",
            "question_webhook_generic_urls": "https://gw.example",
            "question_webhook_generic_headers_json": '{"X-Env": "yes"}',
            "question_webhook_signing_secret": "  fixture-secret  ",
            "question_webhook_signature_header": " X-Sig ",
            "question_webhook_timeout_seconds": "4",
        }.items():
            stack.enter_context(patch.object(qw.settings, attr, value))
        config = qw.OutboundWebhookConfig.from_settings()
    assert config.slack_urls == ["https://a.slack", "https://b.slack"]
    assert config.teams_urls == ["https://teams.example/one"]
    assert config.google_chat_urls == ["https://chat.example"]
    assert config.generic_urls == ["https://gw.example"]
    assert config.generic_headers == {"X-Env": "yes"}
    assert config.generic_signing_secret == "fixture-secret"
    assert config.generic_signature_header == "X-Sig"
    assert config.timeout_seconds == 4.0
    assert config.any_enabled() is True


def test_from_settings_reply_fallback():
    base = {
        "question_webhook_slack_urls": "https://primary.slack",
        "question_webhook_teams_urls": "https://primary.teams",
        "question_webhook_google_chat_urls": "https://primary.chat",
        "question_webhook_generic_urls": "https://primary.generic",
        "question_webhook_signing_secret": "",
    }
    with contextlib.ExitStack() as stack:
        for attr, value in base.items():
            stack.enter_context(patch.object(qw.settings, attr, value))
        stack.enter_context(
            patch.object(
                qw.settings, "question_reply_webhook_use_question_urls_if_empty", True
            )
        )
        for attr in (
            "question_reply_webhook_slack_urls",
            "question_reply_webhook_teams_urls",
            "question_reply_webhook_google_chat_urls",
            "question_reply_webhook_generic_urls",
        ):
            stack.enter_context(patch.object(qw.settings, attr, None))
        config = qw.OutboundWebhookConfig.from_settings(event="reply")
    assert config.slack_urls == ["https://primary.slack"]
    assert config.generic_signature_header == "X-Keen-Signature"
    assert config.timeout_seconds == float(
        qw.settings.question_webhook_timeout_seconds or 6.0
    )

    # Without the fallback flag, empty reply URLs stay empty.
    with contextlib.ExitStack() as stack:
        for attr, value in base.items():
            stack.enter_context(patch.object(qw.settings, attr, value))
        stack.enter_context(
            patch.object(
                qw.settings, "question_reply_webhook_use_question_urls_if_empty", False
            )
        )
        for attr in (
            "question_reply_webhook_slack_urls",
            "question_reply_webhook_teams_urls",
            "question_reply_webhook_google_chat_urls",
            "question_reply_webhook_generic_urls",
        ):
            stack.enter_context(patch.object(qw.settings, attr, None))
        config = qw.OutboundWebhookConfig.from_settings(event="reply")
        assert config.any_enabled() is False


def test_build_generic_payload_extra_merge():
    payload = qw.build_generic_payload(
        event={"id": "e1"},
        thread={"id": "t1"},
        post={"body": "x"},
        controls=None,
        links={"event": "/e"},
        extra={"actor_timezone": "UTC"},
    )
    assert payload["type"] == "keen.question.created"
    assert payload["controls"] == []
    assert payload["actor_timezone"] == "UTC"


def test_compact_text_created_and_replied():
    text = qw._compact_text({})
    assert "(no summary)" in text
    created = qw._compact_text(PAYLOAD)
    assert created.startswith("New question on event")
    assert "Event: Suspicious login" in created
    assert "Source: taiga • 2026-10-09T12:00:00" in created
    assert "Asked by: alice" in created
    assert "Why did this event fire twice?" in created
    assert "Controls: A.9.1, A.5.3" in created
    assert "Open: https://keen.example/event.html?id=e1#questions" in created

    replied_payload = dict(PAYLOAD, type="keen.question.replied")
    replied_payload["reply"] = {"as": "admin"}
    replied_payload["post"] = dict(PAYLOAD["post"], author_username="bob")
    replied = qw._compact_text(replied_payload)
    assert replied.startswith("New admin reply on question")
    assert "Replied by: bob (admin)" in replied
    assert "Thread status: open" in replied
    assert "Asked by" not in replied

    # >12 controls are summarised with an overflow count.
    many = dict(PAYLOAD, controls=[{"ref": f"A.{i}"} for i in range(15)])
    overflow = qw._compact_text(many)
    assert "(+3 more)" in overflow


def test_slack_and_teams_google_payloads():
    slack = qw.slack_payload(PAYLOAD)
    assert slack["text"].startswith(":question: ")
    assert (
        "<https://keen.example/event.html?id=e1#questions|View in Keen>"
        in slack["text"]
    )

    chat = qw.google_chat_payload(PAYLOAD)
    assert chat["text"].startswith("New question on event")

    teams = qw.teams_payload(PAYLOAD)
    assert teams["@type"] == "MessageCard"
    assert teams["summary"] == "Keen question"
    assert teams["title"] == "Keen: Suspicious login"
    assert teams["text"].endswith(
        "[View in Keen](https://keen.example/event.html?id=e1#questions)"
    )

    replied_payload = dict(PAYLOAD, type="keen.question.replied")
    assert qw.slack_payload(replied_payload)["text"].startswith(":speech_balloon:")
    teams_replied = qw.teams_payload(replied_payload)
    assert teams_replied["summary"] == "Keen question reply"
    assert teams_replied["title"] == "Keen: Suspicious login"


def build_client_handler(api: dict):
    """Return a fake secure_http_client fanout recorder."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        seen.append(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": body,
            }
        )
        if request.url.path.endswith("/503"):
            return httpx.Response(503)
        if request.url.path.endswith("/429"):
            return httpx.Response(429)
        if request.url.path.endswith("/404"):
            return httpx.Response(404)
        return httpx.Response(200, json={"ok": True})

    def fake_secure_client(**kwargs):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        returned_kw = dict(kwargs)
        seen.append({"client_kwargs": returned_kw})
        return contextlib.nullcontext(client)

    return fake_secure_client, seen


def make_config(**overrides):
    values = {
        "slack_urls": [],
        "teams_urls": [],
        "google_chat_urls": [],
        "generic_urls": [],
        "generic_headers": {},
        "generic_signing_secret": "",
        "generic_signature_header": "X-Keen-Signature",
        "timeout_seconds": 2.0,
    }
    values.update(overrides)
    return qw.OutboundWebhookConfig(**values)


def test_send_created_webhooks_disabled():
    assert qw.send_question_created_webhooks(make_config(), PAYLOAD) == {
        "enabled": False
    }


def test_send_created_webhooks_success_and_signature():
    fake_client, seen = build_client_handler({})
    config = make_config(
        slack_urls=["https://hooks.slack.example/x"],
        teams_urls=["https://teams.example/one"],
        google_chat_urls=["https://chat.example/x"],
        generic_urls=["https://gw.example/one"],
        generic_headers={"X-Instance": "corp"},
        generic_signing_secret="fixture-secret",
    )
    with patch.object(qw, "secure_http_client", fake_client):
        out = qw.send_question_created_webhooks(config, PAYLOAD)
    assert out == {
        "enabled": True,
        "slack_sent": 1,
        "slack_failed": 0,
        "teams_sent": 1,
        "teams_failed": 0,
        "google_chat_sent": 1,
        "google_chat_failed": 0,
        "generic_sent": 1,
        "generic_failed": 0,
    }
    client_kwargs = seen[0]["client_kwargs"]
    # The outbound boundary must be pinned: no redirects, no proxy env trust.
    assert client_kwargs["follow_redirects"] is False
    assert client_kwargs["verify"] is True
    assert client_kwargs["trust_env"] is False
    slack_post = next(
        entry for entry in seen if "hooks.slack.example" in entry.get("url", "")
    )
    assert slack_post["body"].startswith(b'{"text":')
    teams_post = next(
        entry for entry in seen if "teams.example" in entry.get("url", "")
    )
    assert teams_post["body"].startswith(b'{"@type":"MessageCard"')
    generic_post = next(entry for entry in seen if "gw.example" in entry.get("url", ""))
    assert generic_post["headers"]["content-type"] == "application/json"
    assert generic_post["headers"]["x-instance"] == "corp"
    assert generic_post["headers"]["x-keen-signature"] == qw._hmac_sha256(
        "fixture-secret", generic_post["body"]
    )
    expected_body = json.dumps(PAYLOAD, separators=(",", ":"), sort_keys=True).encode()
    assert generic_post["body"] == expected_body


def test_send_created_webhooks_no_signing_secret_no_header():
    fake_client, seen = build_client_handler({})
    config = make_config(generic_urls=["https://gw.example/one"])
    with patch.object(qw, "secure_http_client", fake_client):
        out = qw.send_question_created_webhooks(config, PAYLOAD)
    generic_post = next(entry for entry in seen if "gw.example" in entry.get("url", ""))
    assert "x-keen-signature" not in generic_post["headers"]
    assert out["generic_sent"] == 1


def test_send_created_webhooks_retry_on_429_and_5xx():
    fake_client, _seen = build_client_handler({})
    config = make_config(slack_urls=["https://hooks.slack.example/429"])
    with (
        patch.object(qw, "secure_http_client", fake_client),
        pytest.raises(qw._TemporarySendError) as info,
    ):
        qw.send_question_created_webhooks(config, PAYLOAD)
    assert "temporary failure" in str(info.value) and "429" in str(info.value)

    fake_client, _seen = build_client_handler({})
    config = make_config(generic_urls=["https://gw.example/503"])
    with (
        patch.object(qw, "secure_http_client", fake_client),
        pytest.raises(qw._TemporarySendError),
    ):
        qw.send_question_created_webhooks(config, PAYLOAD)


def test_send_created_webhooks_4xx_logs_but_does_not_retry():
    fake_client, seen = build_client_handler({})
    config = make_config(slack_urls=["https://hooks.slack.example/404"])
    with patch.object(qw, "secure_http_client", fake_client):
        out = qw.send_question_created_webhooks(config, PAYLOAD)
    assert out == {
        "enabled": True,
        "slack_sent": 0,
        "slack_failed": 1,
        "teams_sent": 0,
        "teams_failed": 0,
        "google_chat_sent": 0,
        "google_chat_failed": 0,
        "generic_sent": 0,
        "generic_failed": 0,
    }


def test_send_created_webhooks_network_failures_raise():
    def failing_client(**kwargs):
        client = MagicMock()
        stream = MagicMock(
            __enter__=MagicMock(side_effect=OSError("connection refused")),
            __exit__=MagicMock(return_value=False),
        )
        client.stream.return_value = stream
        return contextlib.nullcontext(client)

    with pytest.raises(qw._TemporarySendError):
        config = make_config(slack_urls=["https://hooks.slack.example/x"])
        with patch.object(qw, "secure_http_client", failing_client):
            qw.send_question_created_webhooks(config, PAYLOAD)

    with pytest.raises(qw._TemporarySendError):
        config = make_config(generic_urls=["https://gw.example/x"])
        with patch.object(qw, "secure_http_client", failing_client):
            qw.send_question_created_webhooks(config, PAYLOAD)


def test_send_created_webhooks_timeout_configuration():
    captured = {}

    def capturing_client(**kwargs):
        captured.update(kwargs)
        return contextlib.nullcontext(
            httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        )

    config = make_config(
        slack_urls=["https://hooks.slack.example/x"], timeout_seconds=6.0
    )
    with patch.object(qw, "secure_http_client", capturing_client):
        qw.send_question_created_webhooks(config, PAYLOAD)
    timeout = captured["timeout"]
    # Connecting is bounded more tightly than the whole delivery.
    assert (timeout.connect, timeout.read, timeout.write) == (3.0, 6.0, 6.0)
    with patch.object(qw, "secure_http_client", capturing_client):
        qw.send_question_created_webhooks(
            make_config(slack_urls=["https://a.example/x"]), PAYLOAD
        )
    assert captured["timeout"].connect == 2.0


def test_reply_webhooks_delegate_to_fanout():
    fake_client, seen = build_client_handler({})
    replied = dict(PAYLOAD, type="keen.question.replied")
    config = make_config(slack_urls=["https://hooks.slack.example/x"])
    with patch.object(qw, "secure_http_client", fake_client):
        out = qw.send_question_reply_webhooks(config, replied)
    assert out["slack_sent"] == 1
    slack_post = next(
        entry for entry in seen if "hooks.slack.example" in entry.get("url", "")
    )
    assert b":speech_balloon:" in slack_post["body"]
