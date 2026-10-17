"""KEEN requires its browser-facing origin before any process starts.

Absolute links in emails, notifications and outbound webhook messages are
built from KEEN_PUBLIC_BASE_URL, and browser Origin checks compare against it,
so request headers never decide which host a link names.
"""

import os
import subprocess
import sys

import pytest
from app.core.config import Settings
from pydantic import ValidationError

REQUIRED = "KEEN_PUBLIC_BASE_URL is required"
NOT_AN_ORIGIN = "KEEN_PUBLIC_BASE_URL must be an http:// or https:// origin"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://keen.example.com", "https://keen.example.com"),
        ("https://keen.example.com/", "https://keen.example.com"),
        (" HTTPS://Keen.Example.com:8443/ ", "https://keen.example.com:8443"),
        ("http://localhost:8080", "http://localhost:8080"),
        ("http://[::1]:8080", "http://[::1]:8080"),
    ],
)
def test_public_origins_are_accepted_in_one_canonical_form(value, expected):
    assert Settings(public_base_url=value).public_base_url == expected


@pytest.mark.parametrize("value", ["", "   ", "/"])
def test_a_missing_public_origin_is_refused(value):
    with pytest.raises(ValidationError, match=REQUIRED):
        Settings(public_base_url=value)


def test_the_public_origin_is_required_from_the_environment(monkeypatch):
    monkeypatch.delenv("KEEN_PUBLIC_BASE_URL", raising=False)
    with pytest.raises(ValidationError, match=REQUIRED):
        Settings()


@pytest.mark.parametrize(
    "value",
    [
        "keen.example.com",
        "ftp://keen.example.com",
        "javascript:alert(1)",
        "https://",
        "https://user:password@keen.example.com",
        "https://keen.example.com\\@attacker.example",
        "https://keen.example.com\\evil",
        "https://keen.example.com/keen",
        "https://keen.example.com/?next=/",
        "https://keen.example.com?next=/",
        "https://keen.example.com#fragment",
        "https://keen.example.com:99999",
        "https://keen example.com",
        "https://keen.example.com\r\nX-Injected: 1",
    ],
)
def test_anything_but_a_bare_origin_is_refused(value):
    with pytest.raises(ValidationError, match=NOT_AN_ORIGIN):
        Settings(public_base_url=value)


@pytest.mark.parametrize("module", ["app.main", "app.worker.celery_app"])
def test_processes_refuse_to_start_without_a_public_origin(module):
    env = {k: v for k, v in os.environ.items() if k != "KEEN_PUBLIC_BASE_URL"}

    def start(environment):
        return subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )

    refused = start(env)
    assert refused.returncode != 0
    assert REQUIRED in refused.stderr

    started = start({**env, "KEEN_PUBLIC_BASE_URL": "https://keen.example.com"})
    assert started.returncode == 0, started.stderr
