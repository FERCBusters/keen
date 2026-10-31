"""Microsoft Entra ID and Azure posture and activity for one tenant.

KEEN signs in as an app registration with the client credentials flow and
reads the tenant through Microsoft Graph and each Azure subscription through
Azure Resource Manager. The tenant is the app registration's own, and Azure
must confirm that each subscription belongs to it before anything is read, so
results for one tenant never come from another. Tokens last for one read.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import settings
from app.ingest.cloud.api import JsonApi, items
from app.ingest.cloud.base import (
    MAX_ACTIVITY,
    MAX_IDENTITIES,
    MAX_SCOPES,
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
    unknown,
)

LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com"
ARM = "https://management.azure.com"
GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,}")
SECRET_LIMIT = 1024
MAX_PAGES = 100
# Each database server's firewall rules are one more request.
MAX_SERVERS = 200
# Owners named through Graph, one request each.
MAX_NAMED_OWNERS = 50
# Graph refuses these to tenants without a Microsoft Entra ID P1 or P2 licence.
NOT_LICENSED_CODES = {"Authentication_RequestFromNonPremiumTenantOrB2CTenant"}
EXPIRING = timedelta(days=30)
# Entra roles Microsoft labels privileged, apart from the reader roles.
PRIVILEGED_ROLES = {
    "62e90394-69f5-4237-9190-012177145e10": "Global Administrator",
    "e8611ab8-c189-46e8-94e1-60213ab1f814": "Privileged Role Administrator",
    "7be44c8a-adaf-4e2a-84d6-ab2649e08a13": "Privileged Authentication Administrator",
    "194ae4cb-b126-40b2-bd5b-6091b380977d": "Security Administrator",
    "fe930be7-5e62-47db-91af-98c3a49a38b1": "User Administrator",
    "9b895d92-2cd3-44c7-9d02-a6ac2d5ea5c3": "Application Administrator",
    "158c047a-c907-4556-b7ef-446551a6b5f7": "Cloud Application Administrator",
    "cf1c38e5-3621-4004-a7cb-879624dced7c": "Application Developer",
    "c4e39bd9-1100-46d3-8c65-fb160da0071f": "Authentication Administrator",
    "25a516ed-2fa0-40ea-a2d0-12923a21473a": "Authentication Extensibility Administrator",
    "0b00bede-4072-4d22-b441-e7df02a1ef63": (
        "Authentication Extensibility Password Administrator"
    ),
    "b1be1c3e-b65d-4f19-8427-f6fa0d97feb9": "Conditional Access Administrator",
    "8ac3fc64-6eca-42ea-9e69-59f4c7b60eb2": "Hybrid Identity Administrator",
    "729827e3-9c14-49f7-bb1b-9608f156bbb8": "Helpdesk Administrator",
    "966707d0-3269-4727-9be2-8c3a10f19b9d": "Password Administrator",
    "3a2c62db-5318-420d-8d74-23affee5d9d5": "Intune Administrator",
    "7698a772-787b-4ac8-901f-60d6b08affd2": "Cloud Device Administrator",
    "8329153b-31d0-4727-b945-745eb3bc5f31": "Domain Name Administrator",
    "be2f45a1-457d-42af-a067-6ec1fa63bc45": "External Identity Provider Administrator",
    "45d8d3c5-c802-45c6-b32a-1d70b5e1e86e": "Identity Governance Administrator",
    "59d46f88-662b-457b-bceb-5c3809e5908f": "Lifecycle Workflows Administrator",
    "9360feb5-f418-4baa-8175-e2a00bac4301": "Directory Writers",
    "aaf43236-0c0d-4d5f-883a-6955382ac081": "B2C IEF Keyset Administrator",
    "ecb2c6bf-0ab6-418e-bd87-7986f8d63bbe": "Attribute Provisioning Administrator",
    "db506228-d27e-4b7d-95e5-295956d6615f": "Agent ID Administrator",
    "d2562ede-74db-457e-a7b6-544e236ebb61": "AI Administrator",
    "58f930cc-fcf4-4152-852c-1d7dbf502139": "Entra SOC Identity Responder",
    "5f2222b1-57c3-48ba-8ad5-d4759f1fde6f": "Security Operator",
    "1981f584-96e9-4a6f-95b0-f522373f8fae": "Tenant Governance Administrator",
    "4ba39ca4-527c-499a-b93d-d9b492c50246": "Partner Tier1 Support",
    "e00e864a-17c5-4a4b-9c06-f5b95a8d5bd8": "Partner Tier2 Support",
}
# Azure roles that control a subscription.
OWNER_ROLES = {
    "8e3af657-a8ff-443c-a75c-2fe8c4bcb635": "Owner",
    "18d7d88d-d35e-4fb5-a5c3-7773c20a72d9": "User Access Administrator",
}
ADMIN_PORTS = (22, 3389)
INTERNET = {"*", "internet", "any", "0.0.0.0", "0.0.0.0/0", "::/0"}
WHOLE_INTERNET = ("0.0.0.0", "255.255.255.255")
DATABASE_TYPES = (
    ("Microsoft.Sql/servers", "2021-11-01"),
    ("Microsoft.DBforPostgreSQL/flexibleServers", "2022-12-01"),
    ("Microsoft.DBforMySQL/flexibleServers", "2021-05-01"),
)
VERSIONS = {
    "subscription": "2022-12-01",
    "roles": "2022-04-01",
    "diagnostics": "2021-05-01-preview",
    "storage": "2023-01-01",
    "network": "2023-09-01",
    "score": "2020-01-01",
    "graph": "2024-04-01",
    "activity": "2015-04-01",
}
# Unhealthy Defender recommendations by severity, for one subscription.
FINDINGS_QUERY = (
    "securityresources"
    " | where type == 'microsoft.security/assessments'"
    " | where tostring(properties.status.code) == 'Unhealthy'"
    " | summarize findings = count() by severity = tostring(properties.metadata.severity)"
)
CHECKS = {
    "identity.mfa": "MFA for users",
    "identity.admin_mfa": "MFA for administrators",
    "identity.mfa_enforced": "MFA enforcement",
    "identity.admins": "Administrators",
    "identity.guests": "Guest accounts",
    "identity.inactive": "Unused accounts",
    "credentials.key_age": "App registration secrets and certificates",
    "logging.audit_trail": "Activity log export",
    "storage.public_access": "Public blob access",
    "network.admin_ports": "SSH and RDP open to the internet",
    "network.databases": "Databases open to the internet",
    "findings": "Defender for Cloud recommendations",
}
TENANT_CHECKS = (
    "identity.mfa",
    "identity.admin_mfa",
    "identity.mfa_enforced",
    "identity.admins",
    "identity.guests",
    "identity.inactive",
    "credentials.key_age",
)
SUBSCRIPTION_CHECKS = (
    "identity.admins",
    "logging.audit_trail",
    "storage.public_access",
    "network.admin_ports",
    "network.databases",
    "findings",
)
# Graph application permissions; Azure roles are Reader and Security Reader.
PERMISSIONS = (
    "AuditLog.Read.All",
    "Policy.Read.All",
    "User.Read.All",
    "RoleManagement.Read.Directory",
    "Application.Read.All",
)
AZURE_ROLES = ("Reader", "Security Reader")


# --- transport ------------------------------------------------------------


def _error(response: httpx.Response) -> tuple[str, str]:
    try:
        body = response.json()
    except ValueError:
        return "", ""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("code") or ""), str(error.get("message") or "")
    return "", ""


def explain(response: httpx.Response) -> str:
    status = response.status_code
    if status == 401:
        return "not_configured"
    if status == 403:
        code, message = _error(response)
        if code in NOT_LICENSED_CODES or "premium licen" in message.lower():
            return "not_licensed"
        return "permission_denied"
    if status in (400, 404):
        return "not_configured"
    return "unavailable"


class Azure:
    """Graph and Resource Manager for one read; its tokens end with it."""

    def __init__(self, tenant: str, client_id: str, secret: str, *, client=None):
        self.tenant = tenant
        self.login = JsonApi(LOGIN, {}, client=client)
        self.apis = {}
        try:
            for name, origin in (("graph", GRAPH), ("arm", ARM)):
                token = self._token(client_id, secret, origin + "/.default")
                self.apis[name] = JsonApi(
                    origin,
                    {"Authorization": f"Bearer {token}"},
                    explain=explain,
                    client=client,
                )
        except BaseException:
            self.close()
            raise

    def _token(self, client_id: str, secret: str, scope: str) -> str:
        try:
            response = self.login.http.post(
                f"{LOGIN}/{self.tenant}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": secret,
                    "scope": scope,
                },
                headers={"Accept": "application/json"},
            )
        except (httpx.HTTPError, ValueError, TimeoutError, OSError):
            raise Unavailable("unavailable") from None
        if response.status_code in (400, 401):
            # A wrong tenant, client ID or secret, or an expired secret.
            raise Unavailable("not_configured")
        if response.status_code != 200:
            raise Unavailable("unavailable")
        try:
            token = response.json().get("access_token")
        except (ValueError, AttributeError):
            token = None
        if not isinstance(token, str) or not token:
            raise Unavailable("unavailable")
        return token

    def close(self):
        for api in self.apis.values():
            api.close()
        self.apis.clear()
        self.login.close()

    def get(self, name, path, *, params=None, missing=None) -> dict:
        return self.apis[name].get(path, params=params, missing=missing)

    def post(self, name, path, body, *, params=None) -> dict:
        return self.apis[name].post(path, body, params=params)

    def listing(self, name, path, *, params=None, limit: int | None = None):
        """A paged listing up to ``limit`` pages (by default MAX_PAGES), and
        whether it was all read. Paging links are followed only on the origin
        that issued them."""
        api = self.apis[name]
        url, found = api.origin + path, []
        for _ in range(MAX_PAGES if limit is None else limit):
            body = api.body(api.send("GET", url, params=params))
            found.extend(items(body, "value"))
            following = body.get("@odata.nextLink") or body.get("nextLink")
            if not following:
                return found, True
            if not isinstance(following, str):
                raise Unavailable("unavailable")
            url, params = following, None
        return found, False

    def pages(self, name, path, *, params=None) -> list:
        """Every item of a listing; a longer one is refused, since a partial
        population would misstate a check."""
        found, complete = self.listing(name, path, params=params)
        if not complete:
            raise Unavailable("unavailable")
        return found


def _split(value) -> list[str]:
    return [v.lower() for v in split_list(value)]


def connect() -> Azure:
    if settings.demo_mode:  # pragma: no cover - the runner answers demo reads
        raise Unavailable("not_configured")
    tenant = str(settings.azure_tenant_id or "").strip().lower()
    client_id = str(settings.azure_client_id or "").strip().lower()
    secret = str(settings.azure_client_secret or "")
    if (
        not GUID.fullmatch(tenant)
        or not GUID.fullmatch(client_id)
        or not secret
        or len(secret) > SECRET_LIMIT
    ):
        raise Unavailable("not_configured")
    return Azure(tenant, client_id, secret)


def subscriptions(api: Azure) -> list[tuple[str, str, str]]:
    """(ID, name, reason) of each subscription to read: those listed in
    KEEN_AZURE_SUBSCRIPTIONS, or every one the app can see in the tenant. A
    listed subscription that cannot be read, or belongs to another tenant,
    carries the reason instead."""
    wanted = _split(settings.azure_subscriptions)
    if (
        len(wanted) >= MAX_SCOPES
        or len(set(wanted)) != len(wanted)
        or any(not GUID.fullmatch(w) for w in wanted)
    ):
        raise Unavailable("not_configured")
    params = {"api-version": VERSIONS["subscription"]}
    if not wanted:
        found, _ = api.listing("arm", "/subscriptions", params=params, limit=5)
        return [
            (
                str(s.get("subscriptionId")).lower(),
                clip(s.get("displayName") or s.get("subscriptionId"), 128),
                "",
            )
            for s in found
            if GUID.fullmatch(str(s.get("subscriptionId") or "").lower())
            # Subscriptions of other tenants are left out.
            and str(s.get("tenantId") or "").lower() == api.tenant
        ][: MAX_SCOPES - 1]
    out = []
    for ident in wanted:
        try:
            item = api.get("arm", f"/subscriptions/{ident}", params=params)
        except Unavailable as exc:
            out.append((ident, ident, exc.reason))
            continue
        if str(item.get("tenantId") or "").lower() != api.tenant:
            # Results for one tenant must never come from another.
            out.append((ident, ident, "not_configured"))
            continue
        out.append((ident, clip(item.get("displayName") or ident, 128), ""))
    return out


# --- shared ------------------------------------------------------------------


def _email(user: dict) -> str:
    name = str(user.get("userPrincipalName") or "")
    mail = str(user.get("mail") or "")
    return mail if EMAIL.fullmatch(mail) else name if EMAIL.fullmatch(name) else ""


def _users(api: Azure, select: str, *, filter_text: str = "") -> list:
    params = {"$select": select, "$top": "999"}
    if filter_text:
        params["$filter"] = filter_text
    found = api.pages("graph", "/v1.0/users", params=params)
    if len(found) > MAX_IDENTITIES:
        raise Unavailable("unavailable")
    return found


def _person(people: dict, user: dict) -> Identity:
    key = str(user.get("id") or "")
    if key not in people:
        people[key] = Identity(
            name=clip(user.get("userPrincipalName") or key),
            email=_email(user),
            kind="guest"
            if str(user.get("userType") or "").lower() == "guest"
            else "user",
        )
    return people[key]


# --- tenant -----------------------------------------------------------------


def mfa(api: Azure, people: dict) -> list[Check]:
    """MFA capability from the authentication methods registration report."""
    rows = api.pages(
        "graph", "/v1.0/reports/authenticationMethods/userRegistrationDetails"
    )
    members = [r for r in rows if r.get("userType") == "member"]
    if len(members) > MAX_IDENTITIES:
        raise Unavailable("unavailable")
    found = []
    for row in members:
        person = _person(people, row)
        person.mfa = row.get("isMfaCapable") is True
        person.admin = person.admin or row.get("isAdmin") is True
        found.append(person)
    return [
        mfa_check("identity.mfa", CHECKS["identity.mfa"], found, who="users"),
        mfa_check(
            "identity.admin_mfa",
            CHECKS["identity.admin_mfa"],
            [p for p in found if p.admin],
            who="administrators",
        ),
    ]


def _narrowed(conditions: dict) -> bool:
    """Whether a policy applies only to some sign-ins: certain locations,
    platforms, client apps, devices or risk levels."""
    places = conditions.get("locations")
    if isinstance(places, dict) and (
        set(places.get("includeLocations") or ["All"]) != {"All"}
        or places.get("excludeLocations")
    ):
        return True
    platforms = conditions.get("platforms")
    if isinstance(platforms, dict) and (
        {str(p).lower() for p in platforms.get("includePlatforms") or ["all"]}
        != {"all"}
        or platforms.get("excludePlatforms")
    ):
        return True
    if "all" not in [str(c) for c in conditions.get("clientAppTypes") or ["all"]]:
        return True
    return bool(
        conditions.get("devices")
        or conditions.get("userRiskLevels")
        or conditions.get("signInRiskLevels")
        or conditions.get("servicePrincipalRiskLevels")
        or conditions.get("insiderRiskLevels")
        or conditions.get("authenticationFlows")
    )


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def requires_mfa(policy: dict) -> bool:
    """Whether a Conditional Access policy demands MFA of every user for every
    cloud app, with no alternative that satisfies it instead."""
    if policy.get("state") != "enabled":
        return False
    conditions = _dict(policy.get("conditions"))
    users = _dict(conditions.get("users"))
    apps = _dict(conditions.get("applications"))
    if "All" not in (users.get("includeUsers") or []):
        return False
    if "All" not in (apps.get("includeApplications") or []) or apps.get(
        "excludeApplications"
    ):
        return False
    if _narrowed(conditions):
        return False
    grant = _dict(policy.get("grantControls"))
    controls = [c for c in grant.get("builtInControls") or [] if isinstance(c, str)]
    strength = isinstance(grant.get("authenticationStrength"), dict)
    if "block" in controls or not ("mfa" in controls or strength):
        return False
    alternatives = (
        len(controls)
        + int(strength)
        + len(grant.get("customAuthenticationFactors") or [])
        + len(grant.get("termsOfUse") or [])
    )
    return grant.get("operator") == "AND" or alternatives == 1


def enforcement(api: Azure, people: dict) -> Check:
    """Security defaults, or a Conditional Access policy that requires MFA of
    everyone for every cloud app; users every such policy excludes are named."""
    label = CHECKS["identity.mfa_enforced"]
    defaults = api.get(
        "graph", "/v1.0/policies/identitySecurityDefaultsEnforcementPolicy"
    ).get("isEnabled")
    if defaults is True:
        return Check("identity.mfa_enforced", label, "met", "Security defaults are on")
    try:
        policies = api.pages("graph", "/v1.0/identity/conditionalAccess/policies")
    except Unavailable as exc:
        if exc.reason != "not_licensed":
            raise
        # Without the licence there can be no Conditional Access policy.
        policies = []
    covering = [p for p in policies if requires_mfa(p)]
    if not covering:
        return Check(
            "identity.mfa_enforced",
            label,
            "attention",
            "Neither security defaults nor a Conditional Access policy requires "
            "MFA of every user",
        )
    excluded = set.intersection(
        *[
            {str(u) for u in p["conditions"]["users"].get("excludeUsers") or []}
            for p in covering
        ]
    )
    # Group and role exclusions are counted from the least excluding policy.
    groups = min(
        len(p["conditions"]["users"].get("excludeGroups") or [])
        + len(p["conditions"]["users"].get("excludeRoles") or [])
        for p in covering
    )
    names = [people[u].name if u in people else u for u in sorted(excluded)]
    detail = "MFA is required by " + plural(
        len(covering), "Conditional Access policy", "Conditional Access policies"
    )
    if names:
        detail += f"; {plural(len(names), 'user')} excluded from all of them"
    if groups:
        detail += f"; {plural(groups, 'group or role', 'groups or roles')} excluded"
    status = "attention" if names else "met"
    return Check("identity.mfa_enforced", label, status, detail, names)


def directory(api: Azure, people: dict) -> list[Check]:
    """Holders of privileged Entra roles across the tenant, and guests."""
    assignments = api.pages(
        "graph",
        "/v1.0/roleManagement/directory/roleAssignments",
        params={"$expand": "principal"},
    )
    holders: dict[str, str] = {}
    for assignment in assignments:
        role = str(assignment.get("roleDefinitionId") or "")
        if role not in PRIVILEGED_ROLES or assignment.get("directoryScopeId") != "/":
            continue
        principal = _dict(assignment.get("principal"))
        key = str(assignment.get("principalId") or principal.get("id") or "")
        if not key or key in holders:
            continue
        if principal.get("@odata.type") == "#microsoft.graph.user":
            person = _person(people, {**principal, "id": key})
            person.admin = True
            holders[key] = person.name
        else:
            holders[key] = clip(principal.get("displayName") or key)
    guests = _users(
        api, "id,accountEnabled,externalUserState", filter_text="userType eq 'Guest'"
    )
    pending = sum(
        1 for g in guests if g.get("externalUserState") == "PendingAcceptance"
    )
    return [
        Check(
            "identity.admins",
            CHECKS["identity.admins"],
            "info",
            f"{plural(len(holders), 'user, group or app', 'users, groups or apps')} "
            "hold privileged Entra roles",
            list(holders.values()),
        ),
        Check(
            "identity.guests",
            CHECKS["identity.guests"],
            "info",
            f"{plural(len(guests), 'guest')}, {pending} yet to accept their invitation",
        ),
    ]


def sign_in_activity(api: Azure, people: dict, now: datetime) -> Check:
    users = [
        u
        for u in _users(
            api,
            "id,userPrincipalName,mail,accountEnabled,userType,createdDateTime,"
            "signInActivity",
        )
        if u.get("accountEnabled") is True
    ]
    found = []
    for user in users:
        person = _person(people, user)
        activity = _dict(user.get("signInActivity"))
        person.last_used = parse_time(
            activity.get("lastSuccessfulSignInDateTime")
            or activity.get("lastSignInDateTime")
        )
        person.created = parse_time(user.get("createdDateTime"))
        found.append(person)
    return inactive_check(
        "identity.inactive",
        CHECKS["identity.inactive"],
        found,
        settings.cloud_inactive_days,
        now,
    )


def apps(api: Azure, now: datetime) -> Check:
    """Client secret and certificate age for each app registration."""
    label = CHECKS["credentials.key_age"]
    found = api.pages(
        "graph",
        "/v1.0/applications",
        params={
            "$select": "id,appId,displayName,passwordCredentials,keyCredentials",
            "$top": "200",
        },
    )
    if len(found) > MAX_IDENTITIES:
        raise Unavailable("unavailable")
    limit = timedelta(days=settings.cloud_key_age_days)
    total, old, expiring = 0, [], 0
    for app in found:
        name = clip(app.get("displayName") or app.get("appId") or "")
        for credential in [
            c
            for field in ("passwordCredentials", "keyCredentials")
            for c in app.get(field) or []
            if isinstance(c, dict)
        ]:
            ends = parse_time(credential.get("endDateTime"))
            if ends is not None and ends <= now:
                continue
            total += 1
            if ends is not None and ends - now <= EXPIRING:
                expiring += 1
            started = parse_time(credential.get("startDateTime"))
            if started is not None and now - started >= limit:
                old.append(name)
    if not total:
        return Check(
            "credentials.key_age", label, "info", "No live secrets or certificates"
        )
    detail = (
        f"{len(old)} of {plural(total, 'live secret or certificate', 'live secrets and certificates')} "
        f"older than {settings.cloud_key_age_days} days"
    )
    if expiring:
        detail += f"; {expiring} expire within 30 days"
    return Check(
        "credentials.key_age", label, "attention" if old else "met", detail, old
    )


def tenant_result(api: Azure) -> ScopeResult:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    people: dict[str, Identity] = {}
    checks = guarded(
        CHECKS, ["identity.mfa", "identity.admin_mfa"], lambda: mfa(api, people)
    )
    checks += guarded(
        CHECKS, ["identity.mfa_enforced"], lambda: [enforcement(api, people)]
    )
    checks += guarded(
        CHECKS, ["identity.admins", "identity.guests"], lambda: directory(api, people)
    )
    checks += guarded(
        CHECKS, ["identity.inactive"], lambda: [sign_in_activity(api, people, now)]
    )
    checks += guarded(CHECKS, ["credentials.key_age"], lambda: [apps(api, now)])
    identities = sorted(people.values(), key=lambda p: p.name.lower())
    return ScopeResult(
        api.tenant, "Entra tenant", checks, identities[:MAX_IDENTITIES], kind="tenant"
    )


# --- subscriptions ---------------------------------------------------------------


def _arm(api: Azure, path: str, version: str, **params) -> list:
    return api.pages("arm", path, params={"api-version": version, **params})


def owners(api: Azure, sub: str) -> Check:
    """Owners and User Access Administrators at the subscription or above."""
    assignments = _arm(
        api,
        f"/subscriptions/{sub}/providers/Microsoft.Authorization/roleAssignments",
        VERSIONS["roles"],
        **{"$filter": "atScope()"},
    )
    holders: dict[str, str] = {}
    named = 0
    for assignment in assignments:
        props = _dict(assignment.get("properties"))
        role = str(props.get("roleDefinitionId") or "").rsplit("/", 1)[-1].lower()
        if role not in OWNER_ROLES:
            continue
        assigned = str(props.get("scope") or "").lower()
        if assigned not in ("/", f"/subscriptions/{sub}") and not assigned.startswith(
            "/providers/microsoft.management/managementgroups/"
        ):
            continue
        key = str(props.get("principalId") or "").lower()
        if not GUID.fullmatch(key) or key in holders:
            continue
        name = key
        if props.get("principalType") == "User" and named < MAX_NAMED_OWNERS:
            named += 1
            try:
                user = api.get(
                    "graph",
                    f"/v1.0/users/{key}",
                    params={"$select": "id,userPrincipalName"},
                    missing={},
                )
                name = clip(user.get("userPrincipalName") or key)
            except Unavailable:
                pass  # Without Graph access the owner keeps their object ID.
        holders[key] = name
    return Check(
        "identity.admins",
        "Owners",
        "info",
        f"{plural(len(holders), 'principal')} hold Owner or User Access Administrator",
        list(holders.values()),
    )


def audit_trail(api: Azure, sub: str) -> Check:
    found = api.get(
        "arm",
        f"/subscriptions/{sub}/providers/Microsoft.Insights/diagnosticSettings",
        params={"api-version": VERSIONS["diagnostics"]},
    ).get("value")
    sinks = 0
    for setting in found if isinstance(found, list) else []:
        props = _dict(setting.get("properties") if isinstance(setting, dict) else None)
        destination = any(
            props.get(k)
            for k in (
                "workspaceId",
                "storageAccountId",
                "eventHubAuthorizationRuleId",
                "marketplacePartnerId",
            )
        )
        administrative = any(
            isinstance(log, dict)
            and log.get("enabled") is True
            and (
                log.get("category") == "Administrative"
                or log.get("categoryGroup") == "allLogs"
            )
            for log in props.get("logs") or []
        )
        sinks += bool(destination and administrative)
    label = CHECKS["logging.audit_trail"]
    if sinks:
        return Check(
            "logging.audit_trail",
            label,
            "met",
            f"The activity log is exported to {plural(sinks, 'destination')}",
        )
    return Check(
        "logging.audit_trail", label, "attention", "The activity log is not exported"
    )


def public_storage(api: Azure, sub: str) -> Check:
    accounts = _arm(
        api,
        f"/subscriptions/{sub}/providers/Microsoft.Storage/storageAccounts",
        VERSIONS["storage"],
    )
    public = [
        clip(a.get("name"), 128)
        for a in accounts
        if _dict(a.get("properties")).get("allowBlobPublicAccess") is True
    ]
    return Check(
        "storage.public_access",
        CHECKS["storage.public_access"],
        "attention" if public else "met",
        f"{len(public)} of {plural(len(accounts), 'storage account')} allow public blob access",
        public,
    )


def _covers(rule: dict, port: int) -> bool:
    if str(rule.get("protocol") or "").lower() not in ("tcp", "*"):
        return False
    for value in [rule.get("destinationPortRange")] + list(
        rule.get("destinationPortRanges") or []
    ):
        text = str(value or "").strip()
        if text == "*":
            return True
        low, _, high = text.partition("-")
        try:
            if int(low) <= port <= int(high or low):
                return True
        except ValueError:
            continue
    return False


def _from_internet(rule: dict) -> bool:
    sources = [rule.get("sourceAddressPrefix")] + list(
        rule.get("sourceAddressPrefixes") or []
    )
    return any(str(s or "").strip().lower() in INTERNET for s in sources)


def exposed_ports(group: dict) -> list[int]:
    """Remote-access ports the internet reaches through one network security
    group: inbound rules apply in priority order, and Azure's default rules
    deny the internet."""
    rules = []
    for rule in _dict(group.get("properties")).get("securityRules") or []:
        body = rule.get("properties") if isinstance(rule, dict) else None
        if isinstance(body, dict) and str(body.get("direction")) == "Inbound":
            if isinstance(body.get("priority"), int):
                rules.append((body["priority"], body))
    rules.sort(key=lambda pair: pair[0])
    out = []
    for port in ADMIN_PORTS:
        for _, rule in rules:
            if _covers(rule, port) and _from_internet(rule):
                if str(rule.get("access")) == "Allow":
                    out.append(port)
                break
    return out


def exposure(api: Azure, sub: str) -> Check:
    groups = _arm(
        api,
        f"/subscriptions/{sub}/providers/Microsoft.Network/networkSecurityGroups",
        VERSIONS["network"],
    )
    attached, exposing = 0, []
    for group in groups:
        props = _dict(group.get("properties"))
        if not (props.get("networkInterfaces") or props.get("subnets")):
            continue
        attached += 1
        if exposed_ports(group):
            exposing.append(clip(group.get("name") or group.get("id"), 128))
    return Check(
        "network.admin_ports",
        CHECKS["network.admin_ports"],
        "attention" if exposing else "met",
        f"{len(exposing)} of {plural(attached, 'network security group')} in use admit "
        "SSH or RDP from anywhere",
        exposing,
    )


def databases(api: Azure, sub: str) -> Check:
    servers = [
        (server, version)
        for kind, version in DATABASE_TYPES
        for server in _arm(api, f"/subscriptions/{sub}/providers/{kind}", version)
    ]
    if len(servers) > MAX_SERVERS:
        raise Unavailable("unavailable")
    exposed = []
    for server, version in servers:
        ident = str(server.get("id") or "")
        if not ident.lower().startswith(f"/subscriptions/{sub}/"):
            raise Unavailable("unavailable")
        props = _dict(server.get("properties"))
        public = props.get("publicNetworkAccess") or _dict(props.get("network")).get(
            "publicNetworkAccess"
        )
        if str(public or "").lower() == "disabled":
            continue
        ranges = {
            (
                str(_dict(r.get("properties")).get("startIpAddress") or ""),
                str(_dict(r.get("properties")).get("endIpAddress") or ""),
            )
            for r in _arm(api, f"{ident}/firewallRules", version)
        }
        if WHOLE_INTERNET in ranges:
            exposed.append(clip(server.get("name") or ident, 128))
    return Check(
        "network.databases",
        CHECKS["network.databases"],
        "attention" if exposed else "met",
        f"{len(exposed)} of {plural(len(servers), 'database server')} admit every address",
        exposed,
    )


def defender(api: Azure, sub: str) -> Check:
    """Defender for Cloud's secure score and unhealthy recommendations."""
    score = api.get(
        "arm",
        f"/subscriptions/{sub}/providers/Microsoft.Security/secureScores/ascScore",
        params={"api-version": VERSIONS["score"]},
    )
    percentage = _dict(_dict(score.get("properties")).get("score")).get("percentage")
    result = api.post(
        "arm",
        "/providers/Microsoft.ResourceGraph/resources",
        {
            "subscriptions": [sub],
            "query": FINDINGS_QUERY,
            "options": {"resultFormat": "objectArray"},
        },
        params={"api-version": VERSIONS["graph"]},
    )
    counts = {"high": 0, "medium": 0, "low": 0}
    for row in result.get("data") if isinstance(result.get("data"), list) else []:
        severity = str(_dict(row).get("severity") or "").lower()
        number = _dict(row).get("findings")
        if severity in counts and isinstance(number, int) and number >= 0:
            counts[severity] += number
    detail = f"{counts['high']} high, {counts['medium']} medium and {counts['low']} low recommendations"
    if isinstance(percentage, (int, float)) and not isinstance(percentage, bool):
        detail += f"; secure score {round(max(0.0, min(1.0, percentage)) * 100)}%"
    return Check(
        "findings",
        CHECKS["findings"],
        "attention" if counts["high"] else "met",
        detail,
    )


def subscription_result(api: Azure, sub: str, name: str) -> ScopeResult:
    checks = guarded(CHECKS, ["identity.admins"], lambda: [owners(api, sub)])
    checks += guarded(CHECKS, ["logging.audit_trail"], lambda: [audit_trail(api, sub)])
    checks += guarded(
        CHECKS, ["storage.public_access"], lambda: [public_storage(api, sub)]
    )
    checks += guarded(CHECKS, ["network.admin_ports"], lambda: [exposure(api, sub)])
    checks += guarded(CHECKS, ["network.databases"], lambda: [databases(api, sub)])
    checks += guarded(CHECKS, ["findings"], lambda: [defender(api, sub)])
    return ScopeResult(sub, name, checks, kind="subscription")


def collect() -> list[ScopeResult]:
    api = connect()
    try:
        out = [tenant_result(api)]
        for sub, name, reason in subscriptions(api):
            if reason:
                checks = unknown(CHECKS, SUBSCRIPTION_CHECKS, reason)
                out.append(ScopeResult(sub, name, checks, kind="subscription"))
            else:
                out.append(subscription_result(api, sub, name))
        return out
    finally:
        api.close()


# --- activity ---------------------------------------------------------------------


def sign_in_entry(item: dict, tenant: str) -> Activity | None:
    when = parse_time(item.get("createdDateTime"))
    ident = text_field(item.get("id"))
    if when is None or not ident:
        return None
    code = _dict(item.get("status")).get("errorCode")
    outcome = "success" if code == 0 else "failure" if isinstance(code, int) else "info"
    who = text_field(item.get("userPrincipalName"), 256) or text_field(
        item.get("userId")
    )
    detail = {
        "service": "entra",
        "application": text_field(item.get("appDisplayName")),
        "source_ip": text_field(item.get("ipAddress"), 64),
    }
    if isinstance(code, int) and code:
        detail["error_code"] = str(code)
    requirement = item.get("authenticationRequirement")
    if isinstance(requirement, str):
        detail["mfa_used"] = requirement == "multiFactorAuthentication"
    return Activity(
        id="signin:" + ident,
        at=when,
        scope=tenant,
        action="SignIn",
        actor=who,
        outcome=outcome,
        summary=f"Sign-in by {who or 'unknown'} to {detail['application'] or 'an app'} ({outcome})",
        detail={k: v for k, v in detail.items() if v != ""},
        raw=item,
    )


def audit_entry(item: dict, tenant: str) -> Activity | None:
    when = parse_time(item.get("activityDateTime"))
    ident = text_field(item.get("id"))
    name = text_field(item.get("activityDisplayName"))
    if when is None or not ident or not name:
        return None
    outcome = {"success": "success", "failure": "failure", "timeout": "failure"}.get(
        item.get("result"), "info"
    )
    initiated = _dict(item.get("initiatedBy"))
    user, app = _dict(initiated.get("user")), _dict(initiated.get("app"))
    who = text_field(user.get("userPrincipalName"), 256) or text_field(
        app.get("displayName")
    )
    return Activity(
        id="audit:" + ident,
        at=when,
        scope=tenant,
        action=name,
        actor=who,
        outcome=outcome,
        summary=f"{name} by {who or 'unknown'} ({outcome})",
        detail={
            k: v
            for k, v in {
                "service": text_field(item.get("loggedByService"), 64) or "entra",
                "category": text_field(item.get("category"), 64),
            }.items()
            if v
        },
        raw=item,
    )


def activity_entry(item: dict, sub: str) -> Activity | None:
    when = parse_time(item.get("eventTimestamp"))
    ident = text_field(item.get("eventDataId"))
    name = text_field(_dict(item.get("operationName")).get("value"), 256)
    state = _dict(item.get("status")).get("value")
    kind = _dict(item.get("category")).get("value")
    if when is None or not ident or not name or state not in ("Succeeded", "Failed"):
        # Started and accepted entries repeat the operation they lead to.
        return None
    if kind not in ("Administrative", "Security", "Policy"):
        return None
    outcome = "success" if state == "Succeeded" else "failure"
    who = text_field(item.get("caller"), 256)
    return Activity(
        id="activity:" + ident,
        at=when,
        scope=sub,
        action=name,
        actor=who,
        outcome=outcome,
        summary=f"{name} by {who or 'unknown'} ({outcome})",
        detail={"service": name.split("/", 1)[0], "category": kind},
        raw=item,
    )


def _graph_window(api, path, field, since, until):
    params = {
        "$filter": f"{field} ge {api_timestamp(since)} and {field} lt {api_timestamp(until)}",
        "$top": "1000",
    }
    return api.listing("graph", path, params=params, limit=MAX_ACTIVITY // 1000 + 1)


def activity(since: datetime, until: datetime) -> tuple[list[Activity], bool]:
    """Entra sign-ins and directory audits, and each subscription's activity
    log, in [since, until)."""
    api = connect()
    try:
        found, complete = [], True
        for path, field, parse in (
            ("/v1.0/auditLogs/signIns", "createdDateTime", sign_in_entry),
            ("/v1.0/auditLogs/directoryAudits", "activityDateTime", audit_entry),
        ):
            rows, done = _graph_window(api, path, field, since, until)
            complete = complete and done
            found.extend(parse(r, api.tenant) for r in rows)
        for sub, _name, reason in subscriptions(api):
            if reason:
                continue
            rows, done = api.listing(
                "arm",
                f"/subscriptions/{sub}/providers/Microsoft.Insights/eventtypes/management/values",
                params={
                    "api-version": VERSIONS["activity"],
                    "$filter": f"eventTimestamp ge '{api_timestamp(since)}' and "
                    f"eventTimestamp le '{api_timestamp(until)}'",
                },
                limit=MAX_ACTIVITY // 100 + 1,
            )
            complete = complete and done
            found.extend(activity_entry(r, sub) for r in rows)
            if len(found) > MAX_ACTIVITY:
                break
    finally:
        api.close()
    entries = sorted(
        (e for e in found if e is not None and since <= e.at < until),
        key=lambda e: e.at,
    )
    if len(entries) > MAX_ACTIVITY:
        return entries[-MAX_ACTIVITY:], False
    return entries, complete


PROVIDER = Provider(
    key="azure",
    label="Microsoft Entra ID and Azure",
    vendor="Microsoft",
    website="https://www.microsoft.com",
    aliases=("Microsoft Azure", "Microsoft 365", "Microsoft Corporation"),
    scope_noun="subscription",
    checks=CHECKS,
    collect=collect,
    activity=activity,
    # Entra and Azure make log entries readable within about 30 minutes.
    activity_lag=timedelta(minutes=30),
)
