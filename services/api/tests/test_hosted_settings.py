"""Hosted mode refuses any configuration outside its fixed security profile.

A hosted installation signs in through OIDC alone, uses secure host-only
cookies and a public HTTPS origin, and stores evidence in its own S3 bucket
through the instance role. Settings enforce this when a process starts, so a
misconfigured tenant never runs.
"""

import re

import pytest
from app.core.config import Settings
from pydantic import ValidationError

HOSTED = dict(
    hosted_mode=True,
    hosted_owner_subject="owner-subject",
    oidc_enabled=True,
    oidc_issuer="https://idp.example",
    oidc_auto_link_existing=False,
    oidc_auto_provision=False,
    local_auth_enabled=False,
    ldap_enabled=False,
    trust_remote_user=False,
    google_sso_enabled=False,
    github_sso_enabled=False,
    cookie_secure=True,
    cookie_domain="",
    oidc_allow_insecure_http=False,
    session_cookie_name="__Host-keen_session",
    public_base_url="https://tenant.example",
    artifact_storage_backend="s3",
    s3_use_instance_role=True,
    s3_bucket="tenant-evidence",
    s3_access_key="",
    s3_secret_key="",
    s3_endpoint_url="",
)
IDENTITY = "Hosted KEEN requires an exact OIDC issuer and owner subject"
SIGN_IN = "Hosted KEEN requires OIDC only, with automatic account linking disabled"
COOKIES = "Hosted KEEN requires secure __Host- cookies and HTTPS OIDC"
ORIGIN = "Hosted KEEN requires an exact public HTTPS origin"
BUCKET = "Hosted KEEN requires its own AWS bucket and no static S3 credentials"
ROLE = "Hosted KEEN requires S3 with instance-role credentials"


def test_the_hosted_profile_starts():
    hosted = Settings(**HOSTED)

    assert (hosted.hosted_mode, hosted.public_base_url) == (
        True,
        "https://tenant.example",
    )


DEPARTURES = [
    ("no-owner", {"hosted_owner_subject": ""}, IDENTITY),
    ("plain-http-issuer", {"oidc_issuer": "http://idp.example"}, IDENTITY),
    ("no-oidc", {"oidc_enabled": False}, SIGN_IN),
    ("local-passwords", {"local_auth_enabled": True}, SIGN_IN),
    ("ldap", {"ldap_enabled": True}, SIGN_IN),
    ("proxy-identity", {"trust_remote_user": True}, SIGN_IN),
    ("google", {"google_sso_enabled": True}, SIGN_IN),
    ("github", {"github_sso_enabled": True}, SIGN_IN),
    ("auto-link", {"oidc_auto_link_existing": True}, SIGN_IN),
    ("auto-provision", {"oidc_auto_provision": True}, SIGN_IN),
    ("insecure-cookies", {"cookie_secure": False}, COOKIES),
    ("shared-cookie-domain", {"cookie_domain": "tenant.example"}, COOKIES),
    ("plain-http-oidc", {"oidc_allow_insecure_http": True}, COOKIES),
    ("unprefixed-cookie", {"session_cookie_name": "keen_session"}, COOKIES),
    ("plain-http-origin", {"public_base_url": "http://tenant.example"}, ORIGIN),
    ("no-bucket", {"s3_bucket": ""}, BUCKET),
    ("static-access-key", {"s3_access_key": "AKIASYNTHETIC"}, BUCKET),
    ("static-secret", {"s3_secret_key": "synthetic-secret"}, BUCKET),
    ("other-endpoint", {"s3_endpoint_url": "https://minio.example"}, BUCKET),
    ("local-storage", {"artifact_storage_backend": "local"}, ROLE),
    ("no-instance-role", {"s3_use_instance_role": False}, ROLE),
]


@pytest.mark.parametrize(
    "change,message",
    [(change, message) for _, change, message in DEPARTURES],
    ids=[name for name, _, _ in DEPARTURES],
)
def test_hosted_mode_refuses_any_departure_from_its_profile(change, message):
    with pytest.raises(ValidationError, match=re.escape(message)):
        Settings(**{**HOSTED, **change})


def test_self_hosted_installations_keep_their_own_choices():
    relaxed = Settings(
        **{
            **HOSTED,
            "hosted_mode": False,
            "hosted_owner_subject": "",
            "local_auth_enabled": True,
            "oidc_auto_link_existing": True,
            "cookie_domain": "keen.example",
            "session_cookie_name": "keen_session",
            "public_base_url": "http://localhost:8080",
            "artifact_storage_backend": "local",
            "s3_use_instance_role": False,
        }
    )

    assert relaxed.local_auth_enabled and relaxed.cookie_domain == "keen.example"
    assert relaxed.public_base_url == "http://localhost:8080"
