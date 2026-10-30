"""Microsoft Entra ID and Azure posture and activity against a fake Microsoft
behind httpx's mock transport. No network is used."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest
from app.core.config import settings
from app.ingest.cloud import azure as az
from app.ingest.cloud.base import Unavailable

TENANT = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
SUB = "11111111-2222-4333-8444-555555555555"
OTHER = "22222222-3333-4444-8555-666666666666"
CLIENT = "99999999-8888-4777-8666-555555555555"
SECRET = "synthetic~secret.value-123"
NOW = datetime.now(timezone.utc).replace(microsecond=0)
GA = "62e90394-69f5-4237-9190-012177145e10"
OWNER = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"
READER = "acdd72a7-3385-48ef-bd42-f606fba81ae7"


def stamp(when):
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.1234567Z")


def ago(days):
    return stamp(NOW - timedelta(days=days))


def uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def users():
    return [
        {
            "id": uid(1),
            "userPrincipalName": "alice@example.org",
            "mail": "alice@example.org",
            "accountEnabled": True,
            "userType": "Member",
            "createdDateTime": ago(500),
            "signInActivity": {"lastSuccessfulSignInDateTime": ago(1)},
        },
        {
            "id": uid(2),
            "userPrincipalName": "bob@example.org",
            "mail": None,
            "accountEnabled": True,
            "userType": "Member",
            "createdDateTime": ago(400),
            "signInActivity": {"lastSignInDateTime": ago(120)},
        },
        {
            "id": uid(3),
            "userPrincipalName": "carol_partner.example#EXT#@example.org",
            "mail": "carol@partner.example",
            "accountEnabled": True,
            "userType": "Guest",
            "createdDateTime": ago(30),
        },
        {
            "id": uid(4),
            "userPrincipalName": "old@example.org",
            "accountEnabled": False,
            "userType": "Member",
        },
        {
            "id": uid(5),
            "userPrincipalName": "break-glass@example.org",
            "accountEnabled": True,
            "userType": "Member",
            "createdDateTime": "not a date",
            "signInActivity": None,
        },
    ]


def server(kind, name, public="Enabled"):
    return {
        "id": f"/subscriptions/{SUB}/resourceGroups/data/providers/{kind}/{name}",
        "name": name,
        "properties": {"publicNetworkAccess": public},
    }


def rule(access, source, port, priority, protocol="Tcp", **extra):
    return {
        "properties": {
            "direction": "Inbound",
            "access": access,
            "protocol": protocol,
            "sourceAddressPrefix": source,
            "destinationPortRange": port,
            "priority": priority,
            **extra,
        }
    }


def default_data():
    sub = f"/subscriptions/{SUB}"
    sql = server("Microsoft.Sql/servers", "orders")
    pg = server("Microsoft.DBforPostgreSQL/flexibleServers", "reporting")
    closed = server("Microsoft.DBforMySQL/flexibleServers", "private", "Disabled")
    return {
        ("graph", "/v1.0/reports/authenticationMethods/userRegistrationDetails"): [
            {
                "id": uid(1),
                "userPrincipalName": "alice@example.org",
                "userType": "member",
                "isAdmin": True,
                "isMfaCapable": True,
                "isMfaRegistered": True,
            },
            {
                "id": uid(2),
                "userPrincipalName": "bob@example.org",
                "userType": "member",
                "isAdmin": False,
                "isMfaCapable": False,
                "isMfaRegistered": True,
            },
            {
                "id": uid(3),
                "userPrincipalName": "carol_partner.example#EXT#@example.org",
                "userType": "guest",
                "isAdmin": False,
                "isMfaCapable": False,
            },
            {
                "id": uid(5),
                "userPrincipalName": "break-glass@example.org",
                "userType": "member",
                "isAdmin": True,
                "isMfaCapable": False,
            },
        ],
        ("graph", "/v1.0/users"): users(),
        ("graph", "/v1.0/policies/identitySecurityDefaultsEnforcementPolicy"): {
            "isEnabled": False
        },
        ("graph", "/v1.0/identity/conditionalAccess/policies"): [
            {
                "state": "enabled",
                "conditions": {
                    "users": {
                        "includeUsers": ["All"],
                        "excludeUsers": [uid(5)],
                        "excludeGroups": ["g1"],
                    },
                    "applications": {"includeApplications": ["All"]},
                },
                "grantControls": {"operator": "OR", "builtInControls": ["mfa"]},
            },
            {
                "state": "enabled",
                "conditions": {
                    "users": {
                        "includeUsers": ["All"],
                        "excludeUsers": [uid(5), uid(2)],
                    },
                    "applications": {"includeApplications": ["All"]},
                },
                "grantControls": {
                    "operator": "AND",
                    "builtInControls": ["compliantDevice"],
                    "authenticationStrength": {"requirementsSatisfied": "mfa"},
                },
            },
        ],
        ("graph", "/v1.0/roleManagement/directory/roleAssignments"): [
            {
                "principalId": uid(1),
                "roleDefinitionId": GA,
                "directoryScopeId": "/",
                "principal": {
                    "@odata.type": "#microsoft.graph.user",
                    "userPrincipalName": "alice@example.org",
                    "mail": "alice@example.org",
                },
            },
            {
                # A second privileged role for the same person.
                "principalId": uid(1),
                "roleDefinitionId": "e8611ab8-c189-46e8-94e1-60213ab1f814",
                "directoryScopeId": "/",
                "principal": {"@odata.type": "#microsoft.graph.user"},
            },
            {
                "principalId": uid(9),
                "roleDefinitionId": GA,
                "directoryScopeId": "/",
                "principal": {
                    "@odata.type": "#microsoft.graph.servicePrincipal",
                    "displayName": "Deploy pipeline",
                },
            },
            {
                "principalId": uid(8),
                "roleDefinitionId": GA,
                "directoryScopeId": "/",
                "principal": {"@odata.type": "#microsoft.graph.group"},
            },
            {
                # Scoped to an administrative unit.
                "principalId": uid(2),
                "roleDefinitionId": GA,
                "directoryScopeId": "/administrativeUnits/x",
            },
            {
                # Global Reader is outside the count.
                "principalId": uid(2),
                "roleDefinitionId": "f2ef992c-3afb-46b9-b7cf-a126ee74c451",
                "directoryScopeId": "/",
            },
            {"principalId": "", "roleDefinitionId": GA, "directoryScopeId": "/"},
        ],
        ("graph", "/v1.0/applications"): [
            {
                "id": uid(20),
                "displayName": "Payroll",
                "passwordCredentials": [
                    {
                        "startDateTime": ago(400),
                        "endDateTime": stamp(NOW + timedelta(days=20)),
                    },
                    {"startDateTime": ago(900), "endDateTime": ago(10)},
                ],
                "keyCredentials": [{"startDateTime": ago(30), "endDateTime": None}],
            },
            {"id": uid(21), "displayName": "Unused", "passwordCredentials": []},
            {
                "id": uid(22),
                "appId": "app-22",
                "keyCredentials": [{"startDateTime": "x"}, "junk"],
            },
        ],
        ("graph", f"/v1.0/users/{uid(1)}"): {
            "id": uid(1),
            "userPrincipalName": "alice@example.org",
            "mail": "alice@example.org",
        },
        ("arm", sub): {"subscriptionId": SUB, "tenantId": TENANT.upper()},
        ("arm", sub + "/providers/Microsoft.Authorization/roleAssignments"): [
            {
                "properties": {
                    "roleDefinitionId": f"{sub}/providers/Microsoft.Authorization/"
                    f"roleDefinitions/{OWNER}",
                    "principalId": uid(1),
                    "principalType": "User",
                    "scope": sub,
                }
            },
            {
                "properties": {
                    "roleDefinitionId": f"/providers/Microsoft.Authorization/"
                    f"roleDefinitions/{OWNER.upper()}",
                    "principalId": uid(9),
                    "principalType": "ServicePrincipal",
                    "scope": "/providers/Microsoft.Management/managementGroups/root",
                }
            },
            {
                "properties": {
                    "roleDefinitionId": "x/18d7d88d-d35e-4fb5-a5c3-7773c20a72d9",
                    "principalId": uid(8),
                    "principalType": "Group",
                    "scope": "/",
                }
            },
            {
                # Reader is outside the count, and so are resource groups.
                "properties": {
                    "roleDefinitionId": f"x/{READER}",
                    "principalId": uid(2),
                    "principalType": "User",
                    "scope": sub,
                }
            },
            {
                "properties": {
                    "roleDefinitionId": f"x/{OWNER}",
                    "principalId": uid(2),
                    "principalType": "User",
                    "scope": sub + "/resourceGroups/rg",
                }
            },
            {
                "properties": {
                    "roleDefinitionId": f"x/{OWNER}",
                    "principalId": "not-a-guid",
                    "scope": sub,
                }
            },
            {
                # A second role for the same owner.
                "properties": {
                    "roleDefinitionId": "x/18d7d88d-d35e-4fb5-a5c3-7773c20a72d9",
                    "principalId": uid(1),
                    "principalType": "User",
                    "scope": sub,
                }
            },
        ],
        ("arm", sub + "/providers/Microsoft.Insights/diagnosticSettings"): {
            "value": [
                {
                    "properties": {
                        "workspaceId": "ws",
                        "logs": [{"categoryGroup": "allLogs", "enabled": True}],
                    }
                },
                # No destination, or administrative logs switched off.
                {
                    "properties": {
                        "logs": [{"category": "Administrative", "enabled": True}]
                    }
                },
                {
                    "properties": {
                        "storageAccountId": "sa",
                        "logs": [{"category": "Administrative", "enabled": False}],
                    }
                },
                "junk",
            ]
        },
        ("arm", sub + "/providers/Microsoft.Storage/storageAccounts"): [
            {"name": "a", "properties": {"allowBlobPublicAccess": True}},
            {"name": "b", "properties": {"allowBlobPublicAccess": False}},
            {"name": "c", "properties": {}},
        ],
        ("arm", sub + "/providers/Microsoft.Network/networkSecurityGroups"): [
            {
                "name": "nsg-jump",
                "properties": {
                    "securityRules": [
                        rule("Deny", "Internet", "22", 100),
                        rule("Allow", "*", "*", 200),
                        "junk",
                    ],
                    "networkInterfaces": [{"id": "n1"}, {"id": "n2"}],
                },
            },
            {
                "name": "nsg-web",
                "properties": {
                    "securityRules": [rule("Allow", "Internet", "443", 100)],
                    "subnets": [{"id": "s1"}],
                },
            },
            {
                # Exposes SSH but protects nothing.
                "name": "nsg-unused",
                "properties": {"securityRules": [rule("Allow", "*", "22", 100)]},
            },
        ],
        ("arm", sub + "/providers/Microsoft.Sql/servers"): [sql],
        ("arm", sub + "/providers/Microsoft.DBforPostgreSQL/flexibleServers"): [pg],
        ("arm", sub + "/providers/Microsoft.DBforMySQL/flexibleServers"): [closed],
        ("arm", sql["id"] + "/firewallRules"): [
            {"properties": {"startIpAddress": "0.0.0.0", "endIpAddress": "0.0.0.0"}}
        ],
        ("arm", pg["id"] + "/firewallRules"): [
            {
                "properties": {
                    "startIpAddress": "0.0.0.0",
                    "endIpAddress": "255.255.255.255",
                }
            },
            {"name": "junk"},
        ],
        ("arm", sub + "/providers/Microsoft.Security/secureScores/ascScore"): {
            "properties": {"score": {"current": 3.1, "max": 5, "percentage": 0.6234}}
        },
        ("arm", "/providers/Microsoft.ResourceGraph/resources"): {
            "data": [
                {"severity": "High", "findings": 3},
                {"severity": "Medium", "findings": 4},
                {"severity": "Odd", "findings": 9},
                {"severity": "Low", "findings": "x"},
                "junk",
            ]
        },
        ("graph", "/v1.0/auditLogs/signIns"): [
            {
                "id": "s1",
                "createdDateTime": stamp(NOW - timedelta(hours=2)),
                "userId": uid(1),
                "appDisplayName": "Microsoft 365",
                "ipAddress": "192.0.2.10",
                "status": {"errorCode": 0},
                "authenticationRequirement": "multiFactorAuthentication",
            },
            {
                "id": "s2",
                "createdDateTime": stamp(NOW - timedelta(hours=3)),
                "userId": uid(2),
                "status": {"errorCode": 50126},
                "authenticationRequirement": "singleFactorAuthentication",
            },
            {"id": "s3", "createdDateTime": stamp(NOW - timedelta(days=3))},
            {"id": "", "createdDateTime": "x"},
        ],
        ("graph", "/v1.0/auditLogs/directoryAudits"): [
            {
                "id": "a1",
                "activityDateTime": stamp(NOW - timedelta(hours=2)),
                "activityDisplayName": "Add member to role",
                "category": "RoleManagement",
                "result": "success",
                "loggedByService": "Core Directory",
                "initiatedBy": {"user": {"id": uid(1)}},
                "targetResources": [{"type": "User"}],
            },
            {
                "id": "a2",
                "activityDateTime": stamp(NOW - timedelta(hours=2)),
                "activityDisplayName": "Reset user password",
                "category": "UserManagement",
                "result": "failure",
                "initiatedBy": {"app": {"servicePrincipalId": uid(9)}},
            },
            {
                "id": "a3",
                "activityDateTime": stamp(NOW - timedelta(hours=1)),
                "activityDisplayName": "Update conditional access policy",
                "category": "Policy",
                "result": "success",
            },
            {
                "id": "a4",
                "activityDateTime": stamp(NOW - timedelta(hours=1)),
                "activityDisplayName": "Remove member from role",
                "category": "RoleManagement",
                "result": "timeout",
            },
            {
                "id": "a5",
                "activityDateTime": stamp(NOW - timedelta(hours=1)),
                "activityDisplayName": "Self-service password reset flow activity",
                "category": "Other",
                "result": "unknownFutureValue",
            },
            {
                "id": "a6",
                "activityDateTime": stamp(NOW - timedelta(hours=1)),
                "activityDisplayName": "Sign-in activity report",
                "category": "Other",
                "result": "success",
            },
        ],
        ("arm", sub + "/providers/Microsoft.Insights/eventtypes/management/values"): [
            activity("e1", "Microsoft.Authorization/roleAssignments/write"),
            activity("e2", "Microsoft.Authorization/roleAssignments/delete", "Failed"),
            activity("e3", "Microsoft.Storage/storageAccounts/listKeys/action"),
            activity("e4", "Microsoft.Compute/virtualMachines/write"),
            activity("e5", "Microsoft.Compute/virtualMachines/start/action"),
            activity(
                "e6",
                "Microsoft.Security/locations/alerts/activate/action",
                category="Security",
            ),
            activity("e7", "Microsoft.Compute/virtualMachines/write", "Started"),
            activity(
                "e8",
                "Microsoft.Compute/virtualMachines/write",
                category="ServiceHealth",
            ),
        ],
    }


def activity(ident, operation, status="Succeeded", category="Administrative"):
    return {
        "eventDataId": ident,
        "eventTimestamp": stamp(NOW - timedelta(hours=2)),
        "operationName": {"value": operation},
        "status": {"value": status},
        "category": {"value": category},
        "caller": "alice@example.org",
        "httpRequest": {"clientIpAddress": "192.0.2.77"},
    }


class Api:
    """Fake Microsoft endpoints behind httpx's mock transport."""

    def __init__(self, data=None, *, page_size=2, status=None, token_status=200):
        self.data = default_data() if data is None else data
        self.page_size = page_size
        self.status = status or {}
        self.token_status = token_status
        self.requests = []
        self.tokens = []

    def handler(self, request: httpx.Request):
        self.requests.append(request)
        host = request.url.host
        if host == "login.microsoftonline.com":
            form = parse_qs(request.content.decode())
            self.tokens.append(form)
            if self.token_status != 200:
                return httpx.Response(
                    self.token_status, json={"error": "invalid_client"}
                )
            return httpx.Response(200, json={"access_token": "tok-" + form["scope"][0]})
        resource = {"graph.microsoft.com": "graph", "management.azure.com": "arm"}.get(
            host
        )
        if resource is None:
            return httpx.Response(200, json={"value": [], "stolen": True})
        path = request.url.path
        if (resource, path) in self.status:
            answer = self.status[(resource, path)]
            if isinstance(answer, list):
                answer = answer.pop(0) if len(answer) > 1 else answer[0]
            if answer is not None:
                code, body = answer
                return httpx.Response(code, json=body, headers={"Retry-After": "0"})
        value = self.data.get((resource, path))
        if value is None:
            return httpx.Response(
                404, json={"error": {"code": "NotFound", "message": "secret detail"}}
            )
        if isinstance(value, dict):
            return httpx.Response(200, json=value)
        if "Guest" in str(request.url.params.get("$filter", "")):
            value = [v for v in value if v.get("userType") == "Guest"]
        start = int(request.url.params.get("$skiptoken", 0))
        page = value[start : start + self.page_size]
        body = {"value": page}
        if start + self.page_size < len(value):
            link = "@odata.nextLink" if resource == "graph" else "nextLink"
            body[link] = str(
                request.url.copy_set_param("$skiptoken", str(start + self.page_size))
            )
        return httpx.Response(200, json=body)

    def azure(self):
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return az.Azure(TENANT, CLIENT, SECRET, client=client)


def by_key(checks):
    return {c.key: c for c in checks}


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setattr(settings, "azure_tenant_id", TENANT)
    monkeypatch.setattr(settings, "azure_client_id", CLIENT)
    monkeypatch.setattr(settings, "azure_client_secret", SECRET)
    monkeypatch.setattr(settings, "azure_subscriptions", SUB)
    monkeypatch.setattr(settings, "demo_mode", False)


def use(monkeypatch, api):
    monkeypatch.setattr(az, "connect", api.azure)


# --- connection ---------------------------------------------------------------


def test_settings_must_name_a_tenant_client_and_secret(monkeypatch, credentials):
    for name, value in (
        ("azure_tenant_id", "example.org"),
        ("azure_client_id", ""),
        ("azure_client_secret", ""),
        ("azure_client_secret", "x" * 1025),
    ):
        monkeypatch.setattr(settings, name, value)
        with pytest.raises(Unavailable) as caught:
            az.connect()
        assert caught.value.reason == "not_configured"
        monkeypatch.setattr(settings, "azure_tenant_id", TENANT)
        monkeypatch.setattr(settings, "azure_client_id", CLIENT)
        monkeypatch.setattr(settings, "azure_client_secret", SECRET)


def test_tokens_are_requested_per_resource_and_refusals_are_reasons():
    api = Api()
    session = api.azure()
    assert [t["scope"] for t in api.tokens] == [
        ["https://graph.microsoft.com/.default"],
        ["https://management.azure.com/.default"],
    ]
    assert api.tokens[0]["client_secret"] == [SECRET]
    assert (
        str(api.requests[0].url)
        == f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
    )
    session.close()
    for status, reason in (
        (401, "not_configured"),
        (400, "not_configured"),
        (500, "unavailable"),
    ):
        with pytest.raises(Unavailable) as caught:
            Api(token_status=status).azure()
        assert caught.value.reason == reason


def test_paging_links_to_another_host_never_receive_the_token():
    data = default_data()
    api = Api(data)

    def evil(request):
        return httpx.Response(
            200,
            json={
                "value": [{"id": "1"}],
                "@odata.nextLink": "https://evil.example/next",
            },
        )

    session = api.azure()
    session.apis["graph"].http._transport = httpx.MockTransport(
        lambda r: evil(r) if r.url.host == "graph.microsoft.com" else api.handler(r)
    )
    with pytest.raises(Unavailable):
        session.pages("graph", "/v1.0/users")
    assert not any(r.url.host == "evil.example" for r in api.requests)


def test_a_throttled_request_is_retried_once(monkeypatch):
    path = "/v1.0/policies/identitySecurityDefaultsEnforcementPolicy"
    api = Api(status={("graph", path): [(429, {}), None]})
    session = api.azure()
    assert session.get("graph", path) == {"isEnabled": False}
    assert sum(1 for r in api.requests if r.url.path == path) == 2
    api = Api(status={("graph", path): [(429, {})]})
    with pytest.raises(Unavailable):
        api.azure().get("graph", path)


@pytest.mark.parametrize(
    "status, body, reason",
    [
        (
            403,
            {
                "error": {
                    "code": "Authentication_RequestFromNonPremiumTenantOrB2CTenant"
                }
            },
            "not_licensed",
        ),
        (403, {"error": {"message": "This needs a Premium licence."}}, "not_licensed"),
        (403, {"error": {"code": "Authorization_RequestDenied"}}, "permission_denied"),
        (401, {}, "not_configured"),
        (404, {}, "not_configured"),
        (503, "not json", "unavailable"),
    ],
)
def test_refusals_become_reasons_without_provider_text(status, body, reason):
    response = httpx.Response(status, json=body)
    assert az.explain(response) == reason


# --- the tenant ---------------------------------------------------------------


def test_tenant_checks_and_identities(monkeypatch, credentials):
    result = az.tenant_result(Api().azure())
    assert (result.key, result.kind) == (TENANT, "tenant")
    found = by_key(result.checks)
    assert found["identity.mfa"].detail == "MFA off for 2 of 3 users"
    assert sorted(found["identity.mfa"].subjects) == [
        "bob@example.org",
        "break-glass@example.org",
    ]
    assert found["identity.admin_mfa"].detail == "MFA off for 1 of 2 administrators"
    enforced = found["identity.mfa_enforced"]
    assert enforced.status == "attention"
    assert enforced.detail == (
        "MFA is required by 2 Conditional Access policies; 1 user excluded from all of them"
    )
    assert enforced.subjects == ["break-glass@example.org"]
    admins = found["identity.admins"]
    assert admins.status == "info"
    assert admins.detail == "3 users, groups or apps hold privileged Entra roles"
    assert sorted(admins.subjects) == sorted(
        ["alice@example.org", "Deploy pipeline", uid(8)]
    )
    assert (
        found["identity.guests"].detail == "1 guest, 0 yet to accept their invitation"
    )
    inactive = found["identity.inactive"]
    assert inactive.detail == "1 of 4 users unused for 45 days or more"
    assert inactive.subjects == ["bob@example.org"]
    keys = found["credentials.key_age"]
    assert keys.status == "attention" and keys.subjects == ["Payroll"]
    assert keys.detail == (
        "1 of 3 live secrets and certificates older than 90 days; 1 expire within 30 days"
    )
    people = {p.name: p for p in result.identities}
    assert people["alice@example.org"].admin and people["alice@example.org"].mfa is True
    assert people["alice@example.org"].email == "alice@example.org"
    assert people["carol_partner.example#EXT#@example.org"].kind == "guest"
    assert (
        people["carol_partner.example#EXT#@example.org"].email
        == "carol@partner.example"
    )
    assert people["bob@example.org"].last_used is not None


def test_security_defaults_and_licences(monkeypatch, credentials):
    data = default_data()
    data[("graph", "/v1.0/policies/identitySecurityDefaultsEnforcementPolicy")] = {
        "isEnabled": True
    }
    found = by_key(az.tenant_result(Api(data).azure()).checks)
    assert found["identity.mfa_enforced"].status == "met"
    assert found["identity.mfa_enforced"].detail == "Security defaults are on"
    premium = (
        403,
        {"error": {"code": "Authentication_RequestFromNonPremiumTenantOrB2CTenant"}},
    )
    api = Api(
        status={
            ("graph", "/v1.0/identity/conditionalAccess/policies"): premium,
            ("graph", "/v1.0/reports/authenticationMethods/userRegistrationDetails"): (
                403,
                {"error": {"code": "Authorization_RequestDenied"}},
            ),
        }
    )
    found = by_key(az.tenant_result(api.azure()).checks)
    assert found["identity.mfa_enforced"].status == "attention"
    assert found["identity.mfa"].status == "unknown"
    assert found["identity.mfa"].reason == "permission_denied"
    assert found["identity.admins"].status == "info"


@pytest.mark.parametrize(
    "change, expected",
    [
        ({}, True),
        ({"state": "enabledForReportingButNotEnforced"}, False),
        ({"conditions": {"users": {"includeUsers": ["u1"]}}}, False),
        (
            {
                "grantControls": {
                    "operator": "OR",
                    "builtInControls": ["mfa", "compliantDevice"],
                }
            },
            False,
        ),
        (
            {
                "grantControls": {
                    "operator": "AND",
                    "builtInControls": ["mfa", "compliantDevice"],
                }
            },
            True,
        ),
        ({"grantControls": {"operator": "OR", "builtInControls": ["block"]}}, False),
        ({"conditions": {"locations": {"includeLocations": ["trusted"]}}}, False),
        ({"conditions": {"clientAppTypes": ["browser"]}}, False),
        (
            {
                "conditions": {
                    "applications": {
                        "includeApplications": ["All"],
                        "excludeApplications": ["a"],
                    }
                }
            },
            False,
        ),
    ],
)
def test_conditional_access_must_require_mfa_of_everyone(change, expected):
    policy = {
        "state": "enabled",
        "conditions": {
            "users": {"includeUsers": ["All"]},
            "applications": {"includeApplications": ["All"]},
        },
        "grantControls": {"operator": "OR", "builtInControls": ["mfa"]},
    }
    for key, value in change.items():
        if key == "conditions":
            policy["conditions"] = {**policy["conditions"], **value}
        else:
            policy[key] = value
    assert az.requires_mfa(policy) is expected


# --- subscriptions ---------------------------------------------------------------


def test_subscription_checks(credentials):
    result = az.subscription_result(Api().azure(), SUB, "Production")
    assert result.kind == "subscription"
    found = by_key(result.checks)
    owners = found["identity.admins"]
    assert owners.label == "Owners"
    assert owners.detail == "3 principals hold Owner or User Access Administrator"
    assert sorted(owners.subjects) == sorted(["alice@example.org", uid(9), uid(8)])
    assert (
        found["logging.audit_trail"].detail
        == "The activity log is exported to 1 destination"
    )
    storage = found["storage.public_access"]
    assert storage.detail == "1 of 3 storage accounts allow public blob access"
    assert storage.subjects == ["a"]
    ports = found["network.admin_ports"]
    assert (
        ports.detail
        == "1 of 2 network security groups in use admit SSH or RDP from anywhere"
    )
    assert ports.subjects == ["nsg-jump"]
    databases = found["network.databases"]
    assert databases.detail == "1 of 3 database servers admit every address"
    assert databases.subjects == ["reporting"]
    defender = found["findings"]
    assert defender.status == "attention"
    assert (
        defender.detail
        == "3 high, 4 medium and 0 low recommendations; secure score 62%"
    )


def test_network_security_groups_apply_rules_in_priority_order():
    group = {
        "properties": {
            "securityRules": [
                rule("Allow", "Internet", "20-25", 300),
                rule("Deny", "*", "22", 200),
                rule("Allow", "10.0.0.0/8", "3389", 100),
                rule("Allow", "0.0.0.0/0", "3389", 400, protocol="Udp"),
                rule("Allow", "any", "*", 500),
            ]
        }
    }
    assert az.exposed_ports(group) == [3389]


def test_subscriptions_must_belong_to_the_tenant(monkeypatch, credentials):
    data = default_data()
    data[("arm", f"/subscriptions/{OTHER}")] = {
        "subscriptionId": OTHER,
        "tenantId": uid(77),
    }
    api = Api(data).azure()
    monkeypatch.setattr(settings, "azure_subscriptions", f"{SUB}, {OTHER},{uid(5)}")
    assert az.subscriptions(api) == [
        (SUB, SUB, ""),
        (OTHER, OTHER, "not_configured"),
        (uid(5), uid(5), "not_configured"),
    ]
    data[("arm", "/subscriptions")] = [
        {"subscriptionId": SUB, "tenantId": TENANT, "displayName": "Production"},
        {"subscriptionId": OTHER, "tenantId": uid(77), "displayName": "Theirs"},
        {"subscriptionId": "junk"},
    ]
    monkeypatch.setattr(settings, "azure_subscriptions", "")
    assert az.subscriptions(Api(data).azure()) == [(SUB, "Production", "")]
    for bad in ("not-a-guid", f"{SUB},{SUB}"):
        monkeypatch.setattr(settings, "azure_subscriptions", bad)
        with pytest.raises(Unavailable):
            az.subscriptions(api)


def test_collect_reads_the_tenant_and_each_subscription(monkeypatch, credentials):
    data = default_data()
    data[("arm", f"/subscriptions/{OTHER}")] = {
        "subscriptionId": OTHER,
        "tenantId": uid(77),
    }
    monkeypatch.setattr(settings, "azure_subscriptions", f"{SUB},{OTHER}")
    api = Api(data)
    use(monkeypatch, api)
    tenant, sub, other = az.collect()
    assert [tenant.kind, sub.kind, other.kind] == [
        "tenant",
        "subscription",
        "subscription",
    ]
    assert {c.status for c in other.checks} == {"unknown"}
    assert {c.key for c in other.checks} == set(az.SUBSCRIPTION_CHECKS)


def test_server_listings_are_bounded(monkeypatch, credentials):
    monkeypatch.setattr(az, "MAX_SERVERS", 1)
    found = by_key(az.subscription_result(Api().azure(), SUB, "Production").checks)
    assert found["network.databases"].status == "unknown"
    monkeypatch.setattr(az, "MAX_PAGES", 1)
    with pytest.raises(Unavailable):
        Api().azure().pages("graph", "/v1.0/users")


# --- activity -------------------------------------------------------------------


def test_activity_reads_sign_ins_audits_and_the_activity_log(monkeypatch, credentials):
    api = Api(page_size=10)
    use(monkeypatch, api)
    now = NOW.replace(tzinfo=None)
    entries, complete = az.activity(now - timedelta(hours=6), now)
    assert complete is True
    actions = sorted(e.action for e in entries)
    assert actions.count("SignIn") == 2
    sign_in = next(e for e in entries if e.id == "signin:s1")
    assert sign_in.outcome == "success" and sign_in.detail["mfa_used"] is True
    failed = next(e for e in entries if e.id == "signin:s2")
    assert failed.outcome == "failure" and failed.detail["error_code"] == "50126"
    audit = next(e for e in entries if e.id == "audit:a1")
    assert audit.action == "Add member to role" and audit.scope == TENANT
    assert next(e for e in entries if e.id == "audit:a4").outcome == "failure"
    log = [e for e in entries if e.id.startswith("activity:")]
    assert sorted(e.id for e in log) == [f"activity:e{n}" for n in range(1, 7)]
    assert all(e.scope == SUB for e in log)
    assert [e.at for e in entries] == sorted(e.at for e in entries)
    filters = [
        r.url.params.get("$filter")
        for r in api.requests
        if r.url.path.endswith("/signIns")
    ]
    assert filters[0].startswith("createdDateTime ge ")


def test_activity_reports_truncation(monkeypatch, credentials):
    monkeypatch.setattr(az, "MAX_ACTIVITY", 3)
    use(monkeypatch, Api())
    now = NOW.replace(tzinfo=None)
    entries, complete = az.activity(now - timedelta(hours=6), now)
    assert complete is False and len(entries) == 3


def test_connect_hands_the_settings_to_one_session(monkeypatch, credentials):
    monkeypatch.setattr(az, "Azure", lambda *args: args)
    assert az.connect() == (TENANT, CLIENT, SECRET)


def test_unreadable_errors_and_tokens_are_unavailable():
    assert az._error(httpx.Response(403, content=b"<html>")) == ("", "")
    assert az._error(httpx.Response(403, json={"error": "denied"})) == ("", "")
    assert az._when("2026-01-02T03:04:05") == datetime(2026, 1, 2, 3, 4, 5)
    assert az.audit_entry({"id": "a1"}, TENANT) is None

    def refuse(request):
        raise httpx.ConnectError("secret", request=request)

    answers = (
        refuse,
        lambda request: httpx.Response(200, content=b"not json"),
        lambda request: httpx.Response(200, json={"access_token": ""}),
        lambda request: httpx.Response(200, json=["token"]),
    )
    for handler in answers:
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(Unavailable) as caught:
            az.Azure(TENANT, CLIENT, SECRET, client=client)
        assert caught.value.reason == "unavailable"


def test_paging_links_must_be_text():
    api = Api()
    session = api.azure()
    session.apis["graph"].http._transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"value": [], "@odata.nextLink": {"href": "x"}}
        )
    )
    with pytest.raises(Unavailable):
        session.pages("graph", "/v1.0/users")


@pytest.mark.parametrize(
    "platforms",
    [{"includePlatforms": ["android"]}, {"excludePlatforms": ["linux"]}],
)
def test_conditional_access_for_some_platforms_is_not_everyone(platforms):
    policy = {
        "state": "enabled",
        "conditions": {
            "users": {"includeUsers": ["All"]},
            "applications": {"includeApplications": ["All"]},
            "platforms": platforms,
        },
        "grantControls": {"operator": "OR", "builtInControls": ["mfa"]},
    }
    assert az.requires_mfa(policy) is False
    policy["conditions"]["platforms"] = {"includePlatforms": ["All"]}
    assert az.requires_mfa(policy) is True


def test_tenant_listings_are_bounded_and_refusals_are_reasons(monkeypatch, credentials):
    monkeypatch.setattr(az, "MAX_IDENTITIES", 1)
    found = by_key(az.tenant_result(Api().azure()).checks)
    for key in ("identity.mfa", "identity.inactive", "credentials.key_age"):
        assert (found[key].status, found[key].reason) == ("unknown", "unavailable")
    monkeypatch.setattr(az, "MAX_IDENTITIES", 5000)
    denied = (403, {"error": {"code": "Authorization_RequestDenied"}})
    api = Api(status={("graph", "/v1.0/identity/conditionalAccess/policies"): denied})
    found = by_key(az.tenant_result(api.azure()).checks)
    assert found["identity.mfa_enforced"].reason == "permission_denied"


def test_group_exclusions_and_credential_ages(credentials):
    data = default_data()
    policies = data[("graph", "/v1.0/identity/conditionalAccess/policies")]
    policies[1]["conditions"]["users"].update(excludeUsers=[], excludeRoles=["r1"])
    data[("graph", "/v1.0/applications")] = []
    found = by_key(az.tenant_result(Api(data).azure()).checks)
    assert (
        found["identity.mfa_enforced"].status,
        found["identity.mfa_enforced"].detail,
    ) == (
        "met",
        "MFA is required by 2 Conditional Access policies; 1 group or role excluded",
    )
    assert found["credentials.key_age"].detail == "No live secrets or certificates"
    data[("graph", "/v1.0/applications")] = [
        {
            "displayName": "Billing",
            "passwordCredentials": [
                {"startDateTime": ago(200), "endDateTime": ago(-400)}
            ],
        }
    ]
    keys = by_key(az.tenant_result(Api(data).azure()).checks)["credentials.key_age"]
    assert keys.detail == "1 of 1 live secret or certificate older than 90 days"


def test_subscription_edge_cases(monkeypatch, credentials):
    sub = f"/subscriptions/{SUB}"
    data = default_data()
    data[("arm", sub + "/providers/Microsoft.Insights/diagnosticSettings")] = {
        "value": []
    }
    data[("arm", sub + "/providers/Microsoft.Security/secureScores/ascScore")] = {
        "properties": {"score": {"percentage": "high"}}
    }
    data[("arm", sub + "/providers/Microsoft.Sql/servers")] = [
        server("Microsoft.Sql/servers", "theirs") | {"id": "/subscriptions/x/y"}
    ]
    denied = (403, {"error": {"code": "Authorization_RequestDenied"}})
    api = Api(data, status={("graph", f"/v1.0/users/{uid(1)}"): denied})
    found = by_key(az.subscription_result(api.azure(), SUB, "Production").checks)
    assert uid(1) in found["identity.admins"].subjects
    assert found["logging.audit_trail"].status == "attention"
    assert found["findings"].detail == "3 high, 4 medium and 0 low recommendations"
    assert found["network.databases"].status == "unknown"


def test_rules_with_unreadable_ports_or_priorities_are_ignored():
    group = {
        "properties": {
            "securityRules": [
                rule("Allow", "*", "ssh", 100),
                rule("Allow", "*", "22", "first"),
                rule("Allow", "*", "3389", 300),
            ]
        }
    }
    assert az.exposed_ports(group) == [3389]


def test_activity_skips_subscriptions_outside_the_tenant(monkeypatch, credentials):
    data = default_data()
    data[("arm", f"/subscriptions/{OTHER}")] = {
        "subscriptionId": OTHER,
        "tenantId": uid(77),
    }
    monkeypatch.setattr(settings, "azure_subscriptions", f"{SUB},{OTHER}")
    api = Api(data, page_size=10)
    use(monkeypatch, api)
    now = NOW.replace(tzinfo=None)
    entries, _ = az.activity(now - timedelta(hours=6), now)
    assert {e.scope for e in entries if e.id.startswith("activity:")} == {SUB}
    assert not any(
        OTHER in r.url.path and "eventtypes" in r.url.path for r in api.requests
    )


def test_help_documents_permissions_checks_and_settings():
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[2] / "ui/public/help/administrator-azure.html"
    )
    text = page.read_text()
    for permission in (*az.PERMISSIONS, *az.AZURE_ROLES):
        assert f"<code>{permission}</code>" in text, permission
    for key, label in az.CHECKS.items():
        assert label in text and f"posture.{key}" in text, key
    for name in (
        "KEEN_AZURE_ENABLED",
        "AZURE_POSTURE_TENANT_ID",
        "AZURE_POSTURE_CLIENT_ID",
        "AZURE_POSTURE_CLIENT_SECRET",
        "KEEN_AZURE_SUBSCRIPTIONS",
    ):
        assert f"<code>{name}</code>" in text, name
