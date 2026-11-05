"""JSON APIs read through KEEN's built-in HTTP client.

Every request goes through ``ingest/http.py``: DNS validation, address pinning,
original-host TLS verification, response limits and no redirects. A session is
bound to one origin; a link to any other origin, such as a paging link a
response supplies, is refused before the credential is sent. Failures become
reason codes and never carry the provider's text.
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

import httpx

from app.ingest import http
from app.ingest.cloud.base import Unavailable

TIMEOUT = 30.0
# A throttled request waits at most this long, once, before the read stops.
MAX_RETRY_WAIT = 10.0


def reason(response: httpx.Response) -> str:
    status = response.status_code
    if status == 401:
        return "not_configured"
    if status == 403:
        return "permission_denied"
    if status in (400, 404):
        return "not_configured"
    return "unavailable"


class JsonApi:
    """One origin and its credential headers; close it when the read ends."""

    def __init__(self, origin: str, headers: dict, *, explain=reason, client=None):
        self.origin = origin
        self._headers = {"Accept": "application/json", **headers}
        self._explain = explain
        self.http = client or http.client(timeout=TIMEOUT)

    def close(self):
        self._headers.clear()
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def send(
        self, method: str, url: str, *, params=None, body=None, headers=None
    ) -> httpx.Response:
        parts = urlsplit(url)
        if f"{parts.scheme}://{parts.netloc}" != self.origin:
            # A link to another origin never receives the credential.
            raise Unavailable("unavailable")
        sent = {**self._headers, **(headers or {})}
        response = self._request(method, url, params, body, sent)
        if response.status_code == 429:
            # Throttled: wait as asked, within MAX_RETRY_WAIT, then try once more.
            try:
                wait = float(response.headers.get("Retry-After") or 1)
            except ValueError:
                wait = 1.0
            time.sleep(min(max(wait, 0.0), MAX_RETRY_WAIT))
            response = self._request(method, url, params, body, sent)
        return response

    def _request(self, method, url, params, body, headers) -> httpx.Response:
        try:
            return self.http.request(
                method, url, params=params, json=body, headers=headers
            )
        except (httpx.HTTPError, ValueError, TimeoutError, OSError):
            raise Unavailable("unavailable") from None

    def body(self, response: httpx.Response, missing=None) -> dict:
        if missing is not None and response.status_code == 404:
            return missing
        if response.status_code != 200:
            raise Unavailable(self._explain(response))
        try:
            found = response.json()
        except ValueError:
            raise Unavailable("unavailable") from None
        if not isinstance(found, dict):
            raise Unavailable("unavailable")
        return found

    def get(self, path: str, *, params=None, missing=None, headers=None) -> dict:
        """One GET; with ``missing``, a resource that does not exist answers
        ``missing`` instead of a reason."""
        response = self.send("GET", self.origin + path, params=params, headers=headers)
        return self.body(response, missing)

    def post(self, path: str, body: dict, *, params=None) -> dict:
        return self.body(
            self.send("POST", self.origin + path, params=params, body=body)
        )


def items(found: dict, key: str) -> list[dict]:
    value = found.get(key)
    if not isinstance(value, list):
        raise Unavailable("unavailable")
    return [v for v in value if isinstance(v, dict)]
