"""DigitalOcean posture and resource actions for the team a token belongs to.

KEEN reads the team through the built-in HTTP client with a custom-scoped,
read-only token, and names the scope after the team's ID from
``/v2/account``. Listings are paged by number; links in a response are never
followed. DigitalOcean publishes no API for team members, so KEEN lists none.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timedelta

from app.core.config import settings
from app.ingest.cloud.api import JsonApi, items
from app.ingest.cloud.base import (
    MAX_ACTIVITY,
    Activity,
    Check,
    Provider,
    ScopeResult,
    Unavailable,
    clip,
    guarded,
    parse_time,
    plural,
    text_field,
)

ORIGIN = "https://api.digitalocean.com"
# Team and account IDs are 40 hexadecimal digits; UUIDs are accepted too.
IDENT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
TOKEN = re.compile(r"[A-Za-z0-9_.-]{16,512}")
PAGE_SIZE = 200
MAX_PAGES = 100
ADMIN_PORTS = (22, 3389)
EVERYWHERE = (ipaddress.ip_network("0.0.0.0/0"), ipaddress.ip_network("::/0"))
# Each managed database's trusted sources are one more request.
MAX_DATABASES = 200
CHECKS = {
    "network.admin_ports": "SSH and RDP open to the internet",
    "network.firewall_coverage": "Droplets behind a Cloud Firewall",
    "network.databases": "Databases open to the internet",
    "resilience.backups": "Droplet backups",
    "kubernetes.control_plane": "Kubernetes clusters",
    "findings": "Security scan findings",
}
# Custom token scopes, all read-only.
SCOPES = (
    "account:read",
    "actions:read",
    "droplet:read",
    "firewall:read",
    "database:read",
    "kubernetes:read",
    "tag:read",
    "security:read",
)


class DigitalOcean(JsonApi):
    def listing(self, path: str, key: str, *, params=None, limit: int | None = None):
        """Items under ``key`` up to ``limit`` pages, and whether that was all."""
        found = []
        for page in range(1, (MAX_PAGES if limit is None else limit) + 1):
            body = self.get(
                path, params={**(params or {}), "page": page, "per_page": PAGE_SIZE}
            )
            batch = items(body, key)
            found.extend(batch)
            links = body.get("links") if isinstance(body.get("links"), dict) else {}
            pages = links.get("pages") if isinstance(links.get("pages"), dict) else {}
            if not pages.get("next") or not batch:
                return found, True
        return found, False

    def pages(self, path: str, key: str) -> list:
        """Every item of a listing; a longer one is refused, since a partial
        population would misstate a check."""
        found, complete = self.listing(path, key)
        if not complete:
            raise Unavailable("unavailable")
        return found


def connect() -> DigitalOcean:
    token = str(settings.digitalocean_token or "").strip()
    if not TOKEN.fullmatch(token):
        raise Unavailable("not_configured")
    return DigitalOcean(ORIGIN, {"Authorization": f"Bearer {token}"})


def team(api: DigitalOcean) -> tuple[str, str, str]:
    """The scope's ID, name and kind: the token's team, or its account when
    the token is not used in a team context."""
    account = api.get("/v2/account").get("account")
    account = account if isinstance(account, dict) else {}
    found = account.get("team") if isinstance(account.get("team"), dict) else None
    source, kind = (found, "team") if found else (account, "account")
    key = str(source.get("uuid") or "").lower()
    if not IDENT.fullmatch(key):
        raise Unavailable("unavailable")
    name = source.get("name") if found else source.get("email")
    return key, clip(name or key, 128), kind


def _covers(rule: dict, port: int) -> bool:
    if str(rule.get("protocol") or "").lower() != "tcp":
        return False
    text = str(rule.get("ports") or "").strip().lower()
    if text in ("0", "all", ""):
        return True
    low, _, high = text.partition("-")
    try:
        return int(low) <= port <= int(high or low)
    except ValueError:
        return False


def everywhere(addresses) -> bool:
    for value in addresses or []:
        try:
            network = ipaddress.ip_network(str(value), strict=False)
        except ValueError:
            continue
        if network in EVERYWHERE:
            return True
    return False


def exposed_ports(firewall: dict) -> list[int]:
    """Remote-access ports the internet reaches through one Cloud Firewall,
    which denies whatever no rule allows. A deny rule takes precedence, so
    one that covers the port for every address closes it."""
    rules = [r for r in firewall.get("inbound_rules") or [] if isinstance(r, dict)]
    out = []
    for port in ADMIN_PORTS:
        allowed = denied = False
        for rule in rules:
            sources = (
                rule.get("sources") if isinstance(rule.get("sources"), dict) else {}
            )
            if not _covers(rule, port) or not everywhere(sources.get("addresses")):
                continue
            if str(rule.get("action") or "allow").lower() == "deny":
                denied = True
            else:
                allowed = True
        if allowed and not denied:
            out.append(port)
    return out


def _protected(firewall: dict, droplets: list[dict]) -> set[str]:
    ids = {str(i) for i in firewall.get("droplet_ids") or []}
    tags = {str(t) for t in firewall.get("tags") or [] if isinstance(t, str)}
    return {
        str(d.get("id"))
        for d in droplets
        if str(d.get("id")) in ids or tags & {str(t) for t in d.get("tags") or []}
    }


def network(api: DigitalOcean) -> list[Check]:
    firewalls = api.pages("/v2/firewalls", "firewalls")
    droplets = api.pages("/v2/droplets", "droplets")
    protected, exposing = set(), []
    for firewall in firewalls:
        devices = _protected(firewall, droplets)
        protected |= devices
        if devices and exposed_ports(firewall):
            exposing.append(clip(firewall.get("name") or firewall.get("id"), 128))
    unprotected = [
        clip(d.get("name") or d.get("id"), 128)
        for d in droplets
        if str(d.get("id")) not in protected
    ]
    return [
        Check(
            "network.admin_ports",
            CHECKS["network.admin_ports"],
            "attention" if exposing else "met",
            f"{len(exposing)} of {plural(len(firewalls), 'firewall')} let SSH or RDP in "
            "from anywhere",
            exposing,
        ),
        Check(
            "network.firewall_coverage",
            CHECKS["network.firewall_coverage"],
            "attention" if unprotected else "met",
            f"{len(unprotected)} of {plural(len(droplets), 'Droplet')} have no Cloud Firewall",
            unprotected,
        ),
    ]


def databases(api: DigitalOcean) -> Check:
    """Managed databases that accept connections from anywhere: those with no
    trusted sources, or a trusted address range that is the whole internet."""
    found = api.pages("/v2/databases", "databases")
    if len(found) > MAX_DATABASES:
        raise Unavailable("unavailable")
    exposed = []
    for database in found:
        ident = str(database.get("id") or "")
        if not re.fullmatch(r"[0-9a-f-]{36}", ident):
            raise Unavailable("unavailable")
        rules = api.get(f"/v2/databases/{ident}/firewall").get("rules")
        if not isinstance(rules, list):
            raise Unavailable("unavailable")
        addresses = [
            r.get("value")
            for r in rules
            if isinstance(r, dict) and r.get("type") == "ip_addr"
        ]
        if not rules or everywhere(addresses):
            exposed.append(clip(database.get("name") or ident, 128))
    return Check(
        "network.databases",
        CHECKS["network.databases"],
        "attention" if exposed else "met",
        f"{len(exposed)} of {plural(len(found), 'managed database')} accept connections "
        "from anywhere",
        exposed,
    )


def backups(api: DigitalOcean) -> Check:
    droplets = api.pages("/v2/droplets", "droplets")
    without = [
        clip(d.get("name") or d.get("id"), 128)
        for d in droplets
        if "backups" not in (d.get("features") or [])
    ]
    return Check(
        "resilience.backups",
        CHECKS["resilience.backups"],
        "attention" if without else "met",
        f"{len(without)} of {plural(len(droplets), 'Droplet')} without backups",
        without,
    )


def kubernetes(api: DigitalOcean) -> Check:
    clusters = api.pages("/v2/kubernetes/clusters", "kubernetes_clusters")
    flagged = []
    for cluster in clusters:
        problems = []
        if cluster.get("ha") is not True:
            problems.append("no high availability")
        if cluster.get("auto_upgrade") is not True:
            problems.append("no automatic upgrades")
        if problems:
            flagged.append(
                f"{clip(cluster.get('name') or cluster.get('id'), 100)}: {', '.join(problems)}"
            )
    return Check(
        "kubernetes.control_plane",
        CHECKS["kubernetes.control_plane"],
        "attention" if flagged else "met",
        f"{len(flagged)} of {plural(len(clusters), 'cluster')} lack high availability or "
        "automatic upgrades",
        flagged,
    )


def findings(api: DigitalOcean) -> Check:
    """The latest security scan's critical and high findings."""
    counts = {}
    for severity in ("CRITICAL", "HIGH"):
        body = api.get(
            "/v2/security/scans/latest",
            params={"severity": severity, "per_page": PAGE_SIZE, "page": 1},
        )
        scan = body.get("scan") if isinstance(body.get("scan"), dict) else {}
        if scan.get("status") in ("CSPM_NOT_ENABLED", "SCAN_NOT_RUN"):
            raise Unavailable("not_configured")
        meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
        total = meta.get("total")
        listed = [f for f in scan.get("findings") or [] if isinstance(f, dict)]
        counts[severity] = (
            total if isinstance(total, int) and total >= len(listed) else len(listed)
        )
    label = CHECKS["findings"]
    if not counts["CRITICAL"] + counts["HIGH"]:
        return Check(
            "findings", label, "met", "No critical or high findings in the latest scan"
        )
    return Check(
        "findings",
        label,
        "attention",
        f"{counts['CRITICAL']} critical and {counts['HIGH']} high findings in the latest scan",
    )


def collect() -> list[ScopeResult]:
    with connect() as api:
        key, name, kind = team(api)
        checks = guarded(
            CHECKS,
            ["network.admin_ports", "network.firewall_coverage"],
            lambda: network(api),
        )
        checks += guarded(CHECKS, ["network.databases"], lambda: [databases(api)])
        checks += guarded(CHECKS, ["resilience.backups"], lambda: [backups(api)])
        checks += guarded(
            CHECKS, ["kubernetes.control_plane"], lambda: [kubernetes(api)]
        )
        checks += guarded(CHECKS, ["findings"], lambda: [findings(api)])
        return [ScopeResult(key, name, checks, kind=kind)]


# --- activity ---------------------------------------------------------------------


def action_entry(action: dict, scope: str) -> Activity | None:
    when = parse_time(action.get("started_at"))
    ident = action.get("id")
    kind = text_field(action.get("type"))
    if when is None or not isinstance(ident, int) or not kind:
        return None
    outcome = {"completed": "success", "errored": "failure"}.get(
        action.get("status"), "info"
    )
    resource = text_field(action.get("resource_type"), 64)
    return Activity(
        id=f"action:{ident}",
        at=when,
        scope=scope,
        action=kind,
        outcome=outcome,
        summary=f"{kind} on {resource or 'a resource'} ({outcome})",
        detail={
            k: v
            for k, v in {
                "service": "digitalocean",
                "resource_type": resource,
                "region": text_field(action.get("region_slug"), 64),
            }.items()
            if v
        },
        raw=action,
    )


def activity(since: datetime, until: datetime) -> tuple[list[Activity], bool]:
    """Resource actions started in [since, until). The listing runs newest
    first, so reading stops at the first page older than the window."""
    with connect() as api:
        key = team(api)[0]
        found, complete = [], False
        for page in range(1, MAX_ACTIVITY // PAGE_SIZE + 2):
            body = api.get("/v2/actions", params={"page": page, "per_page": PAGE_SIZE})
            entries = [action_entry(a, key) for a in items(body, "actions")]
            found.extend(e for e in entries if e is not None and since <= e.at < until)
            oldest = min((e.at for e in entries if e is not None), default=None)
            links = body.get("links") if isinstance(body.get("links"), dict) else {}
            pages = links.get("pages") if isinstance(links.get("pages"), dict) else {}
            if (
                not entries
                or not pages.get("next")
                or (oldest is not None and oldest < since)
            ):
                complete = True
                break
    out = sorted(found, key=lambda e: e.at)
    if len(out) > MAX_ACTIVITY:
        return out[-MAX_ACTIVITY:], False
    return out, complete


PROVIDER = Provider(
    key="digitalocean",
    label="DigitalOcean",
    vendor="DigitalOcean",
    website="https://www.digitalocean.com",
    aliases=("DigitalOcean, LLC", "Digital Ocean"),
    scope_noun="team",
    checks=CHECKS,
    collect=collect,
    activity=activity,
    activity_lag=timedelta(minutes=15),
)
