"""Bitwarden organisation event log, read through the Bitwarden Public API.

Read-only. The log is read in time windows, oldest first. Each page of a
window is stored and committed before the next is requested, with the paging
value that resumes the window, and the cursor moves to a window's end only
once every page of it is stored.
"""

from __future__ import annotations

import hashlib

import httpx2 as httpx

from app.core.datetime_utils import utc_now_naive
from app.ingest.connections import connection_runs, load_document, namespace, settings
from app.ingest.event_api import (
    EventApiError,
    Page,
    canonical,
    one_stream_config,
    paging_token,
    read_json,
    read_windows,
    records,
    safe_base,
    text,
    timestamp,
)
from app.ingest.http import client as ingestion_client
from app.ingest.registry import HTTP_QUEUE, Source, register
from app.security.errors import collection_error
from app.security.regex import scan_pattern
from app.services.ingestion_pause import pausable

PRODUCT = "Bitwarden"
# Bitwarden chooses the page size; a page holding more than this is refused.
PAGE_ROWS = 1000
_CLIENT_ID = scan_pattern(r"organization\.[0-9A-Fa-f-]{36}")
_SECRET = scan_pattern(r"[\x21-\x7e]{1,512}")
_ACCESS_TOKEN = scan_pattern(r"[\x21-\x7e]{1,16384}")

# Event type codes from Bitwarden's event log reference: action and description.
EVENT_TYPES = {
    1000: ("user.logged_in", "Logged in"),
    1001: ("user.changed_password", "Changed account password"),
    1002: ("user.updated_two_step_login", "Enabled or updated two-step login"),
    1003: ("user.disabled_two_step_login", "Disabled two-step login"),
    1004: ("user.recovered_two_step_login", "Recovered account from two-step login"),
    1005: ("user.failed_login", "Login attempt failed with incorrect password"),
    1006: (
        "user.failed_two_step_login",
        "Login attempt failed with incorrect two-step login",
    ),
    1007: ("user.exported_vault", "Exported individual vault items"),
    1008: (
        "user.updated_recovery_password",
        "Updated a password issued through account recovery",
    ),
    1009: ("user.migrated_key_connector", "Migrated decryption key to Key Connector"),
    1010: ("user.requested_device_approval", "Requested device approval"),
    1100: ("item.created", "Created item"),
    1101: ("item.edited", "Edited item"),
    1102: ("item.deleted", "Permanently deleted item"),
    1103: ("item.attachment_created", "Created attachment for item"),
    1104: ("item.attachment_deleted", "Deleted attachment for item"),
    1105: ("item.shared", "Moved item to an organisation"),
    1106: ("item.collections_edited", "Edited collections for item"),
    1107: ("item.viewed", "Viewed item"),
    1108: ("item.password_viewed", "Viewed password for item"),
    1109: ("item.hidden_field_viewed", "Viewed hidden field for item"),
    1110: ("item.security_code_viewed", "Viewed security code for item"),
    1111: ("item.password_copied", "Copied password for item"),
    1112: ("item.hidden_field_copied", "Copied hidden field for item"),
    1113: ("item.security_code_copied", "Copied security code for item"),
    1114: ("item.autofilled", "Autofilled item"),
    1115: ("item.trashed", "Sent item to trash"),
    1116: ("item.restored", "Restored item"),
    1117: ("item.card_number_viewed", "Viewed card number for item"),
    1300: ("collection.created", "Created collection"),
    1301: ("collection.edited", "Edited collection"),
    1302: ("collection.deleted", "Deleted collection"),
    1400: ("group.created", "Created group"),
    1401: ("group.edited", "Edited group"),
    1402: ("group.deleted", "Deleted group"),
    1500: ("member.invited", "Invited user"),
    1501: ("member.confirmed", "Confirmed user"),
    1502: ("member.edited", "Edited user"),
    1503: ("member.removed", "Removed user"),
    1504: ("member.groups_edited", "Edited groups for user"),
    1505: ("member.sso_unlinked", "Unlinked SSO for user"),
    1506: ("member.recovery_enrolled", "User enrolled in account recovery"),
    1507: ("member.recovery_withdrawn", "User withdrew from account recovery"),
    1508: ("member.password_reset", "Master password reset for user"),
    1509: ("member.sso_link_reset", "Reset SSO link for user"),
    1510: ("member.first_sso_login", "User logged in using SSO for the first time"),
    1511: ("member.revoked", "Revoked organisation access for user"),
    1512: ("member.restored", "Restored organisation access for user"),
    1513: ("member.device_approved", "Approved device for user"),
    1514: ("member.device_denied", "Denied device for user"),
    1515: ("member.deleted", "Deleted user"),
    1516: ("member.left", "User left organisation"),
    1600: ("organization.settings_edited", "Edited organisation settings"),
    1601: ("organization.vault_purged", "Purged organisation vault"),
    1602: ("organization.vault_exported", "Exported organisation vault"),
    1603: (
        "organization.provider_access",
        "Organisation vault accessed by a managing Provider",
    ),
    1604: ("organization.sso_enabled", "Organisation enabled SSO"),
    1605: ("organization.sso_disabled", "Organisation disabled SSO"),
    1606: ("organization.key_connector_enabled", "Organisation enabled Key Connector"),
    1607: (
        "organization.key_connector_disabled",
        "Organisation disabled Key Connector",
    ),
    1608: ("organization.sponsorships_synced", "Families sponsorships synced"),
    1609: (
        "organization.collection_management_edited",
        "Modified collection management setting",
    ),
    1700: ("policy.modified", "Modified policy"),
    2000: ("domain.added", "Added domain"),
    2001: ("domain.removed", "Removed domain"),
    2002: ("domain.verified", "Domain verified"),
    2003: ("domain.not_verified", "Domain not verified"),
    2100: ("secret.accessed", "Accessed secret"),
}
FAILURES = frozenset((1005, 1006))


class BitwardenError(EventApiError):
    """Operator-safe message: never contains credentials or response bodies."""


def validate_config(config):
    return one_stream_config(
        config, product=PRODUCT, stream="events", error=BitwardenError
    )


def _access_token(client, identity, client_id, secret):
    """An organisation access token for this run.

    The client secret is sent to the identity endpoint alone, in the request
    body, and the token it returns is sent to the API endpoint alone.
    """
    data = read_json(
        client,
        "POST",
        identity + "/connect/token",
        product="Bitwarden sign-in",
        data={
            "grant_type": "client_credentials",
            "scope": "api.organization",
            "client_id": client_id,
            "client_secret": secret,
        },
    )
    token = data.get("access_token")
    kind = data.get("token_type")
    if (
        not isinstance(token, str)
        or not _ACCESS_TOKEN.fullmatch(token)
        or not isinstance(kind, str)
        or kind.lower() != "bearer"
    ):
        raise BitwardenError("Bitwarden sign-in returned no usable access token")
    return token


def _read(base, entry, item, collected):
    """What is stored for one event."""
    code = item.get("type")
    known = EVENT_TYPES.get(code) if type(code) is int else None
    if known:
        action, description = known
    elif type(code) is int:
        action, description = f"event.{code}", f"Event type {code}"
    else:
        action, description = "event.unknown", "Event of an unreadable type"
    actor = text(item.get("actingUserId"))
    outcome = "failure" if known and code in FAILURES else "info"
    payload = {"stream": "events", "event": item}
    content = canonical(payload)
    # Bitwarden gives an event no identifier, so its content tells it apart
    # and a window read again stores nothing twice.
    digest = hashlib.sha256(base.encode() + b"\n" + content).hexdigest()
    return dict(
        # An event without a readable date is kept, dated when it was collected.
        timestamp=timestamp(item.get("date")) or collected,
        source="bitwarden",
        system=entry.get("label") or PRODUCT,
        actor=actor,
        action=action,
        outcome=outcome,
        severity=6 if outcome == "failure" else 3,
        summary=f"Bitwarden: {description}" + (f" by {actor}" if actor else ""),
        raw_pointer={
            "bitwarden": {
                "section": "streams",
                "stream": "events",
                "type": code if type(code) is int else None,
            }
        },
        normalized_payload=payload,
        external_id=digest,
        artifact_kind="bitwarden_event",
        artifact_bytes=content,
        artifact_content_type="application/json",
        artifact_key=f"bitwarden/events/{digest}.json",
        captured_by="keen:bitwarden",
    )


def _collect(db, client, base, headers, entry, config):
    """Read the event log in time windows, oldest first."""

    def request(since, until, token):
        params = {"start": since.isoformat() + "Z", "end": until.isoformat() + "Z"}
        if token:
            params["continuationToken"] = token
        data = read_json(
            client,
            "GET",
            base + "/public/events",
            product=PRODUCT,
            params=params,
            headers=headers,
        )
        items, invalid = records(data.get("data"), PAGE_ROWS, PRODUCT)
        return Page(
            items, invalid, paging_token(data.get("continuationToken"), PRODUCT)
        )

    digest = hashlib.sha256(base.encode()).hexdigest()
    return read_windows(
        db,
        name=namespace("bitwarden:" + digest),
        config=config,
        product=PRODUCT,
        stream="events",
        now=utc_now_naive,
        request=request,
        read=lambda item, collected: _read(base, entry, item, collected),
    )


@pausable("bitwarden")
@connection_runs("bitwarden")
def ingest_bitwarden_all(db):
    if settings.demo_mode or not settings.bitwarden_enabled:
        return [{"skipped": True, "reason": "Bitwarden disabled (or demo mode)"}]
    base = safe_base(settings.bitwarden_base_url)
    identity = safe_base(settings.bitwarden_identity_url)
    if not base or not identity:
        return [
            {
                "error": "Configure safe HTTPS Bitwarden API and identity URLs "
                "without credentials, query or fragment"
            }
        ]
    client_id = str(settings.bitwarden_client_id or "").strip()
    secret = str(settings.bitwarden_client_secret or "").strip()
    if not _CLIENT_ID.fullmatch(client_id) or not _SECRET.fullmatch(secret):
        return [
            {
                "error": "Enter the Bitwarden organisation's client ID, which "
                "begins organization., and the client secret issued with it"
            }
        ]
    try:
        config = validate_config(
            load_document("bitwarden", settings.bitwarden_config_path)
        )
    except (ValueError, OSError):
        return [{"error": "Invalid Bitwarden configuration; check streams"}]
    if not config.get("streams"):
        return [{"skipped": True, "reason": "Add the Bitwarden events stream"}]
    entry = config["streams"][0]
    try:
        # No credential is a default header: each request names its own.
        with ingestion_client(
            timeout=httpx.Timeout(30, connect=10),
            verify=True,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        ) as client:
            token = _access_token(client, identity, client_id, secret)
            return [
                _collect(
                    db,
                    client,
                    base,
                    {"Authorization": "Bearer " + token},
                    entry,
                    config,
                )
            ]
    except Exception as exc:
        db.rollback()
        return [
            {
                "stream": "events",
                "error": str(exc)
                if isinstance(exc, EventApiError)
                else collection_error(exc),
            }
        ]


register(
    Source(
        name="bitwarden",
        module=__name__,
        queue=HTTP_QUEUE,
        schedule=float(settings.bitwarden_poll_seconds),
        config_setting="bitwarden_config_path",
        public_fields=("base_url", "identity_url", "client_id"),
        secret_fields=("client_secret",),
        input_sections=("streams",),
        section="streams",
        id_field="stream",
        definition_sections=("streams",),
        key_field="stream",
        demo=False,
        validate_config=validate_config,
    )
)
