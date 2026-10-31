"""Scaleway posture and Audit Trail activity for one Organization.

KEEN reads the Organization through the built-in HTTP client with an API key
whose secret key is sent as ``X-Auth-Token``, and names the Organization in
every listing, so results for one Organization never come from another.
Instance security groups are read per zone, Managed Databases and Kubernetes
clusters per region, and Audit Trail in the regions that offer it.
"""

from __future__ import annotations

import ipaddress
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
    api_timestamp,
    clip,
    guarded,
    inactive_check,
    mfa_check,
    parse_time,
    plural,
    split_list,
    text_field,
)

ORIGIN = "https://api.scaleway.com"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ZONE = re.compile(r"([a-z]{2}-[a-z]{3})-[1-9]")
EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,}")
DEFAULT_ZONES = (
    "fr-par-1,fr-par-2,fr-par-3,nl-ams-1,nl-ams-2,nl-ams-3,pl-waw-1,pl-waw-2,pl-waw-3"
)
# Audit Trail is offered in these regions.
AUDIT_REGIONS = ("fr-par", "nl-ams")
PAGE_SIZE = 100
MAX_PAGES = 50
MAX_ZONES = 20
# Each of these is one more request: a security group's rules, a policy's
# rules, a database's or a cluster's access list.
MAX_GROUPS = 200
MAX_POLICIES = 200
MAX_DATABASES = 200
MAX_CLUSTERS = 200
ADMIN_PORTS = (22, 3389)
EVERYWHERE = (ipaddress.ip_network("0.0.0.0/0"), ipaddress.ip_network("::/0"))
# Permission sets that control IAM, and through it everything else.
ADMIN_SETS = {"OrganizationManager", "IAMManager"}
IDENTITY_CHECKS = (
    "identity.mfa",
    "identity.admin_mfa",
    "identity.admins",
    "identity.inactive",
)
CHECKS = {
    "identity.mfa": "MFA for members",
    "identity.admin_mfa": "MFA for the owner and IAM managers",
    "identity.admins": "Owner and IAM managers",
    "identity.inactive": "Unused members",
    "credentials.key_age": "API key age",
    "network.admin_ports": "SSH and RDP open to the internet",
    "network.databases": "Databases open to the internet",
    "resilience.backups": "Database backups",
    "kubernetes.control_plane": "Kubernetes clusters",
}
# Permission sets for the API key's policy, all read-only.
PERMISSION_SETS = (
    "IAMReadOnly",
    "InstancesReadOnly",
    "RelationalDatabasesReadOnly",
    "KubernetesReadOnly",
    "AuditTrailReadOnly",
)


class Scaleway(JsonApi):
    def __init__(self, secret: str, organisation: str, zones: list[str], **kwargs):
        super().__init__(ORIGIN, {"X-Auth-Token": secret}, **kwargs)
        self.organisation = organisation
        self.zones = zones
        self.regions = sorted({ZONE.fullmatch(z).group(1) for z in zones})

    def pages(self, path: str, key: str, *, params=None, size="page_size") -> list:
        """Every item of a numbered listing; a longer one is refused, since a
        partial population would misstate a check."""
        found = []
        for page in range(1, MAX_PAGES + 1):
            body = self.get(
                path, params={**(params or {}), "page": page, size: PAGE_SIZE}
            )
            batch = items(body, key)
            found.extend(batch)
            # Instance listings give their total in a header instead.
            total = body.get("total_count")
            if not batch or (
                len(found) >= total
                if isinstance(total, int)
                else len(batch) < PAGE_SIZE
            ):
                return found
        raise Unavailable("unavailable")

    def iam(self, kind: str, key: str | None = None, **params) -> list:
        return self.pages(
            "/iam/v1alpha1/" + kind,
            key or kind,
            params={"organization_id": self.organisation, **params},
        )


def connect() -> Scaleway:
    secret = str(settings.scaleway_secret_key or "").strip().lower()
    organisation = str(settings.scaleway_organization_id or "").strip().lower()
    zones = split_list(settings.scaleway_zones or DEFAULT_ZONES)
    if (
        not UUID.fullmatch(secret)
        or not UUID.fullmatch(organisation)
        or not zones
        or len(zones) > MAX_ZONES
        or len(set(zones)) != len(zones)
        or any(not ZONE.fullmatch(z) for z in zones)
    ):
        raise Unavailable("not_configured")
    return Scaleway(secret, organisation, zones)


def _uuid(value) -> str:
    ident = str(value or "")
    if not UUID.fullmatch(ident):
        raise Unavailable("unavailable")
    return ident


def everywhere(value) -> bool:
    try:
        network = ipaddress.ip_network(str(value), strict=False)
    except ValueError:
        return False
    return network in EVERYWHERE


# --- IAM ---------------------------------------------------------------------------


def names(users: list[dict], applications: list[dict]) -> dict:
    """Display names for user and application IDs."""
    found = {
        a.get("id"): "application/" + clip(a.get("name") or a.get("id"), 100)
        for a in applications
    }
    found.update(
        {
            u.get("id"): clip(u.get("email") or u.get("username") or u.get("id"))
            for u in users
        }
    )
    return found


def admins(api: Scaleway, users: list[dict], named: dict) -> tuple[set, list[str]]:
    """User IDs holding the owner role, OrganizationManager or IAMManager,
    directly or through a group, and the groups and applications holding
    either permission set."""
    policies = api.iam("policies")
    if len(policies) > MAX_POLICIES:
        raise Unavailable("unavailable")
    groups = {g.get("id"): g for g in api.iam("groups")}
    held, others = {u.get("id") for u in users if u.get("type") == "owner"}, set()
    for policy in policies:
        rules = api.pages(
            "/iam/v1alpha1/rules",
            "rules",
            params={"policy_id": _uuid(policy.get("id"))},
        )
        if not any(
            set(r.get("permission_set_names") or []) & ADMIN_SETS for r in rules
        ):
            continue
        if policy.get("user_id"):
            held.add(policy["user_id"])
        elif policy.get("group_id") in groups:
            group = groups[policy["group_id"]]
            members = (
                {u.get("id") for u in users}
                if group.get("all_users") is True
                else set(group.get("user_ids") or [])
            )
            held |= members
            others.add("group/" + clip(group.get("name") or group.get("id"), 100))
        elif policy.get("application_id"):
            others.add(named.get(policy["application_id"]) or "application/unknown")
    return held, sorted(others)


def sso_only(organisation: dict) -> bool:
    """Members can sign in only through SAML, so the identity provider
    applies MFA."""
    return (
        organisation.get("login_saml_enabled") is True
        and organisation.get("login_password_enabled") is False
        and organisation.get("login_magic_code_enabled") is False
    )


def identity(
    api: Scaleway, users: list[dict], named: dict, organisation: dict, now: datetime
) -> tuple[list[Check], list[Identity]]:
    if len(users) > MAX_IDENTITIES:
        raise Unavailable("unavailable")
    held, others = admins(api, users, named)
    people, active = [], []
    for user in users:
        email = clip(user.get("email"))
        owner = user.get("type") == "owner"
        mfa = user.get("mfa")
        pending = user.get("status") == "invitation_pending"
        locked = user.get("locked") is True
        person = Identity(
            name=email or clip(user.get("username") or user.get("id")),
            email=email if EMAIL.fullmatch(email) else "",
            admin=user.get("id") in held,
            mfa=mfa if isinstance(mfa, bool) else None,
            last_used=parse_time(user.get("last_login_at")),
            created=parse_time(user.get("created_at")),
            detail=", ".join(
                d
                for d in (
                    "Owner" if owner else "",
                    "IAM manager" if user.get("id") in held and not owner else "",
                    "Invitation pending" if pending else "",
                    "Locked" if locked else "",
                )
                if d
            ),
        )
        people.append(person)
        if not pending and not locked:
            active.append(person)
    privileged = [p for p in active if p.admin]
    if sso_only(organisation):
        mfa = [
            Check(
                key,
                CHECKS[key],
                "info",
                "Members sign in through SAML single sign-on, where the identity "
                "provider applies MFA",
            )
            for key in ("identity.mfa", "identity.admin_mfa")
        ]
    else:
        mfa = [
            mfa_check("identity.mfa", CHECKS["identity.mfa"], active, who="members"),
            mfa_check(
                "identity.admin_mfa",
                CHECKS["identity.admin_mfa"],
                privileged,
                who="administrators",
            ),
        ]
    return [
        *mfa,
        Check(
            "identity.admins",
            CHECKS["identity.admins"],
            "info",
            f"{plural(len([p for p in people if p.admin]), 'user')} and "
            f"{plural(len(others), 'group or application', 'groups or applications')} "
            "hold the owner role, OrganizationManager or IAMManager",
            [p.name for p in people if p.admin] + others,
        ),
        inactive_check(
            "identity.inactive",
            CHECKS["identity.inactive"],
            active,
            settings.cloud_inactive_days,
            now,
        ),
    ], sorted(people, key=lambda p: p.name.lower())


def keys(api: Scaleway, named: dict, now: datetime) -> Check:
    """Live API keys older than the key age setting. Keys Scaleway manages
    for its own products are left out."""
    found = [
        k
        for k in api.iam("api-keys", "api_keys", expired="false")
        if k.get("managed") is not True
    ]
    limit = timedelta(days=settings.cloud_key_age_days)
    old = set()
    for key in found:
        created = parse_time(key.get("created_at"))
        if created is None or now - created < limit:
            continue
        bearer = key.get("user_id") or key.get("application_id")
        old.add(named.get(bearer) or clip(key.get("access_key") or "unknown", 64))
    label = CHECKS["credentials.key_age"]
    if not found:
        return Check("credentials.key_age", label, "info", "No live API keys")
    return Check(
        "credentials.key_age",
        label,
        "attention" if old else "met",
        f"{plural(len(old), 'user or application', 'users or applications')} hold API "
        f"keys older than {settings.cloud_key_age_days} days, of "
        f"{plural(len(found), 'live key')}",
        sorted(old),
    )


# --- network, databases and Kubernetes -----------------------------------------------


def _covers(rule: dict, port: int) -> bool:
    if str(rule.get("protocol") or "").upper() not in ("TCP", "ANY"):
        return False
    low, high = rule.get("dest_port_from"), rule.get("dest_port_to")
    if not isinstance(low, int):
        return True  # No port means every port.
    return low <= port <= (high if isinstance(high, int) else low)


def exposed_ports(group: dict, rules: list[dict]) -> list[int]:
    """Remote-access ports the internet reaches through one security group:
    inbound rules apply by position, the first match decides, and traffic no
    rule matches meets the group's inbound default policy."""
    inbound = sorted(
        (r for r in rules if r.get("direction") == "inbound"),
        key=lambda r: r.get("position") if isinstance(r.get("position"), int) else 0,
    )
    out = []
    for port in ADMIN_PORTS:
        decision = group.get("inbound_default_policy")
        for rule in inbound:
            if _covers(rule, port) and everywhere(rule.get("ip_range")):
                decision = rule.get("action")
                break
        if decision == "accept":
            out.append(port)
    return out


def network(api: Scaleway) -> Check:
    in_use, exposing = 0, []
    for zone in api.zones:
        groups = api.pages(
            f"/instance/v1/zones/{zone}/security_groups",
            "security_groups",
            params={"organization": api.organisation},
            size="per_page",
        )
        for group in groups:
            if not group.get("servers"):
                continue
            in_use += 1
            if in_use > MAX_GROUPS:
                raise Unavailable("unavailable")
            ident = _uuid(group.get("id"))
            rules = api.pages(
                f"/instance/v1/zones/{zone}/security_groups/{ident}/rules",
                "rules",
                size="per_page",
            )
            if exposed_ports(group, rules):
                exposing.append(f"{zone}/{clip(group.get('name') or ident, 100)}")
    return Check(
        "network.admin_ports",
        CHECKS["network.admin_ports"],
        "attention" if exposing else "met",
        f"{len(exposing)} of {plural(in_use, 'security group')} in use let SSH or RDP "
        "in from anywhere",
        exposing,
    )


def databases(api: Scaleway) -> list[Check]:
    """Database Instances with a public endpoint whose access list admits
    every address, and those whose automatic backups are off."""
    found = []
    for region in api.regions:
        for instance in api.pages(
            f"/rdb/v1/regions/{region}/instances",
            "instances",
            params={"organization_id": api.organisation},
        ):
            found.append((region, instance))
    if len(found) > MAX_DATABASES:
        raise Unavailable("unavailable")
    exposed, without = [], []
    for region, instance in found:
        ident = _uuid(instance.get("id"))
        name = f"{region}/{clip(instance.get('name') or ident, 100)}"
        schedule = instance.get("backup_schedule")
        if not isinstance(schedule, dict) or schedule.get("disabled") is not False:
            without.append(name)
        public = any(
            isinstance(e, dict) and not e.get("private_network")
            for e in instance.get("endpoints") or []
        )
        if not public:
            continue
        rules = api.pages(f"/rdb/v1/regions/{region}/instances/{ident}/acls", "rules")
        if any(
            everywhere(r.get("ip"))
            and r.get("action", "allow") == "allow"
            and r.get("direction", "inbound") == "inbound"
            for r in rules
        ):
            exposed.append(name)
    total = plural(len(found), "Database Instance")
    return [
        Check(
            "network.databases",
            CHECKS["network.databases"],
            "attention" if exposed else "met",
            f"{len(exposed)} of {total} accept connections from anywhere",
            exposed,
        ),
        Check(
            "resilience.backups",
            CHECKS["resilience.backups"],
            "attention" if without else "met",
            f"{len(without)} of {total} without automatic backups",
            without,
        ),
    ]


def kubernetes(api: Scaleway) -> Check:
    clusters = []
    for region in api.regions:
        for cluster in api.pages(
            f"/k8s/v1/regions/{region}/clusters",
            "clusters",
            params={"organization_id": api.organisation},
        ):
            clusters.append((region, cluster))
    if len(clusters) > MAX_CLUSTERS:
        raise Unavailable("unavailable")
    flagged = []
    for region, cluster in clusters:
        ident = _uuid(cluster.get("id"))
        problems = []
        upgrade = cluster.get("auto_upgrade")
        if not (isinstance(upgrade, dict) and upgrade.get("enabled") is True):
            problems.append("no automatic upgrades")
        # Clusters from before access lists accept every address.
        open_api = cluster.get("acl_available") is not True or any(
            everywhere(r.get("ip"))
            for r in api.pages(
                f"/k8s/v1/regions/{region}/clusters/{ident}/acls", "rules"
            )
        )
        if open_api:
            problems.append("API open to every address")
        if problems:
            flagged.append(
                f"{region}/{clip(cluster.get('name') or ident, 100)}: {', '.join(problems)}"
            )
    return Check(
        "kubernetes.control_plane",
        CHECKS["kubernetes.control_plane"],
        "attention" if flagged else "met",
        f"{len(flagged)} of {plural(len(clusters), 'cluster')} lack automatic upgrades "
        "or an access list",
        flagged,
    )


def collect() -> list[ScopeResult]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as api:
        try:
            organisation = api.get(f"/iam/v1alpha1/organizations/{api.organisation}")
        except Unavailable:
            organisation = {}
        label = clip(
            organisation.get("alias") or organisation.get("name") or api.organisation,
            128,
        )
        people: list[Identity] = []
        named: dict = {}

        def iam():
            users = api.iam("users")
            named.update(names(users, api.iam("applications")))
            checks, found = identity(api, users, named, organisation, now)
            people.extend(found)
            return checks

        checks = guarded(CHECKS, IDENTITY_CHECKS, iam)
        checks += guarded(
            CHECKS, ["credentials.key_age"], lambda: [keys(api, named, now)]
        )
        checks += guarded(CHECKS, ["network.admin_ports"], lambda: [network(api)])
        checks += guarded(
            CHECKS, ["network.databases", "resilience.backups"], lambda: databases(api)
        )
        checks += guarded(
            CHECKS, ["kubernetes.control_plane"], lambda: [kubernetes(api)]
        )
        return [
            ScopeResult(api.organisation, label, checks, people, kind="organisation")
        ]


# --- activity ----------------------------------------------------------------------


def _resources(event: dict) -> list[dict]:
    return [r for r in event.get("resources") or [] if isinstance(r, dict)]


def _raw(event: dict) -> dict:
    """The vendor's record without request bodies or telephone numbers."""
    raw = {k: v for k, v in event.items() if k != "request_body"}
    raw["resources"] = [
        {
            k: (
                {kk: vv for kk, vv in v.items() if kk != "phone_number"}
                if isinstance(v, dict)
                else v
            )
            for k, v in r.items()
        }
        for r in _resources(event)
    ]
    return raw


def event_entry(event: dict, organisation: str, named: dict) -> Activity | None:
    """One Audit Trail API event."""
    when = parse_time(event.get("recorded_at"))
    ident = text_field(event.get("id"), 64)
    method = text_field(event.get("method_name"))
    if when is None or not ident or not method:
        return None
    status = event.get("status_code")
    status = status if isinstance(status, int) else None
    outcome = "info" if status is None else "failure" if status >= 400 else "success"
    principal = event.get("principal")
    principal = principal if isinstance(principal, dict) else {}
    who = named.get(principal.get("id")) or text_field(principal.get("id"), 64)
    product = text_field(event.get("product_name"), 64)
    resources = [text_field(r.get("name") or r.get("id")) for r in _resources(event)]
    return Activity(
        id=ident,
        at=when,
        scope=organisation,
        action=method,
        actor=who,
        outcome=outcome,
        summary=f"{method} on {product or 'Scaleway'} by {who or 'unknown'} ({outcome})",
        detail={
            k: v
            for k, v in {
                "product": product,
                "service": text_field(event.get("service_name")),
                "source_ip": text_field(event.get("source_ip"), 64),
                "status_code": status,
                "resources": [r for r in resources if r][:10],
                "region": text_field(event.get("locality"), 32),
            }.items()
            if v not in ("", None, [])
        },
        raw=_raw(event),
    )


def sign_in_entry(event: dict, organisation: str) -> Activity | None:
    """One Audit Trail authentication event."""
    when = parse_time(event.get("recorded_at"))
    ident = text_field(event.get("id"), 64)
    if when is None or not ident:
        return None
    outcome = {"success": "success", "failure": "failure"}.get(
        event.get("result"), "info"
    )
    who = next(
        (
            text_field(r["account_user_info"].get("email"))
            for r in _resources(event)
            if isinstance(r.get("account_user_info"), dict)
        ),
        "",
    )
    return Activity(
        id=ident,
        at=when,
        scope=organisation,
        action="SignIn",
        actor=who,
        outcome=outcome,
        summary=f"Sign-in by {who or 'unknown'} ({outcome})",
        detail={
            k: v
            for k, v in {
                "method": text_field(event.get("method"), 32),
                "source_ip": text_field(event.get("source_ip"), 64),
                "country": text_field(event.get("country_code"), 8),
                "failure_reason": text_field(event.get("failure_reason"), 64),
                "mfa": text_field(event.get("mfa_type"), 32),
            }.items()
            if v
        },
        raw=_raw(event),
    )


def _events(api: Scaleway, path: str, since: datetime, until: datetime):
    """Newest-first pages of one Audit Trail listing, and whether every entry
    in the window was read."""
    found, token = [], None
    for _ in range(MAX_ACTIVITY // PAGE_SIZE + 1):
        body = api.get(
            path,
            params={
                "organization_id": api.organisation,
                "recorded_after": api_timestamp(since),
                "recorded_before": api_timestamp(until),
                "order_by": "recorded_at_desc",
                "page_size": PAGE_SIZE,
                **({"page_token": token} if token else {}),
            },
            # A region without the listing has nothing to read.
            missing={"events": []},
        )
        found.extend(items(body, "events"))
        token = body.get("next_page_token")
        if not token:
            return found, True
    return found, False


def activity(since: datetime, until: datetime) -> tuple[list[Activity], bool]:
    """Audit Trail API and authentication events in [since, until)."""
    with connect() as api:
        try:
            named = names(api.iam("users"), api.iam("applications"))
        except Unavailable:
            named = {}
        found, complete = [], True
        for region in AUDIT_REGIONS:
            base = f"/audit-trail/v1alpha1/regions/{region}/"
            events, done = _events(api, base + "events", since, until)
            found += [event_entry(e, api.organisation, named) for e in events]
            complete = complete and done
            events, done = _events(api, base + "authentication-events", since, until)
            found += [sign_in_entry(e, api.organisation) for e in events]
            complete = complete and done
    unique = {e.id: e for e in found if e is not None and since <= e.at < until}
    out = sorted(unique.values(), key=lambda e: e.at)
    if len(out) > MAX_ACTIVITY:
        return out[-MAX_ACTIVITY:], False
    return out, complete


PROVIDER = Provider(
    key="scaleway",
    label="Scaleway",
    vendor="Scaleway",
    website="https://www.scaleway.com",
    aliases=("Scaleway SAS",),
    scope_noun="organisation",
    checks=CHECKS,
    collect=collect,
    activity=activity,
    activity_lag=timedelta(minutes=15),
)
