"""Scaleway posture and Audit Trail activity against a fake Scaleway API
behind httpx's mock transport. No network is used."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from app.core.config import settings
from app.ingest.cloud import scaleway as scw
from app.ingest.cloud.base import Unavailable

SECRET = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
ORG = "6a7b8c9d-0e1f-4a2b-9c3d-4e5f6a7b8c9d"
NOW = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)


def uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def stamp(when):
    return when.isoformat() + ".123456789Z"


def ago(days=0, hours=0):
    return stamp(NOW - timedelta(days=days, hours=hours))


OWNER, MEMBER, IDLE, PENDING, APP, GROUP = (
    uid(1),
    uid(2),
    uid(3),
    uid(4),
    uid(5),
    uid(6),
)
P_ADMIN, P_GROUP, P_APP, P_READ = uid(11), uid(12), uid(13), uid(14)
SG_OPEN, SG_SAFE, SG_IDLE = uid(21), uid(22), uid(23)
DB_OPEN, DB_PRIVATE = uid(31), uid(32)
K8S_OLD, K8S_NEW = uid(41), uid(42)


def default_data():
    return {
        f"/iam/v1alpha1/organizations/{ORG}": {
            "id": ORG,
            "name": "Example Ltd",
            "alias": "example-ltd",
            "login_password_enabled": True,
            "login_saml_enabled": False,
            "login_magic_code_enabled": True,
        },
        "/iam/v1alpha1/users": [
            {
                "id": OWNER,
                "email": "alex@example.org",
                "type": "owner",
                "mfa": True,
                "status": "activated",
                "last_login_at": ago(1),
                "created_at": ago(900),
            },
            {
                "id": MEMBER,
                "email": "sam@example.org",
                "type": "member",
                "mfa": False,
                "status": "activated",
                "last_login_at": ago(2),
                "created_at": ago(300),
            },
            {
                "id": IDLE,
                "email": "idle@example.org",
                "type": "member",
                "mfa": True,
                "status": "activated",
                "last_login_at": ago(200),
                "created_at": ago(400),
            },
            {
                "id": PENDING,
                "email": "new@example.org",
                "type": "member",
                "mfa": False,
                "status": "invitation_pending",
                "created_at": ago(100),
            },
        ],
        "/iam/v1alpha1/applications": [
            {"id": APP, "name": "terraform"},
        ],
        "/iam/v1alpha1/groups": [
            {"id": GROUP, "name": "platform", "user_ids": [IDLE], "all_users": False},
        ],
        "/iam/v1alpha1/policies": [
            {"id": P_ADMIN, "user_id": MEMBER},
            {"id": P_GROUP, "group_id": GROUP},
            {"id": P_APP, "application_id": APP},
            {"id": P_READ, "user_id": PENDING},
        ],
        ("/iam/v1alpha1/rules", P_ADMIN): [
            {"permission_set_names": ["IAMManager", "InstancesFullAccess"]}
        ],
        ("/iam/v1alpha1/rules", P_GROUP): [
            {"permission_set_names": ["OrganizationManager"]}
        ],
        ("/iam/v1alpha1/rules", P_APP): [{"permission_set_names": ["IAMManager"]}],
        ("/iam/v1alpha1/rules", P_READ): [
            {"permission_set_names": ["InstancesReadOnly"]}
        ],
        "/iam/v1alpha1/api-keys": [
            {
                "access_key": "SCWAAAAAAAAAAAAAAAAA",
                "user_id": MEMBER,
                "created_at": ago(10),
            },
            {
                "access_key": "SCWBBBBBBBBBBBBBBBBB",
                "application_id": APP,
                "created_at": ago(200),
            },
            {
                "access_key": "SCWCCCCCCCCCCCCCCCCC",
                "application_id": uid(99),
                "created_at": ago(500),
                "managed": True,
            },
        ],
        "/instance/v1/zones/fr-par-1/security_groups": [
            {
                "id": SG_OPEN,
                "name": "legacy",
                "inbound_default_policy": "drop",
                "servers": [{"id": "s1", "name": "bastion"}],
            },
            {
                "id": SG_SAFE,
                "name": "web",
                "inbound_default_policy": "accept",
                "servers": [{"id": "s2", "name": "web-1"}],
            },
            {
                "id": SG_IDLE,
                "name": "unused",
                "inbound_default_policy": "accept",
                "servers": [],
            },
        ],
        f"/instance/v1/zones/fr-par-1/security_groups/{SG_OPEN}/rules": [
            {
                "direction": "inbound",
                "protocol": "TCP",
                "action": "accept",
                "ip_range": "0.0.0.0/0",
                "dest_port_from": 22,
                "dest_port_to": None,
                "position": 1,
            }
        ],
        f"/instance/v1/zones/fr-par-1/security_groups/{SG_SAFE}/rules": [
            {
                "direction": "inbound",
                "protocol": "TCP",
                "action": "accept",
                "ip_range": "0.0.0.0/0",
                "dest_port_from": 443,
                "position": 3,
            },
            {
                "direction": "inbound",
                "protocol": "ANY",
                "action": "drop",
                "ip_range": "0.0.0.0/0",
                "position": 2,
            },
            {
                "direction": "inbound",
                "protocol": "TCP",
                "action": "accept",
                "ip_range": "0.0.0.0/0",
                "dest_port_from": 22,
                "position": 4,
            },
        ],
        "/rdb/v1/regions/fr-par/instances": [
            {
                "id": DB_OPEN,
                "name": "analytics",
                "endpoints": [{"ip": "51.15.0.1", "port": 5432, "load_balancer": {}}],
                "backup_schedule": {"disabled": False, "frequency": 24},
            },
            {
                "id": DB_PRIVATE,
                "name": "orders",
                "endpoints": [{"ip": "10.0.0.2", "private_network": {"id": "pn"}}],
                "backup_schedule": {"disabled": True},
            },
        ],
        f"/rdb/v1/regions/fr-par/instances/{DB_OPEN}/acls": [
            {"ip": "0.0.0.0/0", "action": "allow", "direction": "inbound"}
        ],
        "/k8s/v1/regions/fr-par/clusters": [
            {
                "id": K8S_NEW,
                "name": "apps",
                "auto_upgrade": {"enabled": True},
                "acl_available": True,
            },
            {
                "id": K8S_OLD,
                "name": "old",
                "auto_upgrade": {"enabled": False},
                "acl_available": False,
            },
        ],
        f"/k8s/v1/regions/fr-par/clusters/{K8S_NEW}/acls": [{"ip": "203.0.113.0/24"}],
        "/audit-trail/v1alpha1/regions/fr-par/events": [
            {
                "id": uid(51),
                "recorded_at": ago(hours=2),
                "principal": {"id": MEMBER},
                "method_name": "CreateAPIKey",
                "product_name": "iam",
                "service_name": "scaleway.iam.v1alpha1.Api",
                "status_code": 200,
                "source_ip": "203.0.113.7",
                "request_body": {"secret": "never stored"},
                "resources": [{"id": "SCWX", "type": "iam_api_key"}],
            },
            {
                "id": uid(52),
                "recorded_at": ago(hours=1),
                "principal": {"id": APP},
                "method_name": "DeleteServer",
                "product_name": "instance",
                "status_code": 403,
            },
        ],
        "/audit-trail/v1alpha1/regions/fr-par/authentication-events": [
            {
                "id": uid(61),
                "recorded_at": ago(hours=3),
                "result": "failure",
                "method": "password",
                "failure_reason": "invalid_mfa",
                "source_ip": "198.51.100.4",
                "resources": [
                    {
                        "id": MEMBER,
                        "type": "account_user",
                        "account_user_info": {
                            "email": "sam@example.org",
                            "phone_number": "+33100000000",
                        },
                    }
                ],
            },
            {"id": uid(62), "recorded_at": ago(days=3), "result": "success"},
        ],
    }


class Api:
    def __init__(self, data=None, *, page_size=100, status=None):
        self.data = default_data() if data is None else data
        self.page_size = page_size
        self.status = status or {}
        self.requests = []

    def handler(self, request: httpx.Request):
        self.requests.append(request)
        if request.url.host != "api.scaleway.com":
            return httpx.Response(200, json={"users": [], "stolen": True})
        path, params = request.url.path, request.url.params
        if path in self.status:
            return httpx.Response(self.status[path], json={"message": "secret detail"})
        if path.startswith("/iam/") and path != "/iam/v1alpha1/rules":
            if path.endswith("/organizations/" + ORG):
                pass
            elif params.get("organization_id") != ORG:
                return httpx.Response(400, json={"message": "wrong organisation"})
        value = self.data.get((path, params.get("policy_id")), self.data.get(path))
        if value is None:
            return httpx.Response(404, json={"message": "not found"})
        if isinstance(value, dict):
            return httpx.Response(200, json=value)
        key = path.rsplit("/", 1)[-1].replace("-", "_")
        key = {"acls": "rules"}.get(key, key)
        if path.startswith("/audit-trail/"):
            # Newest first, paged by token.
            value = sorted(value, key=lambda e: e["recorded_at"], reverse=True)
            start = int(params.get("page_token") or 0)
            rows = value[start : start + self.page_size]
            body = {"events": rows}
            if start + self.page_size < len(value):
                body["next_page_token"] = str(start + self.page_size)
            return httpx.Response(200, json=body)
        size = min(
            self.page_size, int(params.get("page_size") or params.get("per_page"))
        )
        page = int(params.get("page", 1))
        rows = value[(page - 1) * size : page * size]
        body = {key: rows}
        if not path.startswith("/instance/"):
            body["total_count"] = len(value)
        return httpx.Response(200, json=body)

    def scaleway(self, zones=("fr-par-1",)):
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return scw.Scaleway(SECRET, ORG, list(zones), client=client)


def by_key(checks):
    return {c.key: c for c in checks}


@pytest.fixture
def use(monkeypatch):
    monkeypatch.setattr(settings, "scaleway_secret_key", SECRET)
    monkeypatch.setattr(settings, "scaleway_organization_id", ORG)
    monkeypatch.setattr(settings, "scaleway_zones", "")
    monkeypatch.setattr(settings, "demo_mode", False)

    def install(api):
        monkeypatch.setattr(scw, "connect", api.scaleway)
        return api

    return install


def test_settings_must_name_a_key_an_organisation_and_valid_zones(use, monkeypatch):
    api = scw.connect()
    try:
        assert api.origin == "https://api.scaleway.com"
        assert api._headers["X-Auth-Token"] == SECRET
        assert api.regions == ["fr-par", "nl-ams", "pl-waw"]
    finally:
        api.close()
    monkeypatch.setattr(settings, "scaleway_zones", "nl-ams-1 fr-par-2")
    with scw.connect() as api:
        assert api.regions == ["fr-par", "nl-ams"]
    many = ",".join(f"x{c}-abc-{n}" for c in "abc" for n in range(1, 8))
    for name, value in (
        ("scaleway_secret_key", "SCWXXXXXXXXXXXXXXXXX"),
        ("scaleway_organization_id", "example"),
        ("scaleway_zones", "fr-par-1,fr-par-1"),
        ("scaleway_zones", "fr-par-1/../x"),
        ("scaleway_zones", many),
    ):
        monkeypatch.setattr(settings, "scaleway_secret_key", SECRET)
        monkeypatch.setattr(settings, "scaleway_organization_id", ORG)
        monkeypatch.setattr(settings, "scaleway_zones", "")
        monkeypatch.setattr(settings, name, value)
        with pytest.raises(Unavailable) as caught:
            scw.connect()
        assert caught.value.reason == "not_configured"


def test_collect_reads_the_organisation(use):
    api = use(Api())
    [scope] = scw.collect()
    assert scope.key == ORG and scope.label == "example-ltd"
    assert scope.kind == "organisation"
    checks = by_key(scope.checks)
    assert set(checks) == set(scw.CHECKS)
    # The pending invitation is left out of MFA and unused-member counts.
    assert checks["identity.mfa"].subjects == ["sam@example.org"]
    assert checks["identity.mfa"].detail == "MFA off for 1 of 3 members"
    assert checks["identity.admin_mfa"].subjects == ["sam@example.org"]
    assert sorted(checks["identity.admins"].subjects) == [
        "alex@example.org",
        "application/terraform",
        "group/platform",
        "idle@example.org",
        "sam@example.org",
    ]
    assert checks["identity.inactive"].subjects == ["idle@example.org"]
    # Managed keys are left out; the old key belongs to an application.
    assert checks["credentials.key_age"].subjects == ["application/terraform"]
    assert "of 2 live keys" in checks["credentials.key_age"].detail
    # The web group's earlier drop rule wins; the unused group is skipped.
    assert checks["network.admin_ports"].subjects == ["fr-par-1/legacy"]
    assert checks["network.admin_ports"].detail.startswith("1 of 2 security groups")
    assert checks["network.databases"].subjects == ["fr-par/analytics"]
    assert checks["resilience.backups"].subjects == ["fr-par/orders"]
    assert checks["kubernetes.control_plane"].subjects == [
        "fr-par/old: no automatic upgrades, API open to every address"
    ]
    people = {p.name: p for p in scope.identities}
    assert people["alex@example.org"].detail == "Owner"
    assert people["sam@example.org"].detail == "IAM manager"
    assert people["new@example.org"].detail == "Invitation pending"
    assert people["idle@example.org"].last_used is not None
    assert all(r.url.host == "api.scaleway.com" for r in api.requests)
    assert all(r.headers["X-Auth-Token"] == SECRET for r in api.requests)


def test_saml_only_sign_in_leaves_mfa_to_the_identity_provider(use):
    data = default_data()
    data[f"/iam/v1alpha1/organizations/{ORG}"].update(
        login_password_enabled=False,
        login_magic_code_enabled=False,
        login_saml_enabled=True,
    )
    use(Api(data))
    [scope] = scw.collect()
    checks = by_key(scope.checks)
    for key in ("identity.mfa", "identity.admin_mfa"):
        assert checks[key].status == "info"
        assert "identity provider" in checks[key].detail


def test_a_refused_read_marks_only_its_checks(use):
    use(
        Api(
            status={
                "/iam/v1alpha1/policies": 403,
                f"/iam/v1alpha1/organizations/{ORG}": 403,
            }
        )
    )
    [scope] = scw.collect()
    assert scope.label == ORG
    checks = by_key(scope.checks)
    for key in scw.IDENTITY_CHECKS:
        assert checks[key].status == "unknown"
        assert checks[key].reason == "permission_denied"
    assert checks["credentials.key_age"].status == "attention"
    assert checks["network.databases"].status == "attention"


def test_security_group_rules_apply_by_position():
    group = {"inbound_default_policy": "accept"}
    assert scw.exposed_ports(group, []) == [22, 3389]
    assert scw.exposed_ports({"inbound_default_policy": "drop"}, []) == []

    def rule(action, ip_range, low=None, high=None, position=1, **extra):
        return {
            "direction": "inbound",
            "protocol": "TCP",
            "action": action,
            "ip_range": ip_range,
            "dest_port_from": low,
            "dest_port_to": high,
            "position": position,
            **extra,
        }

    rules = [
        rule("accept", "::/0", 3389, position=2),
        rule("drop", "0.0.0.0/0", 20, 25, position=1),
        rule("accept", "0.0.0.0/0", position=0, direction="outbound", protocol="ANY"),
    ]
    assert scw.exposed_ports({"inbound_default_policy": "accept"}, rules) == [3389]
    narrow = [rule("accept", "203.0.113.0/24", 22)]
    assert scw.exposed_ports({"inbound_default_policy": "drop"}, narrow) == []


def test_listings_are_paged_and_bounded(use, monkeypatch):
    api = use(Api(page_size=1))
    monkeypatch.setattr(scw, "PAGE_SIZE", 1)
    [scope] = scw.collect()
    assert by_key(scope.checks)["identity.mfa"].subjects == ["sam@example.org"]
    pages = [
        int(r.url.params["page"])
        for r in api.requests
        if r.url.path == "/iam/v1alpha1/users"
    ]
    assert max(pages) == 4
    monkeypatch.setattr(scw, "MAX_PAGES", 2)
    [scope] = scw.collect()
    assert by_key(scope.checks)["identity.mfa"].status == "unknown"
    monkeypatch.setattr(scw, "MAX_PAGES", 50)
    monkeypatch.setattr(scw, "MAX_POLICIES", 3)
    [scope] = scw.collect()
    assert by_key(scope.checks)["identity.admins"].status == "unknown"


def test_activity_reads_api_and_authentication_events(use):
    api = use(Api(page_size=1))
    entries, complete = scw.activity(NOW - timedelta(hours=6), NOW)
    assert complete
    assert [(e.action, e.actor, e.outcome) for e in entries] == [
        ("SignIn", "sam@example.org", "failure"),
        ("CreateAPIKey", "sam@example.org", "success"),
        ("DeleteServer", "application/terraform", "failure"),
    ]
    sign_in = entries[0]
    assert sign_in.detail["failure_reason"] == "invalid_mfa"
    assert "phone_number" not in str(sign_in.raw)
    assert "request_body" not in entries[1].raw
    # Every listing names the organisation and the window, in both regions.
    audit = [r for r in api.requests if r.url.path.startswith("/audit-trail/")]
    assert {r.url.path.split("/")[4] for r in audit} == {"fr-par", "nl-ams"}
    for request in audit:
        assert request.url.params["organization_id"] == ORG
        assert request.url.params["recorded_before"] == NOW.isoformat() + "Z"


def test_activity_reports_truncation(use, monkeypatch):
    monkeypatch.setattr(scw, "MAX_ACTIVITY", 1)
    use(Api(page_size=1))
    entries, complete = scw.activity(NOW - timedelta(hours=6), NOW)
    assert complete is False and len(entries) == 1
    assert entries[0].action == "DeleteServer"


def test_times_identifiers_and_addresses_are_read_strictly():
    assert scw._when("yesterday") is None
    assert scw._when("2026-01-02T03:04:05") == datetime(2026, 1, 2, 3, 4, 5)
    with pytest.raises(Unavailable):
        scw._uuid("../iam")
    assert scw.everywhere("not-a-range") is False
    udp = {
        "direction": "inbound",
        "protocol": "UDP",
        "action": "accept",
        "ip_range": "0.0.0.0/0",
        "dest_port_from": 22,
        "position": 1,
    }
    assert scw.exposed_ports({"inbound_default_policy": "drop"}, [udp]) == []
    assert scw.event_entry({"id": uid(1), "recorded_at": ago(1)}, ORG, {}) is None
    assert scw.sign_in_entry({"recorded_at": ago(1)}, ORG) is None


def test_admin_policies_for_unknown_groups_and_bounded_listings(use, monkeypatch):
    data = default_data()
    data["/iam/v1alpha1/policies"].append({"id": uid(98), "group_id": uid(97)})
    data[("/iam/v1alpha1/rules", uid(98))] = [
        {"permission_set_names": ["OrganizationManager"]}
    ]
    data["/iam/v1alpha1/api-keys"] = []
    data["/rdb/v1/regions/fr-par/instances"].append(
        {
            "id": uid(96),
            "name": "reports",
            "endpoints": [{"ip": "51.15.0.2"}],
            "backup_schedule": {"disabled": False},
        }
    )
    data[f"/rdb/v1/regions/fr-par/instances/{uid(96)}/acls"] = [
        {"ip": "203.0.113.0/24"}
    ]
    use(Api(data))
    [scope] = scw.collect()
    checks = by_key(scope.checks)
    assert checks["identity.admins"].status == "info"
    assert checks["credentials.key_age"].detail == "No live API keys"
    assert checks["network.databases"].subjects == ["fr-par/analytics"]
    for name in ("MAX_IDENTITIES", "MAX_GROUPS", "MAX_DATABASES", "MAX_CLUSTERS"):
        monkeypatch.setattr(scw, name, 0)
    use(Api())
    [scope] = scw.collect()
    checks = by_key(scope.checks)
    for key in (
        "identity.mfa",
        "network.admin_ports",
        "network.databases",
        "kubernetes.control_plane",
    ):
        assert checks[key].reason == "unavailable", key


def test_activity_without_names_keeps_principal_ids(use):
    use(Api(status={"/iam/v1alpha1/users": 403}))
    entries, _ = scw.activity(NOW - timedelta(hours=6), NOW)
    deleted = next(e for e in entries if e.action == "DeleteServer")
    assert APP in deleted.actor


def test_help_documents_permission_sets_checks_and_settings():
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[2]
        / "ui/public/help/administrator-scaleway.html"
    )
    text = page.read_text()
    for name in scw.PERMISSION_SETS:
        assert f"<code>{name}</code>" in text, name
    for key, label in scw.CHECKS.items():
        assert label in text and f"posture.{key}" in text, key
    for name in (
        "KEEN_SCALEWAY_ENABLED",
        "KEEN_SCALEWAY_ORGANIZATION_ID",
        "SCALEWAY_SECRET_KEY",
        "KEEN_SCALEWAY_ZONES",
    ):
        assert f"<code>{name}</code>" in text, name
