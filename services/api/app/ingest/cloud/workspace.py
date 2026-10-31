"""Google Workspace posture: 2-Step Verification, administrators and unused
accounts for the customer of the administrator KEEN acts as.

KEEN uses the Google Workspace audit ingester's service account and delegated
administrator (``GOOGLE_WORKSPACE_IMPERSONATE``), asking only for the
read-only Directory scope. Every user the listing returns must belong to one
customer, so results for one customer never mix with another's. Workspace
activity comes from the audit ingester, which records the Reports API.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.core.config import settings
from app.ingest.cloud import google
from app.ingest.cloud.base import (
    MAX_IDENTITIES,
    Check,
    Identity,
    Provider,
    ScopeResult,
    Unavailable,
    clip,
    inactive_check,
    mfa_check,
    parse_time,
    plural,
)

EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,}")
DIRECTORY = "https://www.googleapis.com/auth/admin.directory.user.readonly"
USER_PAGE = 500
USER_PAGES = 60
NEVER = "1970-01-01T00:00:00.000Z"

USER_FIELDS = (
    "nextPageToken,users(id,primaryEmail,isAdmin,isDelegatedAdmin,"
    "isEnrolledIn2Sv,isEnforcedIn2Sv,suspended,archived,lastLoginTime,"
    "creationTime,customerId)"
)
CHECKS = {
    "identity.mfa": "2-Step Verification",
    "identity.mfa_enforced": "2-Step Verification enforcement",
    "identity.admin_mfa": "2-Step Verification for administrators",
    "identity.admins": "Administrators",
    "identity.inactive": "Unused accounts",
}


def _when(value) -> datetime | None:
    # Google reports "never" (e.g. never signed in) as the epoch.
    return None if str(value or "").strip() == NEVER else parse_time(value)


def users() -> list[dict]:
    subject = str(settings.google_workspace_impersonate or "").strip()
    if not EMAIL.fullmatch(subject):
        raise Unavailable("not_configured")
    info = google.key_info(
        str(settings.google_workspace_sa_keyfile or ""),
        str(settings.google_workspace_sa_json_b64 or ""),
        str(settings.google_workspace_sa_json or ""),
    )
    service = google.client("admin", "directory_v1", info, [DIRECTORY], subject)
    try:
        found = google.pages(
            lambda token: service.users().list(
                customer="my_customer",
                maxResults=USER_PAGE,
                projection="basic",
                fields=USER_FIELDS,
                **({"pageToken": token} if token else {}),
            ),
            "users",
            USER_PAGES,
        )
    finally:
        service.close()
    if len(found) > MAX_IDENTITIES:
        raise Unavailable("unavailable")
    customers = {str(u.get("customerId") or "") for u in found}
    if len(customers) > 1:
        raise Unavailable("not_configured")
    return found


def collect() -> list[ScopeResult]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    found = users()
    customer = clip(next((u.get("customerId") for u in found), "") or "my_customer", 64)
    domain = str(settings.google_workspace_impersonate).rpartition("@")[2]
    people, enforced = [], {}
    for user in found:
        if user.get("suspended") is True or user.get("archived") is True:
            continue
        email = clip(user.get("primaryEmail"))
        admin = user.get("isAdmin") is True or user.get("isDelegatedAdmin") is True
        person = Identity(
            name=email,
            email=email if EMAIL.fullmatch(email) else "",
            admin=admin,
            mfa=user.get("isEnrolledIn2Sv") is True,
            last_used=_when(user.get("lastLoginTime")),
            created=_when(user.get("creationTime")),
            detail="Super administrator"
            if user.get("isAdmin") is True
            else "Delegated administrator"
            if admin
            else "",
        )
        enforced[email] = user.get("isEnforcedIn2Sv") is True
        people.append(person)

    def checks():
        unenforced = [p.name for p in people if not enforced[p.name]]
        admins = [p for p in people if p.admin]
        return [
            mfa_check("identity.mfa", CHECKS["identity.mfa"], people, who="users"),
            Check(
                "identity.mfa_enforced",
                CHECKS["identity.mfa_enforced"],
                "attention" if unenforced else "met",
                f"Not enforced for {len(unenforced)} of {plural(len(people), 'user')}",
                unenforced,
            ),
            mfa_check(
                "identity.admin_mfa",
                CHECKS["identity.admin_mfa"],
                admins,
                who="administrators",
            ),
            Check(
                "identity.admins",
                CHECKS["identity.admins"],
                "info",
                f"{plural(len(admins), 'super or delegated administrator')}",
                [p.name for p in admins],
            ),
            inactive_check(
                "identity.inactive",
                CHECKS["identity.inactive"],
                people,
                settings.cloud_inactive_days,
                now,
            ),
        ]

    return [
        ScopeResult(
            customer,
            domain or customer,
            checks(),
            sorted(people, key=lambda p: p.name.lower()),
            kind="customer",
        )
    ]


PROVIDER = Provider(
    key="google_workspace",
    label="Google Workspace",
    vendor="Google Workspace",
    website="https://workspace.google.com",
    aliases=("Google", "Google LLC"),
    scope_noun="customer",
    checks=CHECKS,
    collect=collect,
    # Workspace activity is recorded by the Google Workspace audit ingester.
    activity=None,
    enabled_setting="google_workspace_posture_enabled",
)
