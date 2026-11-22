"""Cloudflare account audit logs.

Read-only. The log is read with an API token in time windows, oldest first.
Each page of a window is stored and committed with the paging value that
resumes the window, and the cursor moves to a window's end only once every
page of it is stored (``event_api.read_windows``). Cloudflare gives each entry
an identifier, so an entry read again is stored once.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import httpx2 as httpx
from myf.core.datetime_utils import utc_now_naive
from myf.security.errors import collection_error
from myf.security.redaction import REDACTED, is_sensitive_key
from myf.security.regex import scan_pattern

from app.ingest.connections import connection_runs, load_document, namespace, settings
from app.ingest.event_api import (
    EventApiError,
    Page,
    canonical,
    identity,
    label,
    member,
    one_stream_config,
    paging_token,
    read_json,
    read_windows,
    records,
    short,
    text,
    timestamp,
)
from app.ingest.http import client as ingestion_client
from app.ingest.http import no_cookies
from app.ingest.registry import HTTP_QUEUE, Source, register
from app.services.ingestion_pause import pausable

PRODUCT = "Cloudflare"
# The API is served only by Cloudflare; the API token is sent nowhere else.
API = "https://api.cloudflare.com/client/v4"
STREAM = "audit"
# The most entries Cloudflare returns in one page; a page holding more is
# refused, whatever page size was asked for.
PAGE_ROWS = 1000
PAGE_SIZE = 500
# A Cloudflare account ID, which is also a segment of the request path.
ACCOUNT_ID = scan_pattern(r"[0-9a-f]{32}")
_TOKEN = scan_pattern(r"[\x21-\x7e]{1,512}")


class CloudflareError(EventApiError):
    """Operator-safe message: never contains credentials or response bodies."""


def validate_config(config):
    return one_stream_config(
        config,
        product=PRODUCT,
        stream=STREAM,
        error=CloudflareError,
        extra={"page_size": (PAGE_SIZE, 1, PAGE_ROWS)},
    )


def _names_a_secret(item: dict) -> bool:
    """Whether an entry's resource, or the path its request was made at, is
    named like a secret.

    Such an entry is about an API token, a tunnel's token, a Worker's secret
    and the like, whose request or response can hold the secret itself under
    ordinary names (a token's ``value``, a secret's ``text``). The resource's
    product and type, and the request's path up to any query, are read in
    parts at their dots and slashes, so a secret Cloudflare files under a
    product named otherwise is still known by the path it was set at. Parts
    are matched against the keys Keen's redaction treats as sensitive.
    """
    resource = member(item, "resource")
    named = (
        resource.get("product"),
        resource.get("type"),
        member(item, "raw").get("uri"),
    )
    for value in named:
        if isinstance(value, str):
            path = value[:2048].partition("?")[0].replace("/", ".")
            if any(is_sensitive_key(part) for part in path.split(".") if part):
                return True
    return False


def _withheld(item: dict) -> dict:
    """An entry without the request and response of a resource named like a
    secret; every other entry as Cloudflare listed it."""
    if not _names_a_secret(item):
        return item
    kept = {
        name: REDACTED if name in ("request", "response") else value
        for name, value in member(item, "resource").items()
    }
    return {**item, "resource": kept}


def _read(account, entry, item, collected):
    """What is stored for one audit log entry."""
    item = _withheld(item)
    action = member(item, "action")
    actor = member(item, "actor")
    resource = member(item, "resource")
    # A token's name and identifier are withheld with the stored entry, so the
    # actor is named by email or by Cloudflare's identifier for them.
    who = short(actor.get("email")) or short(actor.get("id"))
    result = text(action.get("result"))
    outcome = result if result in ("success", "failure") else "info"
    # An account-wide action names its product; one on a user, its scope.
    product = resource.get("product")
    area = label(product if product else resource.get("scope"))
    where = short(member(item, "zone").get("name"))
    summary = "Cloudflare: " + (short(action.get("description")) or "action")
    if where:
        summary += f" in {where}"
    payload = {"stream": STREAM, "event": item}
    content = canonical(payload)
    ident = text(item.get("id"))
    # An entry without an identifier is told apart by its content.
    digest = identity(
        API, account, STREAM, ident or hashlib.sha256(content).hexdigest()
    )
    return dict(
        # An entry without a readable time is kept, dated when it was collected.
        timestamp=timestamp(action.get("time")) or collected,
        source="cloudflare",
        system=entry.get("label") or PRODUCT,
        actor=who,
        action=f"{area}.{label(action.get('type'))}",
        outcome=outcome,
        severity=6 if outcome == "failure" else 3,
        summary=summary + (f" by {who}" if who else ""),
        raw_pointer={"cloudflare": {"section": "streams", "stream": STREAM}},
        normalized_payload=payload,
        external_id=digest,
        artifact_kind="cloudflare_" + STREAM,
        artifact_bytes=content,
        artifact_content_type="application/json",
        artifact_key=f"cloudflare/{STREAM}/{digest}.json",
        captured_by="keen:cloudflare",
    )


def _when(value) -> str:
    return value.isoformat(timespec="seconds") + "Z"


def _collect(db, client, account, entry, config):
    """Read the account's audit log in time windows, oldest first."""
    url = f"{API}/accounts/{account}/logs/audit"
    size = config.get("page_size", PAGE_SIZE)

    def request(since, until, token):
        params = {
            "since": _when(since),
            # One second past the window's end, so an entry at its last
            # second is read whether Cloudflare's bound includes it or not.
            "before": _when(until + timedelta(seconds=1)),
            "direction": "asc",
            "limit": size,
        }
        if token:
            params["cursor"] = token
        data = read_json(client, "GET", url, product=PRODUCT, params=params)
        if data.get("success") is not True:
            raise CloudflareError(
                "Cloudflare refused the request; check the API token's permissions"
            )
        rows = data.get("result")
        items, invalid = records(rows, PAGE_ROWS, PRODUCT)
        # A page without entries ends the window, whatever paging value it
        # carries: Cloudflare may return one with the last page.
        following = paging_token(member(data, "result_info").get("cursor"), PRODUCT)
        return Page(items, invalid, following if rows else None)

    return read_windows(
        db,
        name=namespace("cloudflare:" + hashlib.sha256(account.encode()).hexdigest()),
        config=config,
        product=PRODUCT,
        stream=STREAM,
        # Windows end on a whole second, which is as finely as they are asked for.
        now=lambda: utc_now_naive().replace(microsecond=0),
        request=request,
        read=lambda item, collected: _read(account, entry, item, collected),
    )


@pausable("cloudflare")
@connection_runs("cloudflare")
def ingest_cloudflare_all(db):
    if settings.demo_mode or not settings.cloudflare_enabled:
        return [{"skipped": True, "reason": "Cloudflare disabled (or demo mode)"}]
    account = str(settings.cloudflare_account_id or "").strip()
    token = str(settings.cloudflare_api_token or "").strip()
    if not ACCOUNT_ID.fullmatch(account) or not _TOKEN.fullmatch(token):
        return [
            {
                "error": "Set the Cloudflare account ID and an API token that "
                "can read the account's audit logs"
            }
        ]
    try:
        config = validate_config(
            load_document("cloudflare", settings.cloudflare_config_path)
        )
    except (ValueError, OSError):
        return [{"error": "Invalid Cloudflare configuration; check streams"}]
    if not config.get("streams"):
        return [{"skipped": True, "reason": "Add the Cloudflare audit stream"}]
    try:
        # The token goes to Cloudflare's API alone, and no cookie is kept.
        with ingestion_client(
            timeout=httpx.Timeout(30, connect=10),
            verify=True,
            follow_redirects=False,
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
            cookies=no_cookies(),
        ) as client:
            return [_collect(db, client, account, config["streams"][0], config)]
    except Exception as exc:
        db.rollback()
        return [
            {
                "stream": STREAM,
                "error": str(exc)
                if isinstance(exc, EventApiError)
                else collection_error(exc),
            }
        ]


register(
    Source(
        name="cloudflare",
        module=__name__,
        queue=HTTP_QUEUE,
        schedule=float(settings.cloudflare_poll_seconds),
        config_setting="cloudflare_config_path",
        public_fields=("account_id",),
        secret_fields=("api_token",),
        input_sections=("streams",),
        section="streams",
        id_field="stream",
        definition_sections=("streams",),
        key_field="stream",
        demo=False,
        streams=(STREAM,),
        validate_config=validate_config,
    )
)
