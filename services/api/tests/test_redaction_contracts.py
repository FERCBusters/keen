"""Secret redaction and sample masking preserve useful non-sensitive evidence."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from app.security import redaction as r


@pytest.fixture(autouse=True)
def fresh_policy(monkeypatch):
    monkeypatch.delenv("KEEN_SENSITIVE_KEYS", raising=False)
    monkeypatch.delenv("KEEN_SENSITIVE_KEYS_MODE", raising=False)
    r._sensitive_cfg.cache_clear()
    yield
    r._sensitive_cfg.cache_clear()


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "password",
        "api_key",
        "api-key",
        "Authorization",
        "client_secret",
        "cookie",
    ],
)
def test_sensitive_object_keys_are_redacted_at_every_depth(key):
    obj = {
        "items": [{key: "DO-NOT-LEAK", "count": 0, "enabled": False}],
        "safe": "retained",
    }
    result = r.redact_obj(obj)
    assert "DO-NOT-LEAK" not in json.dumps(result)
    assert result["items"][0]["count"] == 0 and result["items"][0]["enabled"] is False
    assert obj["items"][0][key] == "DO-NOT-LEAK"


def test_url_redaction_keeps_path_safe_query_and_fragment():
    result = urlsplit(
        r.redact_url("https://example.test/path?token=DO-NOT-LEAK&safe=hello#section")
    )
    assert result.path == "/path" and result.fragment == "section"
    assert parse_qs(result.query) == {"token": ["REDACTED"], "safe": ["hello"]}


@pytest.mark.parametrize(
    "text",
    [
        "token=DO-NOT-LEAK",
        "password: DO-NOT-LEAK",
        "https://example.test/?api_key=DO-NOT-LEAK",
    ],
)
def test_free_text_secrets_are_removed(text):
    assert "DO-NOT-LEAK" not in r.redact_str(text)


def test_custom_redaction_extends_defaults_and_replace_is_explicit(monkeypatch):
    monkeypatch.setenv("KEEN_SENSITIVE_KEYS", "custom_secret")
    r._sensitive_cfg.cache_clear()
    assert r.redact_obj({"custom_secret": "x", "password": "y"}) == {
        "custom_secret": "REDACTED",
        "password": "REDACTED",
    }
    monkeypatch.setenv("KEEN_SENSITIVE_KEYS_MODE", "replace")
    r._sensitive_cfg.cache_clear()
    assert r.redact_obj({"custom_secret": "x", "password": "y"}) == {
        "custom_secret": "REDACTED",
        "password": "y",
    }


@pytest.mark.parametrize(
    "text,expected",
    [
        ("contact a@example.test", "contact [MASKED_EMAIL]"),
        ("from 192.0.2.1", "from [MASKED_IP]"),
        ("from 2001:db8::1", "from [MASKED_IP]"),
        ("at 13:40:19", "at 13:40:19"),
        ("version 1.2.3", "version 1.2.3"),
        ("bad 999.999.999.999", "bad 999.999.999.999"),
        # Sentence punctuation must not let an address escape masking.
        ("Login from 192.0.2.1.", "Login from [MASKED_IP]."),
        ("Mail a@example.test.", "Mail [MASKED_EMAIL]."),
        ("Peers: 192.0.2.1, 198.51.100.7.", "Peers: [MASKED_IP], [MASKED_IP]."),
        ("from fe80::1.", "from [MASKED_IP]."),
        # A colon after an address introduces the rest of a log message.
        (
            "address 2001:db8::1: Name or service not known",
            "address [MASKED_IP]: Name or service not known",
        ),
        ("peer 2001:db8:1:2:3:4:5:6: refused", "peer [MASKED_IP]: refused"),
        ("at 13:40:19: done", "at 13:40:19: done"),
        ("mapped 0:0:0:0:0:ffff:192.0.2.1.", "mapped [MASKED_IP]."),
        ("route via 2001:db8::.", "route via [MASKED_IP]."),
        ("(a@example.test)", "([MASKED_EMAIL])"),
        ("a@mail.example.co.uk", "[MASKED_EMAIL]"),
        # A display name identifies the person as much as the address does.
        ("Alice Smith <alice@example.test>", "[MASKED_EMAIL]"),
        (
            'To: "Smith, Alice" <alice@example.test>, Bob <bob@example.test>',
            "To: [MASKED_EMAIL], [MASKED_EMAIL]",
        ),
        ("Dr. J. R. R. Tolkien-Smith <jrr@example.test>", "[MASKED_EMAIL]"),
        ("Ünal Yılmaz <unal@example.test>", "[MASKED_EMAIL]"),
        ("Reply-To: <noreply@example.test>", "Reply-To: [MASKED_EMAIL]"),
        # Words that are not a name stay: numbers, punctuation and URLs.
        ("Version 1.2.3 sent by bob <b@example.test>", "Version 1.2.3 [MASKED_EMAIL]"),
        ("ticket #42: Bob <bob@example.test>", "ticket #42: [MASKED_EMAIL]"),
        (
            "see https://x.example/<alice@example.test>",
            "see https://x.example/[MASKED_EMAIL]",
        ),
        # Longer dotted tokens are left alone as versions or host names.
        ("build 1.2.3.4.5", "build 1.2.3.4.5"),
        ("host 192.0.2.1a", "host 192.0.2.1a"),
    ],
)
def test_sample_masking_does_not_confuse_times_or_versions(text, expected):
    assert r.mask_event_data_str(text) == expected


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "application/problem+json", "text/plain", "application/xml"],
)
def test_text_artifact_redaction(content_type):
    data = (
        b'{"token":"DO-NOT-LEAK"}'
        if "json" in content_type
        else b'<item token="DO-NOT-LEAK" />'
        if "xml" in content_type
        else b"token=DO-NOT-LEAK"
    )
    result, status = r.redact_bytes(data, content_type)
    assert b"DO-NOT-LEAK" not in result and status == "redacted"


def test_binary_artifacts_are_not_corrupted():
    data = b"\x00\xff\x80password=secret"
    assert r.redact_bytes(data, "application/octet-stream") == (data, "skipped")
    assert r.mask_event_data_bytes(data, "image/png") == (data, "skipped")


def test_json_sample_masking_preserves_structure_and_scalars():
    data = json.dumps(
        {
            "ip": "192.0.2.1",
            "nested": [{"email": "a@example.test"}],
            "n": 0,
            "flag": False,
        }
    ).encode()
    result, status = r.mask_event_data_bytes(data, "application/json")
    assert status == "redacted"
    assert json.loads(result) == {
        "ip": "[MASKED_IP]",
        "nested": [{"email": "[MASKED_EMAIL]"}],
        "n": 0,
        "flag": False,
    }


@pytest.mark.parametrize(
    "statuses,expected",
    [
        ([], "unknown"),
        (["clean"], "clean"),
        (["skipped"], "skipped"),
        (["clean", "skipped"], "clean"),
        (["clean", "redacted"], "redacted"),
        ([None, "redacted"], "redacted"),
    ],
)
def test_redaction_status_composition(statuses, expected):
    assert r.combine_redaction_status(*statuses) == expected


def test_malformed_credential_urls_are_replaced_whole():
    assert r.redact_url("https://user:pw@[::1/report") == "REDACTED"
    assert r.redact_str("see https://user:pw@[::1/report now") == "see REDACTED now"


def test_query_strings_keep_blank_and_safe_values():
    assert r.redact_query_string("") is None
    assert r.redact_query_string("token=&page=2&api-key=x") == (
        "token=&page=2&api-key=REDACTED"
    )
    # Extra keys apply only where a caller names them, such as OAuth callbacks.
    assert r.redact_query_string("code=abc&state=xyz", frozenset({"code"})) == (
        "code=REDACTED&state=xyz"
    )


def test_tuples_are_redacted_masked_and_made_storable():
    value = ("token=DO-NOT-LEAK", "from 192.0.2.1", "a\x00b")

    assert r.redact_obj(value) == ("token=REDACTED", "from 192.0.2.1", "a\x00b")
    assert r.mask_event_data_obj(value)[1] == "from [MASKED_IP]"
    assert r.storable(value)[2] == "a�b"
    assert r.prepare_event_fields({"pair": value}, mask=True) == {
        "pair": ("token=REDACTED", "from [MASKED_IP]", "a�b")
    }


@pytest.mark.parametrize("protect", [r.redact_bytes, r.mask_event_data_bytes])
def test_empty_and_untyped_artifacts_are_left_alone(protect):
    data = b"token=x from 192.0.2.1"

    assert protect(b"", "text/plain") == (b"", "clean")
    assert protect(data, None) == (data, "skipped")


def test_text_artifacts_with_invalid_utf8_are_still_protected():
    data = b"\xff\xfe token=DO-NOT-LEAK from 192.0.2.1"

    redacted, status = r.redact_bytes(data, "text/plain; charset=utf-8")
    assert status == "redacted" and b"DO-NOT-LEAK" not in redacted
    masked, status = r.mask_event_data_bytes(data, "text/plain")
    assert status == "redacted" and b"192.0.2.1" not in masked
    # Text with nothing to protect keeps its original bytes.
    assert r.mask_event_data_bytes(b"\xff plain", "text/plain") == (
        b"\xff plain",
        "clean",
    )


def test_malformed_json_artifacts_fall_back_to_text_protection():
    data = b'{"token": "DO-NOT-LEAK", "ip": "192.0.2.1"'

    redacted, status = r.redact_bytes(data, "application/json")
    assert status == "redacted" and b"DO-NOT-LEAK" not in redacted
    masked, status = r.mask_event_data_bytes(data, "application/vnd.keen+json")
    assert status == "redacted" and b"192.0.2.1" not in masked
    assert r.mask_event_data_bytes(b'{"n": 1}', "application/json") == (
        b'{"n": 1}',
        "clean",
    )


@pytest.mark.parametrize(
    "text", ["ratio 1:2:3:4", "id ab::cd::ef", "node 1:2:3:4:5:6:7:8:9"]
)
def test_colon_separated_tokens_that_are_not_addresses_are_kept(text):
    assert r.mask_event_data_str(text) == text


def test_mailbox_headers_from_a_mail_collector_are_masked_whole(monkeypatch):
    """Address headers mask the person, not only the address."""
    from app.core.config import settings
    from app.ingest.common import normalise_ingested_fields

    monkeypatch.setattr(settings, "event_data_masking", "true")
    message = {
        "headers": {
            "from": "Alice Smith <alice@example.test>",
            "to": '"Smith, Bob" <bob@example.test>, carol@example.test',
            "subject": "Re: access review for dave@example.test",
        },
        "body": "Hi Bob,\nplease add Erin <erin@example.test> from 192.0.2.7.",
    }
    fields = normalise_ingested_fields(
        system="mail",
        actor=message["headers"]["from"],
        action="received",
        outcome=None,
        summary="Email: " + message["headers"]["subject"],
        external_id="<id@example.test>",
        raw_pointer={"uid": 7},
        normalized_payload=message,
        artifact_bytes=json.dumps(message).encode(),
        artifact_content_type="application/json",
    )

    assert fields.actor == "[MASKED_EMAIL]"
    assert fields.summary == "Email: Re: access review for [MASKED_EMAIL]"
    assert fields.normalized_payload["headers"] == {
        "from": "[MASKED_EMAIL]",
        "to": "[MASKED_EMAIL], [MASKED_EMAIL]",
        "subject": "Re: access review for [MASKED_EMAIL]",
    }
    # Words before a bracketed address can be a name, so they go with it.
    assert (
        fields.normalized_payload["body"] == "Hi Bob,\n[MASKED_EMAIL] from [MASKED_IP]."
    )
    stored = json.loads(fields.artifact_bytes)
    assert stored == fields.normalized_payload
    assert fields.redaction_status == "redacted"
    # The dedupe identifier is never masked; it is not shown as evidence.
    assert fields.external_id == "<id@example.test>"
