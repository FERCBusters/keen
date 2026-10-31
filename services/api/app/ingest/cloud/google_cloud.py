"""Google Cloud posture and Admin Activity audit logs for one organisation, or
one project where there is no organisation.

KEEN reads it as a service account through the shared Google transport, and
every request names the configured organisation or project as its parent, so
results for one organisation never come from another. People sign in with
Google accounts, whose 2-Step Verification the Google Workspace provider
checks.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.ingest.cloud import google
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
    parse_time,
    plural,
    split_list,
    text_field,
)

ORGANISATION = re.compile(r"[1-9][0-9]{0,19}")
PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,}")
MAX_PROJECTS = 50
PAGE = 500
MAX_PAGES = 40
CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"
READ_ONLY = "https://www.googleapis.com/auth/cloud-platform.read-only"
ORG_POLICY = "https://www.googleapis.com/auth/organizationpolicy.readonly"
# Roles that control everything beneath them.
PRIVILEGED_ROLES = {
    "roles/owner": "Owner",
    "roles/resourcemanager.organizationAdmin": "Organization Administrator",
}
KEY_ASSET = "iam.googleapis.com/ServiceAccountKey"
FIREWALL_ASSET = "compute.googleapis.com/Firewall"
SQL_ASSET = "sqladmin.googleapis.com/Instance"
ADMIN_PORTS = (22, 3389)
EVERYWHERE = (ipaddress.ip_network("0.0.0.0/0"), ipaddress.ip_network("::/0"))
# A constraint is met when either its legacy or its managed form is enforced.
PUBLIC_ACCESS = ("storage.publicAccessPrevention",)
KEY_CREATION = (
    "iam.disableServiceAccountKeyCreation",
    "iam.managed.disableServiceAccountKeyCreation",
)
DOMAINS = ("iam.allowedPolicyMemberDomains", "iam.managed.allowedPolicyMembers")
# Admin Activity entries KEEN records: IAM policy, service account, service
# account key and organisation policy changes.
METHODS = (
    "SetIamPolicy",
    "CreateServiceAccountKey",
    "DeleteServiceAccountKey",
    "UploadServiceAccountKey",
    "CreateServiceAccount",
    "DeleteServiceAccount",
    "CreatePolicy",
    "UpdatePolicy",
    "DeletePolicy",
)
LOG_PAGE = 1000
CHECKS = {
    "identity.admins": "Owners and organisation administrators",
    "credentials.key_age": "Service account keys",
    "google_cloud.key_creation": "Service account key creation blocked",
    "google_cloud.domain_sharing": "Domain-restricted sharing",
    "storage.public_access": "Public access prevention",
    "logging.audit_trail": "Data Access audit logs",
    "network.admin_ports": "SSH and RDP open to the internet",
    "network.databases": "Databases open to the internet",
    "findings": "Security Command Center findings",
}
# IAM roles to grant the service account on the organisation or project.
ROLES = (
    "roles/cloudasset.viewer",
    "roles/iam.securityReviewer",
    "roles/orgpolicy.policyViewer",
    "roles/securitycenter.findingsViewer",
    "roles/logging.viewer",
    "roles/serviceusage.serviceUsageConsumer",
)


class Api:
    """The service account, acting for one organisation or project."""

    def __init__(self, info: dict, parent: str):
        self.info = info
        self.parent = parent
        self.organisation = parent.startswith("organizations/")

    def client(self, api: str, version: str, scopes):
        return google.client(api, version, self.info, scopes)


def connect() -> Api:
    organisation = str(settings.google_cloud_organization or "").strip()
    project = str(settings.google_cloud_project or "").strip()
    if organisation:
        if not ORGANISATION.fullmatch(organisation):
            raise Unavailable("not_configured")
        parent = "organizations/" + organisation
    elif PROJECT.fullmatch(project):
        parent = "projects/" + project
    else:
        raise Unavailable("not_configured")
    info = google.key_info(
        str(settings.google_cloud_sa_keyfile or ""),
        str(settings.google_cloud_sa_json_b64 or ""),
        str(settings.google_cloud_sa_json or ""),
    )
    return Api(info, parent)


def _assets(api: Api, asset_type: str) -> list[dict]:
    """Each asset's resource data for one asset type under the parent."""
    assets = api.client("cloudasset", "v1", [CLOUD_PLATFORM]).assets()
    found = google.pages(
        lambda token: assets.list(
            parent=api.parent,
            assetTypes=[asset_type],
            contentType="RESOURCE",
            pageSize=1000,
            **({"pageToken": token} if token else {}),
        ),
        "assets",
        MAX_PAGES,
    )
    out = []
    for asset in found:
        resource = (
            asset.get("resource") if isinstance(asset.get("resource"), dict) else {}
        )
        data = resource.get("data") if isinstance(resource.get("data"), dict) else {}
        out.append({"_name": str(asset.get("name") or ""), **data})
    return out


# --- IAM -------------------------------------------------------------------------


def _member(member: str) -> Identity | None:
    kind, _, value = member.partition(":")
    if not value or kind.startswith("deleted"):
        return None
    if kind == "user":
        return Identity(
            clip(value), value if EMAIL.fullmatch(value) else "", admin=True
        )
    if kind == "serviceAccount":
        return Identity(clip(value), kind="service", admin=True)
    if kind in ("group", "domain"):
        return Identity(clip(value), kind="group", admin=True)
    return None


def administrators(api: Api) -> tuple[Check, list[Identity]]:
    """Owners and organisation administrators anywhere under the parent;
    conditional bindings are left out."""
    policies = api.client("cloudasset", "v1", [CLOUD_PLATFORM]).v1()
    holders: dict[str, Identity] = {}
    for role, name in PRIVILEGED_ROLES.items():
        if role == "roles/resourcemanager.organizationAdmin" and not api.organisation:
            continue
        results = google.pages(
            lambda token, role=role: policies.searchAllIamPolicies(
                scope=api.parent,
                query=f"policy:{role}",
                pageSize=PAGE,
                **({"pageToken": token} if token else {}),
            ),
            "results",
            MAX_PAGES,
        )
        for result in results:
            policy = (
                result.get("policy") if isinstance(result.get("policy"), dict) else {}
            )
            for binding in policy.get("bindings") or []:
                if not isinstance(binding, dict) or binding.get("role") != role:
                    continue
                if binding.get("condition"):
                    continue
                for member in binding.get("members") or []:
                    person = holders.get(str(member)) or _member(str(member))
                    if person is None:
                        continue
                    holders[str(member)] = person
                    roles = person.detail.split(", ") if person.detail else []
                    if name not in roles:
                        person.detail = ", ".join([*roles, name])
        if len(holders) > MAX_IDENTITIES:
            raise Unavailable("unavailable")
    found = sorted(holders.values(), key=lambda p: p.name.lower())
    check = Check(
        "identity.admins",
        CHECKS["identity.admins"],
        "info",
        f"{plural(len(found), 'user, group or service account', 'users, groups or service accounts')} "
        "hold Owner or Organization Administrator",
        [p.name for p in found],
    )
    return check, found


def keys(api: Api, now: datetime) -> Check:
    """Age of each user-managed service account key that is live."""
    label = CHECKS["credentials.key_age"]
    total, old = 0, set()
    limit = timedelta(days=settings.cloud_key_age_days)
    for key in _assets(api, KEY_ASSET):
        if key.get("keyType") != "USER_MANAGED" or key.get("disabled") is True:
            continue
        ends = parse_time(key.get("validBeforeTime"))
        if ends is not None and ends <= now and ends.year < 9999:
            continue
        match = re.search(
            r"/serviceAccounts/([^/]+)/keys/", str(key.get("name") or key["_name"])
        )
        if not match:
            continue
        total += 1
        starts = parse_time(key.get("validAfterTime"))
        if starts is not None and now - starts >= limit:
            old.add(clip(match.group(1)))
    if not total:
        return Check(
            "credentials.key_age", label, "info", "No user-managed service account keys"
        )
    return Check(
        "credentials.key_age",
        label,
        "attention" if old else "met",
        f"{plural(len(old), 'service account')} with keys older than "
        f"{settings.cloud_key_age_days} days, of {plural(total, 'live key')}",
        sorted(old),
    )


# --- organisation policies ------------------------------------------------------------


def _effective(api: Api, constraint: str) -> dict:
    """The constraint's effective policy; a managed constraint the organisation
    does not offer has none."""
    policies = api.client("orgpolicy", "v2", [ORG_POLICY])
    collection = (
        policies.organizations() if api.organisation else policies.projects()
    ).policies()
    request = collection.getEffectivePolicy(name=f"{api.parent}/policies/{constraint}")
    try:
        found = google.execute(request, missing={})
    except Unavailable as exc:
        if ".managed." not in constraint or exc.reason != "not_configured":
            raise
        found = {}
    return found.get("spec") if isinstance(found.get("spec"), dict) else {}


def _enforced(spec: dict) -> bool:
    return any(
        isinstance(rule, dict)
        and rule.get("enforce") is True
        and not rule.get("condition")
        for rule in spec.get("rules") or []
    )


def _restricted(spec: dict) -> tuple[bool, int]:
    """Whether a list constraint limits its values, and to how many."""
    allowed, limited = 0, False
    for rule in spec.get("rules") or []:
        if not isinstance(rule, dict) or rule.get("condition"):
            continue
        if rule.get("allowAll") is True:
            return False, 0
        values = rule.get("values") if isinstance(rule.get("values"), dict) else {}
        listed = [v for v in values.get("allowedValues") or [] if isinstance(v, str)]
        if listed or rule.get("denyAll") is True:
            limited = True
            allowed += len(listed)
        if rule.get("enforce") is True:
            # A managed constraint enforced with its own parameters.
            limited = True
    return limited, allowed


def org_policies(api: Api) -> list[Check]:
    public = any(_enforced(_effective(api, c)) for c in PUBLIC_ACCESS)
    creation = any(_enforced(_effective(api, c)) for c in KEY_CREATION)
    domains = [_restricted(_effective(api, c)) for c in DOMAINS]
    restricted = any(r for r, _ in domains)
    allowed = sum(n for r, n in domains if r)

    def check(key, ok, yes, no):
        return Check(key, CHECKS[key], "met" if ok else "attention", yes if ok else no)

    return [
        check(
            "storage.public_access",
            public,
            "Public access prevention is enforced",
            "Public access prevention is not enforced",
        ),
        check(
            "google_cloud.key_creation",
            creation,
            "Service account key creation is blocked",
            "Service account keys can be created",
        ),
        check(
            "google_cloud.domain_sharing",
            restricted,
            f"IAM members are restricted to {plural(allowed, 'allowed customer or domain', 'allowed customers or domains')}",
            "IAM policies can name members from any domain",
        ),
    ]


def audit_logging(api: Api) -> Check:
    """Whether Data Access audit logs are on for every service."""
    manager = api.client("cloudresourcemanager", "v3", [READ_ONLY])
    collection = manager.organizations() if api.organisation else manager.projects()
    policy = google.execute(
        collection.getIamPolicy(
            resource=api.parent, body={"options": {"requestedPolicyVersion": 3}}
        )
    )
    kinds = set()
    for config in policy.get("auditConfigs") or []:
        if isinstance(config, dict) and config.get("service") == "allServices":
            kinds |= {
                str(c.get("logType"))
                for c in config.get("auditLogConfigs") or []
                if isinstance(c, dict)
            }
    label = CHECKS["logging.audit_trail"]
    if {"DATA_READ", "DATA_WRITE"} <= kinds:
        return Check(
            "logging.audit_trail",
            label,
            "met",
            "Data Access audit logs are on for all services",
        )
    return Check(
        "logging.audit_trail",
        label,
        "attention",
        "Data Access audit logs are not on for all services",
    )


# --- network --------------------------------------------------------------------


def _ports(values) -> list[tuple[int, int]] | None:
    """A rule's port ranges; None means every port."""
    if not values:
        return None
    out = []
    for value in values:
        low, _, high = str(value).partition("-")
        try:
            out.append((int(low), int(high or low)))
        except ValueError:
            continue
    return out


def _covers(entries, port: int) -> bool:
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("IPProtocol") or "").lower() not in ("tcp", "all"):
            continue
        ranges = _ports(entry.get("ports"))
        if ranges is None or any(low <= port <= high for low, high in ranges):
            return True
    return False


def _from_internet(rule: dict) -> bool:
    for value in rule.get("sourceRanges") or []:
        try:
            network = ipaddress.ip_network(str(value), strict=False)
        except ValueError:
            continue
        if network in EVERYWHERE:
            return True
    return False


def exposed_ports(rules: list[dict]) -> list[int]:
    """Remote-access ports the internet reaches in one VPC network: ingress
    rules apply in priority order, a deny beats an allow of equal priority,
    and the implied rule denies the rest. A deny limited to tagged instances
    leaves the others open."""
    ordered = sorted(
        (r for r in rules if r.get("direction", "INGRESS") == "INGRESS"),
        key=lambda r: (
            r.get("priority") if isinstance(r.get("priority"), int) else 1000,
            0 if r.get("denied") else 1,
        ),
    )
    out = []
    for port in ADMIN_PORTS:
        for rule in ordered:
            if rule.get("disabled") is True or not _from_internet(rule):
                continue
            if rule.get("denied") and _covers(rule.get("denied"), port):
                if not (rule.get("targetTags") or rule.get("targetServiceAccounts")):
                    break
                continue
            if _covers(rule.get("allowed"), port):
                out.append(port)
                break
    return out


def network(api: Api) -> list[Check]:
    by_network: dict[str, list[dict]] = {}
    for rule in _assets(api, FIREWALL_ASSET):
        by_network.setdefault(str(rule.get("network") or ""), []).append(rule)
    exposing = sorted(
        clip(name.rsplit("/", 1)[-1], 128)
        for name, found in by_network.items()
        if exposed_ports(found)
    )
    instances = _assets(api, SQL_ASSET)
    exposed = []
    for instance in instances:
        configured = (
            instance.get("settings")
            if isinstance(instance.get("settings"), dict)
            else {}
        )
        ip = configured.get("ipConfiguration")
        ip = ip if isinstance(ip, dict) else {}
        if ip.get("ipv4Enabled") is False:
            continue
        networks = [
            n.get("value")
            for n in ip.get("authorizedNetworks") or []
            if isinstance(n, dict)
        ]
        if _from_internet({"sourceRanges": networks}):
            exposed.append(clip(instance.get("name") or instance["_name"], 128))
    return [
        Check(
            "network.admin_ports",
            CHECKS["network.admin_ports"],
            "attention" if exposing else "met",
            f"{len(exposing)} of {plural(len(by_network), 'VPC network')} let SSH or RDP in from anywhere",
            exposing,
        ),
        Check(
            "network.databases",
            CHECKS["network.databases"],
            "attention" if exposed else "met",
            f"{len(exposed)} of {plural(len(instances), 'Cloud SQL instance')} authorise every address",
            sorted(exposed),
        ),
    ]


def findings(api: Api) -> Check:
    """Active, unmuted Security Command Center findings by severity."""
    centre = api.client("securitycenter", "v1", [CLOUD_PLATFORM])
    collection = centre.organizations() if api.organisation else centre.projects()
    groups = google.pages(
        lambda token: (
            collection.sources()
            .findings()
            .group(
                parent=f"{api.parent}/sources/-",
                body={
                    "groupBy": "severity",
                    "filter": 'state="ACTIVE" AND NOT mute="MUTED"',
                    "pageSize": 100,
                    **({"pageToken": token} if token else {}),
                },
            )
        ),
        "groupByResults",
        5,
    )
    counts = {"critical": 0, "high": 0}
    for group in groups:
        props = (
            group.get("properties") if isinstance(group.get("properties"), dict) else {}
        )
        severity = str(props.get("severity") or "").lower()
        try:
            number = int(group.get("count") or 0)
        except (TypeError, ValueError):
            continue
        if severity in counts and number >= 0:
            counts[severity] += number
    label = CHECKS["findings"]
    if not counts["critical"] + counts["high"]:
        return Check("findings", label, "met", "No active critical or high findings")
    return Check(
        "findings",
        label,
        "attention",
        f"{counts['critical']} critical and {counts['high']} high findings",
    )


def collect() -> list[ScopeResult]:
    api = connect()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    people: list[Identity] = []

    def admins():
        check, found = administrators(api)
        people.extend(found)
        return [check]

    checks = guarded(CHECKS, ["identity.admins"], admins)
    checks += guarded(CHECKS, ["credentials.key_age"], lambda: [keys(api, now)])
    checks += guarded(
        CHECKS,
        [
            "storage.public_access",
            "google_cloud.key_creation",
            "google_cloud.domain_sharing",
        ],
        lambda: org_policies(api),
    )
    checks += guarded(CHECKS, ["logging.audit_trail"], lambda: [audit_logging(api)])
    checks += guarded(
        CHECKS, ["network.admin_ports", "network.databases"], lambda: network(api)
    )
    checks += guarded(CHECKS, ["findings"], lambda: [findings(api)])
    kind, _, key = api.parent.partition("/")
    return [
        ScopeResult(
            key,
            key,
            checks,
            people,
            kind="organisation" if api.organisation else "project",
        )
    ]


# --- activity -------------------------------------------------------------------


def activity_entry(entry: dict, scope: str) -> Activity | None:
    when = parse_time(entry.get("timestamp"))
    ident = text_field(entry.get("insertId"))
    payload = (
        entry.get("protoPayload") if isinstance(entry.get("protoPayload"), dict) else {}
    )
    method = text_field(payload.get("methodName"), 256)
    if when is None or not ident or not method:
        return None
    short = method.rsplit(".", 1)[-1]
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    code = status.get("code")
    outcome = "failure" if isinstance(code, int) and code != 0 else "success"
    auth = payload.get("authenticationInfo")
    who = text_field(
        (auth if isinstance(auth, dict) else {}).get("principalEmail"), 256
    )
    resource = entry.get("resource") if isinstance(entry.get("resource"), dict) else {}
    return Activity(
        # Insert IDs are unique within a log; the time keeps them apart across logs.
        id=f"{api_timestamp(when)}|{ident}",
        at=when,
        scope=scope,
        action=short,
        actor=who,
        outcome=outcome,
        summary=f"{short} by {who or 'unknown'} ({outcome})",
        detail={
            k: v
            for k, v in {
                "service": text_field(payload.get("serviceName"), 64),
                "resource_type": text_field(resource.get("type"), 64),
                "method": method,
                "resource": text_field(payload.get("resourceName"), 256),
            }.items()
            if v
        },
        raw=entry,
    )


def activity(since: datetime, until: datetime) -> tuple[list[Activity], bool]:
    """Admin Activity audit log entries for IAM, service account and
    organisation policy changes, from the parent and any projects listed in
    KEEN_GOOGLE_CLOUD_PROJECTS."""
    api = connect()
    projects = split_list(settings.google_cloud_projects)
    if len(projects) > MAX_PROJECTS or any(not PROJECT.fullmatch(p) for p in projects):
        raise Unavailable("not_configured")
    methods = " OR ".join(f'protoPayload.methodName:"{m}"' for m in METHODS)
    query = (
        'logName:"cloudaudit.googleapis.com%2Factivity" '
        f'AND timestamp>="{api_timestamp(since)}" AND timestamp<"{api_timestamp(until)}" '
        f"AND ({methods})"
    )
    entries = api.client("logging", "v2", [READ_ONLY]).entries()
    found, token, complete = [], None, False
    for _ in range(MAX_ACTIVITY // LOG_PAGE + 1):
        page = google.execute(
            entries.list(
                body={
                    "resourceNames": [api.parent] + [f"projects/{p}" for p in projects],
                    "filter": query,
                    # Newest first, so a busy window keeps its latest entries.
                    "orderBy": "timestamp desc",
                    "pageSize": LOG_PAGE,
                    **({"pageToken": token} if token else {}),
                }
            )
        )
        found.extend(e for e in page.get("entries") or [] if isinstance(e, dict))
        token = page.get("nextPageToken")
        if not token:
            complete = True
            break
    scope = api.parent.partition("/")[2]
    out = sorted(
        (
            e
            for e in (activity_entry(r, scope) for r in found)
            if e is not None and since <= e.at < until
        ),
        key=lambda e: e.at,
    )
    if len(out) > MAX_ACTIVITY:
        return out[-MAX_ACTIVITY:], False
    return out, complete


PROVIDER = Provider(
    key="google_cloud",
    label="Google Cloud",
    vendor="Google Cloud",
    website="https://cloud.google.com",
    aliases=("Google", "Google LLC"),
    scope_noun="organisation",
    checks=CHECKS,
    collect=collect,
    activity=activity,
    # Admin Activity entries are readable within a few minutes.
    activity_lag=timedelta(minutes=15),
)
