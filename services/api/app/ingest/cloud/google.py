"""Google API transport for the Google Cloud and Google Workspace providers.

A service-account key is checked before use: its token endpoint must be HTTPS
without embedded credentials or a fragment. Each client is built from the
discovery documents bundled with the SDK, over one certificate-validating
HTTPS-only transport with a 30-second timeout, no redirects and no
environment proxy, which also carries token refresh. Each read asks only for
the OAuth scopes it needs.
"""

from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
from urllib.parse import urlsplit

import httplib2
from google.auth.exceptions import GoogleAuthError, RefreshError
from google.oauth2 import service_account
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.ingest.cloud.base import Unavailable
from app.security.provider_logging import suppress_provider_logs

# The SDK logs response bodies on malformed 403s and URLs during retries.
suppress_provider_logs("googleapiclient.http", "googleapiclient.discovery")

# Service-account key files are small; anything larger is refused unread.
MAX_KEY_BYTES = 64 * 1024


def _safe(url) -> bool:
    endpoint = urlsplit(str(url or ""))
    return bool(
        endpoint.scheme == "https"
        and endpoint.hostname
        and not endpoint.username
        and not endpoint.password
        and not endpoint.fragment
    )


class HTTPSOnlyHttp(httplib2.Http):
    def __init__(self):
        super().__init__(
            timeout=30, disable_ssl_certificate_validation=False, proxy_info=None
        )
        self.follow_redirects = False
        self.follow_all_redirects = False

    def request(self, uri, *args, **kwargs):
        if not _safe(uri):
            raise ValueError("Google API requests require HTTPS")
        return super().request(uri, *args, **kwargs)


def key_info(keyfile: str, raw_b64: str, raw_json: str) -> dict:
    """The service-account key from a file, base64 text or JSON text. A
    missing, oversized, unreadable or unsafe key is a configuration reason."""
    try:
        if keyfile:
            path = Path(keyfile)
            if path.stat().st_size > MAX_KEY_BYTES:
                raise ValueError("too large")
            text = path.read_text()
        elif raw_b64:
            if len(raw_b64) > MAX_KEY_BYTES * 2:
                raise ValueError("too large")
            text = base64.b64decode(raw_b64.encode(), validate=True).decode()
        elif raw_json:
            text = raw_json
        else:
            raise ValueError("missing")
        if len(text) > MAX_KEY_BYTES:
            raise ValueError("too large")
        info = json.loads(text)
    except (ValueError, OSError, binascii.Error):
        raise Unavailable("not_configured") from None
    if not isinstance(info, dict) or not _safe(info.get("token_uri")):
        raise Unavailable("not_configured")
    return info


def client(api: str, version: str, info: dict, scopes, subject: str = ""):
    """A client for one API holding only ``scopes``, acting as ``subject``."""
    if not _safe(info.get("token_uri")):
        raise Unavailable("not_configured")
    try:
        granted = service_account.Credentials.from_service_account_info(
            info, scopes=list(scopes)
        )
    except (ValueError, KeyError):
        raise Unavailable("not_configured") from None
    if subject:
        granted = granted.with_subject(subject)
    # The same verified transport covers token refresh and API requests.
    return build(
        api,
        version,
        http=AuthorizedHttp(granted, http=HTTPSOnlyHttp()),
        cache_discovery=False,
        static_discovery=True,
    )


def reason(exc: Exception) -> str:
    """A fixed reason for a Google failure; provider text is never kept."""
    if isinstance(exc, RefreshError):
        # Usually a scope not delegated to the client, or a wrong subject.
        return "permission_denied"
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", 0)
        if status == 403:
            return "permission_denied"
        if status in (400, 401, 404):
            return "not_configured"
    return "unavailable"


def execute(request, *, missing=None):
    """Run one request; failures become a reason. With ``missing``, a
    resource that does not exist answers ``missing`` instead."""
    try:
        found = request.execute(num_retries=2)
    except HttpError as exc:
        if missing is not None and getattr(exc.resp, "status", 0) == 404:
            return missing
        raise Unavailable(reason(exc)) from None
    except (GoogleAuthError, OSError, ValueError, httplib2.HttpLib2Error) as exc:
        raise Unavailable(reason(exc)) from None
    return found if isinstance(found, dict) else {}


def pages(request_for, key: str, limit: int) -> list[dict]:
    """Items under ``key`` across at most ``limit`` pages; a longer listing is
    refused, since a partial population would misstate a check."""
    found, token = [], None
    for _ in range(limit):
        page = execute(request_for(token))
        found.extend(i for i in page.get(key) or [] if isinstance(i, dict))
        token = page.get("nextPageToken")
        if not token:
            return found
    raise Unavailable("unavailable")
