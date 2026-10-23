"""Keen requires its browser-facing origin before any process starts.

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


# Other settings validators: each refuses values outside its allowed set with a
# message naming the variable, so a misconfigured deployment fails at start-up.


def test_demo_mode_needs_an_aware_expiry_and_an_https_consultation_link():
    from datetime import datetime, timezone

    expires = datetime(2027, 1, 1, tzinfo=timezone.utc)
    assert Settings(demo_mode=True, demo_expires_at=expires).demo_mode is True
    with pytest.raises(ValidationError, match="timezone-aware expiry"):
        Settings(demo_mode=True)
    with pytest.raises(ValidationError, match="timezone-aware expiry"):
        Settings(demo_mode=True, demo_expires_at=datetime(2027, 1, 1))
    for link in (
        "http://mig5.net/",
        "https://",
        "https://user@mig5.net/",
        "javascript:alert(1)",
    ):
        with pytest.raises(ValidationError, match="must be HTTPS"):
            Settings(
                demo_mode=True, demo_expires_at=expires, demo_consultation_url=link
            )
    # Outside demo mode the link is not checked, so a plain deployment starts.
    assert Settings(demo_consultation_url="http://ignored.example/").demo_mode is False


def test_mfa_policy_must_name_a_known_scope():
    for value in ("optional", "admins", "all"):
        assert Settings(mfa_policy=value).mfa_policy == value
    for value in ("Admins", "everyone", ""):
        with pytest.raises(ValidationError, match="MFA policy must be"):
            Settings(mfa_policy=value)


def test_artifact_storage_backend_is_normalised_or_refused():
    assert Settings(artifact_storage_backend=" S3 ").artifact_storage_backend == "s3"
    for value in ("", "minio", "filesystem"):
        with pytest.raises(ValidationError, match="KEEN_ARTIFACT_STORAGE_BACKEND"):
            Settings(artifact_storage_backend=value)


def test_s3_compatibility_mode_accepts_hyphenated_spelling_and_refuses_others():
    assert Settings(s3_compatibility_mode="S3-Compatible").s3_compatibility_mode == (
        "s3_compatible"
    )
    assert Settings(s3_compatibility_mode="").s3_compatibility_mode == "auto"
    with pytest.raises(ValidationError) as refused:
        Settings(s3_compatibility_mode="minio")
    assert "KEEN_S3_COMPATIBILITY_MODE must be one of: auto, aws, s3_compatible" in str(
        refused.value
    )


def test_event_data_masking_mode_is_normalised_or_refused():
    assert Settings(event_data_masking=" Samples ").event_data_masking == "samples"
    assert Settings(event_data_masking="").event_data_masking == "false"
    with pytest.raises(ValidationError, match="KEEN_EVENT_DATA_MASKING must be"):
        Settings(event_data_masking="yes")


def test_scheduled_audit_lead_time_is_a_whole_number_of_days():
    assert (
        Settings(scheduled_audit_create_days_ahead=0).scheduled_audit_create_days_ahead
        == 0
    )
    assert (
        Settings(
            scheduled_audit_create_days_ahead="7"
        ).scheduled_audit_create_days_ahead
        == 7
    )
    with pytest.raises(ValidationError, match="non-negative integer"):
        Settings(scheduled_audit_create_days_ahead=-1)
    # The validator itself refuses text when called with an unparsed value.
    with pytest.raises(ValueError, match="non-negative integer"):
        Settings.validate_scheduled_audit_create_days_ahead("soon")
    assert Settings.validate_scheduled_audit_create_days_ahead(None) == 0
