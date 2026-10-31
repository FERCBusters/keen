"""Exercise actual dependency logging and hostile preview inputs."""

import logging
import subprocess
import sys

import httpx
from app.ingest import bookstack, git_activity, google_workspace
from app.ingest import http as ingestion_http


def test_httpx_does_not_log_query_credentials_or_reason_phrase(caplog):
    assert ingestion_http.client
    with caplog.at_level(logging.DEBUG, logger="httpx"):
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    403, extensions={"reason_phrase": b"SYNTHETIC_SECRET"}
                )
            )
        ) as client:
            client.get("https://example.test/feed?token=SYNTHETIC_SECRET")
    assert "SYNTHETIC_SECRET" not in caplog.text
    assert not [r for r in caplog.records if r.name == "httpx"]


def test_google_sdk_does_not_log_invalid_response_body(caplog):
    from googleapiclient.http import _should_retry_response

    assert google_workspace.SCOPES
    with caplog.at_level(logging.DEBUG, logger="googleapiclient.http"):
        assert not _should_retry_response(403, b"SYNTHETIC_SECRET")
    assert "SYNTHETIC_SECRET" not in caplog.text
    assert not [r for r in caplog.records if r.name == "googleapiclient.http"]


def test_redirect_cannot_reflect_credential_under_unrecognised_key():
    response = httpx.Response(
        302,
        request=httpx.Request("GET", "https://example.test/SYNTHETIC_SECRET"),
        headers={"location": "https://example.test/?innocent=SYNTHETIC_SECRET"},
        extensions={"reason_phrase": b"SYNTHETIC_SECRET"},
    )
    assert git_activity._response_status_error(response) == (
        "Collection failed: upstream returned HTTP 302"
    )


def test_bookstack_normal_preview():
    assert (
        bookstack._strip_html_to_text(
            "<p>Hello &amp; goodbye</p><script>secret()</script><style>css</style>"
        )
        == "Hello & goodbye"
    )


def test_bookstack_hostile_preview_is_bounded():
    # A killed child protects pytest itself if the runtime boundary regresses.
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.ingest.bookstack import _strip_html_to_text; "
                "_strip_html_to_text('<script>' * 20000); "
                "_strip_html_to_text('<' * 20000)"
            ),
        ],
        timeout=3,
        check=True,
    )


def test_bookstack_preview_timeout_omits_optional_preview(monkeypatch):
    class SlowPattern:
        def sub(self, replacement, text, *, timeout):
            assert len(text) <= 65536
            assert timeout == 0.025
            raise TimeoutError

    monkeypatch.setattr(bookstack, "_SCRIPT_STYLE_RE", SlowPattern())
    assert bookstack._strip_html_to_text("x" * 100000) == ""
