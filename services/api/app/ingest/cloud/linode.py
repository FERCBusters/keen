"""Akamai Cloud (Linode) posture and account activity for the account a
personal access token belongs to.

KEEN reads the account through the built-in HTTP client with a read-only
token, and names the scope after the account's external unique ID from
``/account``.
"""

from __future__ import annotations

import ipaddress
import json
import re
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.ingest.cloud.api import JsonApi, items
from app.ingest.cloud.base import (
    MAX_ACTIVITY,
    MAX_IDENTITIES,
    Activity,
    Check,
    Identity,
    Provider,
    ScopeResult,
    Unavailable,
    clip,
    guarded,
    inactive_check,
    mfa_check,
    plural,
)

ORIGIN = "https://api.linode.com"
TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{16,512}")
EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,}")
PAGE_SIZE = 500
MAX_PAGES = 50
ADMIN_PORTS = (22, 3389)
EVERYWHERE = (ipaddress.ip_network("0.0.0.0/0"), ipaddress.ip_network("::/0"))
# Each cluster's access list is one more request.
MAX_CLUSTERS = 200
CHECKS = {
    "identity.mfa": "Two-factor authentication",
    "identity.admin_mfa": "Two-factor authentication for unrestricted users",
    "identity.admins": "Unrestricted users",
    "identity.inactive": "Unused users",
    "network.admin_ports": "SSH and RDP open to the internet",
    "network.firewall_coverage": "Linodes behind a Cloud Firewall",
    "network.databases": "Databases open to the internet",
    "resilience.backups": "Linode backups",
    "kubernetes.control_plane": "LKE control planes",
}
# Read-only token scopes.
SCOPES = (
    "account:read_only",
    "events:read_only",
    "firewall:read_only",
    "linodes:read_only",
    "databases:read_only",
    "lke:read_only",
)


class Linode(JsonApi):
    def pages(self, path: str, *, filters=None, limit: int | None = None) -> list:
        """Every item of a listing; a longer one is refused, since a partial
        population would misstate a check."""
        found, complete = self.listing(path, filters=filters, limit=limit)
        if not complete:
            raise Unavailable("unavailable")
        return found

    def listing(self, path: str, *, filters=None, limit: int | None = None):
        """Items of a listing up to ``limit`` pages, and whether that was all."""
        headers = {"X-Filter": json.dumps(filters)} if filters else None
        found = []
        for page in range(1, (MAX_PAGES if limit is None else limit) + 1):
            body = self.get(
                path, params={"page": page, "page_size": PAGE_SIZE}, headers=headers
            )
            found.extend(items(body, "data"))
            pages = body.get("pages")
            if not isinstance(pages, int) or page >= pages:
                return found, True
        return found, False


def connect() -> Linode:
    token = str(settings.linode_token or "").strip()
    if not TOKEN.fullmatch(token):
        raise Unavailable("not_configured")
    return Linode(ORIGIN, {"Authorization": f"Bearer {token}"})


def _when(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.replace(tzinfo=None)


def identity(api: Linode, now: datetime) -> tuple[list[Check], list[Identity]]:
    users = api.pages("/v4/account/users")
    if len(users) > MAX_IDENTITIES:
        raise Unavailable("unavailable")
    people = []
    for user in users:
        email = clip(user.get("email"))
        last = (
            user.get("last_login") if isinstance(user.get("last_login"), dict) else None
        )
        unrestricted = user.get("restricted") is False
        people.append(
            Identity(
                name=clip(user.get("username")),
                email=email if EMAIL.fullmatch(email) else "",
                admin=unrestricted,
                mfa=user.get("tfa_enabled") is True,
                # The account reports only the latest attempt; a failed one
                # leaves the last successful sign-in unknown.
                last_used=_when(last.get("login_datetime"))
                if last and last.get("status") == "successful"
                else None,
                created=_when(user.get("password_created")) if last is None else None,
                detail="Unrestricted" if unrestricted else "Restricted",
            )
        )
    admins = [p for p in people if p.admin]
    return [
        mfa_check("identity.mfa", CHECKS["identity.mfa"], people, who="users"),
        mfa_check(
            "identity.admin_mfa",
            CHECKS["identity.admin_mfa"],
            admins,
            who="unrestricted users",
        ),
        Check(
            "identity.admins",
            CHECKS["identity.admins"],
            "info",
            f"{plural(len(admins), 'unrestricted user')} of {len(people)}",
            [p.name for p in admins],
        ),
        inactive_check(
            "identity.inactive",
            CHECKS["identity.inactive"],
            people,
            settings.cloud_inactive_days,
            now,
        ),
    ], people


def _ports(value) -> list[tuple[int, int]] | None:
    """The rule's port ranges; None means every port."""
    if value in (None, ""):
        return None
    out = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        low, _, high = part.partition("-")
        try:
            out.append((int(low), int(high or low)))
        except ValueError:
            return []
    return out


def everywhere(addresses) -> bool:
    if not isinstance(addresses, dict):
        return False
    for family in ("ipv4", "ipv6"):
        for value in addresses.get(family) or []:
            try:
                network = ipaddress.ip_network(str(value), strict=False)
            except ValueError:
                continue
            if network in EVERYWHERE:
                return True
    return False


def _covers(rule: dict, port: int) -> bool:
    if str(rule.get("protocol") or "").upper() not in ("TCP", ""):
        return False
    ranges = _ports(rule.get("ports"))
    return ranges is None or any(low <= port <= high for low, high in ranges)


def exposed_ports(firewall: dict) -> list[int]:
    """Remote-access ports the internet reaches through one firewall: rules
    apply in order, and traffic no rule matches meets the inbound policy."""
    rules = firewall.get("rules") if isinstance(firewall.get("rules"), dict) else {}
    inbound = [r for r in rules.get("inbound") or [] if isinstance(r, dict)]
    policy = str(rules.get("inbound_policy") or "").upper()
    out = []
    for port in ADMIN_PORTS:
        decision = policy
        for rule in inbound:
            if _covers(rule, port) and everywhere(rule.get("addresses")):
                decision = str(rule.get("action") or "").upper()
                break
        if decision == "ACCEPT":
            out.append(port)
    return out


def _protected(firewall: dict) -> set[str]:
    """The Linodes a firewall protects: assigned directly, or through one of
    the Linode's interfaces."""
    out = set()
    for entity in firewall.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        if entity.get("type") == "linode_interface":
            entity = entity.get("parent_entity")
            if not isinstance(entity, dict):
                continue
        if entity.get("type") == "linode":
            out.add(str(entity.get("id")))
    return out


def network(api: Linode) -> list[Check]:
    firewalls = [
        f for f in api.pages("/v4/networking/firewalls") if f.get("status") == "enabled"
    ]
    instances = api.pages("/v4/linode/instances")
    names = {
        str(i.get("id")): clip(i.get("label") or i.get("id"), 128) for i in instances
    }
    protected, exposing = set(), []
    for firewall in firewalls:
        devices = _protected(firewall)
        protected |= devices
        if devices and exposed_ports(firewall):
            exposing.append(clip(firewall.get("label") or firewall.get("id"), 128))
    unprotected = [name for ident, name in names.items() if ident not in protected]
    return [
        Check(
            "network.admin_ports",
            CHECKS["network.admin_ports"],
            "attention" if exposing else "met",
            f"{len(exposing)} of {plural(len(firewalls), 'enabled firewall')} let SSH or "
            "RDP in from anywhere",
            exposing,
        ),
        Check(
            "network.firewall_coverage",
            CHECKS["network.firewall_coverage"],
            "attention" if unprotected else "met",
            f"{len(unprotected)} of {plural(len(instances), 'Linode')} have no enabled "
            "Cloud Firewall",
            unprotected,
        ),
    ]


def databases(api: Linode) -> Check:
    found = api.pages("/v4/databases/instances")
    exposed = [
        clip(d.get("label") or d.get("id"), 128)
        for d in found
        if everywhere(
            {"ipv4": d.get("allow_list") or [], "ipv6": d.get("allow_list") or []}
        )
    ]
    return Check(
        "network.databases",
        CHECKS["network.databases"],
        "attention" if exposed else "met",
        f"{len(exposed)} of {plural(len(found), 'managed database')} allow every address",
        exposed,
    )


def backups(api: Linode) -> Check:
    instances = api.pages("/v4/linode/instances")
    default = api.get("/v4/account/settings").get("backups_enabled")
    without = [
        clip(i.get("label") or i.get("id"), 128)
        for i in instances
        if not (
            isinstance(i.get("backups"), dict) and i["backups"].get("enabled") is True
        )
    ]
    detail = f"{len(without)} of {plural(len(instances), 'Linode')} without backups"
    if default is True:
        detail += "; new Linodes get backups by default"
    return Check(
        "resilience.backups",
        CHECKS["resilience.backups"],
        "attention" if without else "met",
        detail,
        without,
    )


def kubernetes(api: Linode) -> Check:
    clusters = api.pages("/v4/lke/clusters")
    if len(clusters) > MAX_CLUSTERS:
        raise Unavailable("unavailable")
    flagged = []
    for cluster in clusters:
        ident = str(cluster.get("id"))
        if not re.fullmatch(r"[0-9]{1,12}", ident):
            raise Unavailable("unavailable")
        plane = cluster.get("control_plane")
        plane = plane if isinstance(plane, dict) else {}
        # Clusters from before control plane access lists have none.
        acl = api.get(f"/v4/lke/clusters/{ident}/control_plane_acl", missing={}).get(
            "acl"
        )
        acl = acl if isinstance(acl, dict) else {}
        problems = []
        if plane.get("high_availability") is not True:
            problems.append("no high availability")
        if acl.get("enabled") is not True or everywhere(acl.get("addresses")):
            problems.append("API open to every address")
        if problems:
            flagged.append(
                f"{clip(cluster.get('label') or ident, 100)}: {', '.join(problems)}"
            )
    return Check(
        "kubernetes.control_plane",
        CHECKS["kubernetes.control_plane"],
        "attention" if flagged else "met",
        f"{len(flagged)} of {plural(len(clusters), 'cluster')} lack high availability or "
        "an access list",
        flagged,
    )


def collect() -> list[ScopeResult]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as api:
        account = api.get("/v4/account")
        key = clip(account.get("euuid"), 64).upper()
        if not re.fullmatch(r"[0-9A-F][0-9A-F-]{15,63}", key):
            raise Unavailable("unavailable")
        people: list[Identity] = []

        def users():
            checks, found = identity(api, now)
            people.extend(found)
            return checks

        identity_keys = [
            "identity.mfa",
            "identity.admin_mfa",
            "identity.admins",
            "identity.inactive",
        ]
        checks = guarded(CHECKS, identity_keys, users)
        checks += guarded(
            CHECKS,
            ["network.admin_ports", "network.firewall_coverage"],
            lambda: network(api),
        )
        checks += guarded(CHECKS, ["network.databases"], lambda: [databases(api)])
        checks += guarded(CHECKS, ["resilience.backups"], lambda: [backups(api)])
        checks += guarded(
            CHECKS, ["kubernetes.control_plane"], lambda: [kubernetes(api)]
        )
        label = clip(account.get("company") or account.get("email") or key, 128)
        return [ScopeResult(key, label, checks, people)]


# --- activity --------------------------------------------------------------------


def _naive(when: datetime) -> str:
    return when.replace(tzinfo=None, microsecond=0).isoformat()


def _text(value, limit=128) -> str:
    return value[:limit] if isinstance(value, str) else ""


def login_entry(login: dict, account: str) -> Activity | None:
    when = _when(login.get("datetime"))
    ident = login.get("id")
    if when is None or not isinstance(ident, int):
        return None
    outcome = {"successful": "success", "failed": "failure"}.get(
        login.get("status"), "info"
    )
    who = _text(login.get("username"), 256)
    return Activity(
        id=f"login:{ident}",
        at=when,
        scope=account,
        action="login",
        actor=who,
        outcome=outcome,
        summary=f"Cloud Manager sign-in by {who or 'unknown'} ({outcome})",
        detail={"service": "account", "source_ip": _text(login.get("ip"), 64)},
        raw=login,
    )


def event_entry(event: dict, account: str) -> Activity | None:
    when = _when(event.get("created"))
    ident = event.get("id")
    action = _text(event.get("action"))
    if when is None or not isinstance(ident, int) or not action:
        return None
    outcome = {
        "failed": "failure",
        "finished": "success",
        "notification": "success",
    }.get(event.get("status"), "info")
    entity = event.get("entity") if isinstance(event.get("entity"), dict) else {}
    who = _text(event.get("username"), 256)
    return Activity(
        id=f"event:{ident}",
        at=when,
        scope=account,
        action=action,
        actor=who,
        outcome=outcome,
        summary=f"{action} by {who or 'Linode'} ({outcome})",
        detail={
            k: v
            for k, v in {
                "service": "account",
                "resource_type": _text(entity.get("type"), 64),
                "resource": _text(entity.get("label"), 128),
            }.items()
            if v
        },
        raw=event,
    )


def activity(since: datetime, until: datetime) -> tuple[list[Activity], bool]:
    """Cloud Manager sign-ins and account events in [since, until)."""
    with connect() as api:
        account = clip(api.get("/v4/account").get("euuid"), 64).upper()
        logins, done_logins = api.listing("/v4/account/logins", limit=10)
        filters = {
            "+and": [
                {"created": {"+gte": _naive(since)}},
                {"created": {"+lt": _naive(until)}},
            ],
            "+order_by": "created",
            "+order": "desc",
        }
        events, done_events = api.listing(
            "/v4/account/events", filters=filters, limit=MAX_ACTIVITY // PAGE_SIZE + 1
        )
    found = [login_entry(item, account) for item in logins]
    found += [event_entry(item, account) for item in events]
    out = sorted(
        (e for e in found if e is not None and since <= e.at < until),
        key=lambda e: e.at,
    )
    if len(out) > MAX_ACTIVITY:
        return out[-MAX_ACTIVITY:], False
    return out, done_logins and done_events


PROVIDER = Provider(
    key="linode",
    label="Akamai Cloud (Linode)",
    vendor="Akamai (Linode)",
    website="https://www.linode.com",
    aliases=("Linode", "Akamai", "Akamai Technologies", "Akamai Cloud"),
    scope_noun="account",
    checks=CHECKS,
    collect=collect,
    activity=activity,
    activity_lag=timedelta(minutes=15),
)
