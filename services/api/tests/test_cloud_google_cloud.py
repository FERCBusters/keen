"""Google Cloud posture and Admin Activity logs against in-memory Google API
answers. No network is used."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from app.core.config import settings
from app.ingest.cloud import google_cloud as gc
from app.ingest.cloud.base import Unavailable

from tests.google_fakes import Service, http_error

ORG = "123456789012"
NOW = datetime.now(timezone.utc).replace(microsecond=0)
KEY = {"token_uri": "https://oauth2.googleapis.com/token", "client_email": "keen@x"}


def stamp(when):
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


def ago(days):
    return stamp(NOW - timedelta(days=days))


def asset(name, data):
    return {"name": name, "resource": {"data": data}}


def default_assets():
    return {
        gc.KEY_ASSET: [
            asset(
                "//iam/projects/p1/serviceAccounts/deploy@p1.iam.gserviceaccount.com/keys/a",
                {
                    "name": "projects/p1/serviceAccounts/deploy@p1.iam.gserviceaccount.com/keys/a",
                    "keyType": "USER_MANAGED",
                    "validAfterTime": ago(400),
                    "validBeforeTime": "9999-12-31T23:59:59Z",
                },
            ),
            asset(
                "//iam/projects/p1/serviceAccounts/deploy@p1.iam.gserviceaccount.com/keys/b",
                {
                    "keyType": "USER_MANAGED",
                    "validAfterTime": ago(20),
                    "validBeforeTime": stamp(NOW + timedelta(days=30)),
                },
            ),
            asset(
                "//iam/projects/p2/serviceAccounts/old@p2.iam.gserviceaccount.com/keys/c",
                {
                    "name": "projects/p2/serviceAccounts/old@p2.iam.gserviceaccount.com/keys/c",
                    "keyType": "USER_MANAGED",
                    "validAfterTime": ago(900),
                    "validBeforeTime": ago(5),
                },
            ),
            asset("//iam/x/keys/d", {"keyType": "SYSTEM_MANAGED"}),
            asset("//iam/x/keys/e", {"keyType": "USER_MANAGED", "disabled": True}),
            asset("//iam/no-account", {"keyType": "USER_MANAGED", "name": "odd"}),
            asset(
                "//iam/projects/p3/serviceAccounts/new@p3.iam.gserviceaccount.com/keys/f",
                {"keyType": "USER_MANAGED", "validAfterTime": "not a date"},
            ),
        ],
        gc.FIREWALL_ASSET: [
            firewall("default", "allow-ssh", 65534, ["22"]),
            firewall(
                "default",
                "deny-ssh-tagged",
                100,
                ["22"],
                denied=True,
                targetTags=["bastion"],
            ),
            firewall("prod", "deny-rdp", 100, ["3389"], denied=True),
            firewall("prod", "allow-rdp", 100, ["3389"]),
            firewall("prod", "allow-all-tcp", 200, None, protocol="tcp"),
            firewall("prod", "egress", 1, None, direction="EGRESS"),
            firewall("lab", "office-only", 1000, ["22"], source="198.51.100.0/24"),
            firewall("lab", "disabled", 1000, ["22"], disabled=True),
        ],
        gc.SQL_ASSET: [
            asset(
                "//cloudsql/instances/open",
                {
                    "name": "open",
                    "settings": {
                        "ipConfiguration": {
                            "ipv4Enabled": True,
                            "authorizedNetworks": [{"value": "0.0.0.0/0"}],
                        }
                    },
                },
            ),
            asset(
                "//cloudsql/instances/private",
                {
                    "name": "private",
                    "settings": {
                        "ipConfiguration": {
                            "ipv4Enabled": False,
                            "authorizedNetworks": [{"value": "0.0.0.0/0"}],
                        }
                    },
                },
            ),
            asset(
                "//cloudsql/instances/office", {"name": "office", "settings": "junk"}
            ),
        ],
    }


def firewall(
    network,
    name,
    priority,
    ports,
    *,
    denied=False,
    source="0.0.0.0/0",
    protocol="tcp",
    **extra,
):
    entry = [{"IPProtocol": protocol, **({"ports": ports} if ports else {})}]
    data = {
        "name": name,
        "network": f"https://www.googleapis.com/compute/v1/projects/p/global/networks/{network}",
        "priority": priority,
        "sourceRanges": [source],
        ("denied" if denied else "allowed"): entry,
        **extra,
    }
    return asset(f"//compute/firewalls/{name}", data)


class FakeCloud:
    """Stands in for app.ingest.cloud.google_cloud.Api."""

    methods = {
        "list",
        "searchAllIamPolicies",
        "getEffectivePolicy",
        "getIamPolicy",
        "group",
    }

    def __init__(self, *, page=2, fail=None, parent=f"organizations/{ORG}"):
        self.parent = parent
        self.organisation = parent.startswith("organizations/")
        self.page = page
        self.fail = fail or {}
        self.calls = []
        self.made = []
        self.services = []
        self.assets = default_assets()
        self.policies = {
            "roles/owner": [
                {
                    "policy": {
                        "bindings": [
                            {
                                "role": "roles/owner",
                                "members": [
                                    "user:alice@example.org",
                                    "group:admins@example.org",
                                    "serviceAccount:tf@p1.iam.gserviceaccount.com",
                                    "deleted:user:gone@example.org?uid=1",
                                    "allUsers",
                                ],
                            },
                            {
                                "role": "roles/viewer",
                                "members": ["user:viewer@example.org"],
                            },
                            {
                                "role": "roles/owner",
                                "members": ["user:temp@example.org"],
                                "condition": {"expression": "request.time < x"},
                            },
                        ]
                    }
                },
                {"policy": "junk"},
            ],
            "roles/resourcemanager.organizationAdmin": [
                {
                    "policy": {
                        "bindings": [
                            {
                                "role": "roles/resourcemanager.organizationAdmin",
                                "members": [
                                    "user:alice@example.org",
                                    "domain:example.org",
                                ],
                            }
                        ]
                    }
                }
            ],
        }
        self.effective = {
            "storage.publicAccessPrevention": {"spec": {"rules": [{"enforce": True}]}},
            "iam.disableServiceAccountKeyCreation": {
                "spec": {"rules": [{"enforce": False}]}
            },
            "iam.managed.disableServiceAccountKeyCreation": {
                "spec": {"rules": [{"enforce": True, "condition": {"expression": "x"}}]}
            },
            "iam.allowedPolicyMemberDomains": {
                "spec": {"rules": [{"values": {"allowedValues": ["C01", "C02", 7]}}]}
            },
        }
        self.audit = {
            "auditConfigs": [
                {
                    "service": "allServices",
                    "auditLogConfigs": [
                        {"logType": "DATA_READ"},
                        {"logType": "DATA_WRITE"},
                    ],
                },
                "junk",
            ]
        }
        self.groups = [
            {"properties": {"severity": "CRITICAL"}, "count": "2"},
            {"properties": {"severity": "HIGH"}, "count": "5"},
            {"properties": {"severity": "LOW"}, "count": "x"},
            {"properties": "junk", "count": "1"},
        ]
        self.entries = []

    def client(self, api, version, scopes):
        self.made.append((api, version, tuple(scopes)))
        return Service(self)

    def _paged(self, items, kwargs, key):
        start = int(
            kwargs.get("pageToken") or (kwargs.get("body") or {}).get("pageToken") or 0
        )
        out = {key: items[start : start + self.page]}
        if start + self.page < len(items):
            out["nextPageToken"] = str(start + self.page)
        return out

    def answer(self, name, kwargs):
        if name == "assets.list":
            return self._paged(self.assets[kwargs["assetTypes"][0]], kwargs, "assets")
        if name == "v1.searchAllIamPolicies":
            role = kwargs["query"].removeprefix("policy:")
            return self._paged(self.policies.get(role, []), kwargs, "results")
        if name.endswith("policies.getEffectivePolicy"):
            constraint = kwargs["name"].rsplit("/", 1)[-1]
            if constraint not in self.effective:
                raise http_error(404)
            return self.effective[constraint]
        if name.endswith("getIamPolicy"):
            return self.audit
        if name.endswith("sources.findings.group"):
            return self._paged(self.groups, kwargs, "groupByResults")
        if name == "entries.list":
            return self._paged(self.entries, kwargs, "entries")
        raise AssertionError(name)


def by_key(checks):
    return {c.key: c for c in checks}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "google_cloud_organization", ORG)
    monkeypatch.setattr(settings, "google_cloud_project", "")
    monkeypatch.setattr(settings, "google_cloud_projects", "")
    monkeypatch.setattr(gc.google, "key_info", lambda *a: dict(KEY))


def test_the_parent_is_an_organisation_or_a_project(monkeypatch, configured):
    assert gc.connect().parent == f"organizations/{ORG}"
    monkeypatch.setattr(settings, "google_cloud_organization", "")
    monkeypatch.setattr(settings, "google_cloud_project", "solo-project-1")
    api = gc.connect()
    assert (api.parent, api.organisation) == ("projects/solo-project-1", False)
    for organisation, project in (
        ("organizations/1", ""),
        ("", "Bad_Project"),
        ("", ""),
    ):
        monkeypatch.setattr(settings, "google_cloud_organization", organisation)
        monkeypatch.setattr(settings, "google_cloud_project", project)
        with pytest.raises(Unavailable) as caught:
            gc.connect()
        assert caught.value.reason == "not_configured"


def test_collect_reads_each_check(monkeypatch, configured):
    fake = FakeCloud()
    monkeypatch.setattr(gc, "connect", lambda: fake)
    (result,) = gc.collect()
    assert (result.key, result.kind) == (ORG, "organisation")
    found = by_key(result.checks)
    admins = found["identity.admins"]
    assert admins.status == "info"
    assert admins.detail == (
        "4 users, groups or service accounts hold Owner or Organization Administrator"
    )
    assert sorted(admins.subjects) == [
        "admins@example.org",
        "alice@example.org",
        "example.org",
        "tf@p1.iam.gserviceaccount.com",
    ]
    people = {p.name: p for p in result.identities}
    assert people["alice@example.org"].detail == "Owner, Organization Administrator"
    assert people["alice@example.org"].email == "alice@example.org"
    assert people["tf@p1.iam.gserviceaccount.com"].kind == "service"
    assert people["example.org"].kind == "group"
    keys = found["credentials.key_age"]
    assert (
        keys.detail == "1 service account with keys older than 90 days, of 3 live keys"
    )
    assert keys.subjects == ["deploy@p1.iam.gserviceaccount.com"]
    assert found["storage.public_access"].status == "met"
    assert (
        found["google_cloud.key_creation"].detail
        == "Service account keys can be created"
    )
    assert found["google_cloud.domain_sharing"].detail == (
        "IAM members are restricted to 2 allowed customers or domains"
    )
    assert found["logging.audit_trail"].status == "met"
    ports = found["network.admin_ports"]
    assert ports.detail == "2 of 3 VPC networks let SSH or RDP in from anywhere"
    assert ports.subjects == ["default", "prod"]
    databases = found["network.databases"]
    assert databases.detail == "1 of 3 Cloud SQL instances authorise every address"
    assert databases.subjects == ["open"]
    assert found["findings"].detail == "2 critical and 5 high findings"
    # Each client asks only for its own scopes.
    assert ("orgpolicy", "v2", (gc.ORG_POLICY,)) in fake.made
    assert ("cloudresourcemanager", "v3", (gc.READ_ONLY,)) in fake.made


def test_projects_skip_organisation_roles_and_failures_are_reasons(
    monkeypatch, configured
):
    fake = FakeCloud(parent="projects/solo-project-1")
    fake.fail["v1.searchAllIamPolicies"] = http_error(403)
    fake.audit = {"auditConfigs": []}
    monkeypatch.setattr(gc, "connect", lambda: fake)
    (result,) = gc.collect()
    found = by_key(result.checks)
    assert result.kind == "project"
    assert found["identity.admins"].status == "unknown"
    assert found["identity.admins"].reason == "permission_denied"
    assert found["logging.audit_trail"].status == "attention"
    assert not any(
        kwargs.get("query") == "policy:roles/resourcemanager.organizationAdmin"
        for _, kwargs in fake.calls
    )


@pytest.mark.parametrize(
    "rules, expected",
    [
        (
            [
                {
                    "priority": 100,
                    "sourceRanges": ["0.0.0.0/0"],
                    "allowed": [{"IPProtocol": "tcp"}],
                }
            ],
            [22, 3389],
        ),
        (
            [
                {
                    "priority": 100,
                    "sourceRanges": ["::/0"],
                    "allowed": [{"IPProtocol": "all"}],
                }
            ],
            [22, 3389],
        ),
        (
            [
                {
                    "priority": 100,
                    "sourceRanges": ["10.0.0.0/8"],
                    "allowed": [{"IPProtocol": "tcp"}],
                }
            ],
            [],
        ),
        (
            [
                {
                    "priority": 10,
                    "sourceRanges": ["0.0.0.0/0"],
                    "denied": [{"IPProtocol": "tcp", "ports": ["22"]}],
                },
                {
                    "priority": 20,
                    "sourceRanges": ["0.0.0.0/0"],
                    "allowed": [{"IPProtocol": "tcp", "ports": ["20-30"]}],
                },
            ],
            [],
        ),
        (
            [
                {
                    "priority": 100,
                    "sourceRanges": ["0.0.0.0/0"],
                    "allowed": [{"IPProtocol": "udp"}],
                }
            ],
            [],
        ),
    ],
)
def test_vpc_firewall_rules_apply_in_priority_order(rules, expected):
    assert gc.exposed_ports(rules) == expected


def test_listings_are_bounded(monkeypatch, configured):
    monkeypatch.setattr(gc, "MAX_PAGES", 1)
    fake = FakeCloud()
    monkeypatch.setattr(gc, "connect", lambda: fake)
    (result,) = gc.collect()
    assert by_key(result.checks)["credentials.key_age"].status == "unknown"


def test_admin_activity_reads_iam_and_policy_changes(monkeypatch, configured):
    fake = FakeCloud()
    now = NOW.replace(tzinfo=None)
    fake.entries = [
        {
            "insertId": "a1",
            "timestamp": stamp(NOW - timedelta(hours=1)),
            "protoPayload": {
                "methodName": "SetIamPolicy",
                "serviceName": "cloudresourcemanager.googleapis.com",
                "authenticationInfo": {"principalEmail": "alice@example.org"},
                "resourceName": "organizations/123456789012",
            },
            "resource": {"type": "organization"},
        },
        {
            "insertId": "a2",
            "timestamp": stamp(NOW - timedelta(hours=2)),
            "protoPayload": {
                "methodName": "google.iam.admin.v1.CreateServiceAccountKey",
                "status": {"code": 7},
            },
        },
        {
            "insertId": "a3",
            "timestamp": "nonsense",
            "protoPayload": {"methodName": "x"},
        },
        {
            "insertId": "a4",
            "timestamp": stamp(NOW - timedelta(days=3)),
            "protoPayload": {"methodName": "SetIamPolicy"},
        },
    ]
    monkeypatch.setattr(gc, "connect", lambda: fake)
    monkeypatch.setattr(settings, "google_cloud_projects", "extra-project-1")
    entries, complete = gc.activity(now - timedelta(hours=6), now)
    assert complete is True
    assert [e.action for e in entries] == ["CreateServiceAccountKey", "SetIamPolicy"]
    assert entries[0].outcome == "failure" and entries[1].actor == "alice@example.org"
    assert entries[1].detail["resource_type"] == "organization"
    body = next(k for n, k in fake.calls if n == "entries.list")["body"]
    assert body["resourceNames"] == [f"organizations/{ORG}", "projects/extra-project-1"]
    assert body["orderBy"] == "timestamp desc"
    assert 'protoPayload.methodName:"SetIamPolicy"' in body["filter"]
    monkeypatch.setattr(settings, "google_cloud_projects", "../x")
    with pytest.raises(Unavailable):
        gc.activity(now - timedelta(hours=6), now)


def test_clients_carry_the_key_and_unknown_members_are_left_out(monkeypatch):
    monkeypatch.setattr(gc.google, "client", lambda *args: args)
    api = gc.Api(dict(KEY), f"organizations/{ORG}")
    assert api.client("logging", "v2", [gc.READ_ONLY]) == (
        "logging",
        "v2",
        KEY,
        [gc.READ_ONLY],
    )
    assert gc._member("principalSet:x") is None
    assert gc._when("2026-01-02T03:04:05") == datetime(2026, 1, 2, 3, 4, 5)


def test_project_administrators_and_repeated_bindings(monkeypatch, configured):
    fake = FakeCloud(parent="projects/solo-project-1")
    bindings = fake.policies["roles/owner"][0]["policy"]["bindings"]
    bindings.append({"role": "roles/owner", "members": ["user:alice@example.org"]})
    fake.assets[gc.KEY_ASSET] = []
    fake.groups = []
    monkeypatch.setattr(gc, "connect", lambda: fake)
    (result,) = gc.collect()
    found = by_key(result.checks)
    people = {p.name: p for p in result.identities}
    assert people["alice@example.org"].detail == "Owner"
    assert found["credentials.key_age"].detail == "No user-managed service account keys"
    assert found["findings"].status == "met"
    monkeypatch.setattr(gc, "MAX_IDENTITIES", 1)
    (result,) = gc.collect()
    assert by_key(result.checks)["identity.admins"].reason == "unavailable"


class PolicyFailures(FakeCloud):
    def __init__(self, failures):
        super().__init__()
        self.failures = failures

    def answer(self, name, kwargs):
        constraint = kwargs.get("name", "").rsplit("/", 1)[-1]
        if constraint in self.failures:
            raise http_error(self.failures[constraint])
        return super().answer(name, kwargs)


def test_managed_constraints_an_organisation_lacks_count_as_unset(
    monkeypatch, configured
):
    fake = PolicyFailures({"iam.managed.allowedPolicyMembers": 400})
    monkeypatch.setattr(gc, "connect", lambda: fake)
    (result,) = gc.collect()
    assert by_key(result.checks)["google_cloud.domain_sharing"].status == "met"
    fake = PolicyFailures({"storage.publicAccessPrevention": 403})
    monkeypatch.setattr(gc, "connect", lambda: fake)
    (result,) = gc.collect()
    found = by_key(result.checks)
    assert found["storage.public_access"].reason == "permission_denied"


def test_list_constraints_and_unreadable_rules():
    rules = ["junk", {"condition": {"expression": "x"}}, {}, {"enforce": True}]
    assert gc._restricted({"rules": rules}) == (True, 0)
    assert gc._restricted({"rules": [{"allowAll": True}]}) == (False, 0)
    rule = {
        "priority": 1,
        "sourceRanges": ["not-a-range", "0.0.0.0/0"],
        "allowed": ["junk", {"IPProtocol": "tcp", "ports": ["ssh", "3389"]}],
    }
    assert gc.exposed_ports([rule]) == [3389]


def test_activity_beyond_the_ceiling_keeps_the_newest(monkeypatch, configured):
    fake = FakeCloud()
    now = NOW.replace(tzinfo=None)
    fake.entries = [
        {
            "insertId": f"e{n}",
            "timestamp": stamp(NOW - timedelta(hours=n)),
            "protoPayload": {"methodName": "SetIamPolicy"},
        }
        for n in (1, 2, 3)
    ]
    monkeypatch.setattr(gc, "connect", lambda: fake)
    monkeypatch.setattr(gc, "MAX_ACTIVITY", 1)
    entries, complete = gc.activity(now - timedelta(hours=6), now)
    assert len(entries) == 1 and complete is False


def test_help_documents_roles_checks_and_settings():
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[2]
        / "ui/public/help/administrator-google-cloud.html"
    )
    text = page.read_text()
    for role in gc.ROLES:
        assert f"<code>{role}</code>" in text, role
    for key, label in gc.CHECKS.items():
        assert label in text and f"posture.{key}" in text, key
    for name in (
        "KEEN_GOOGLE_CLOUD_ENABLED",
        "KEEN_GOOGLE_CLOUD_ORGANIZATION",
        "KEEN_GOOGLE_CLOUD_PROJECT",
        "KEEN_GOOGLE_CLOUD_PROJECTS",
        "GOOGLE_CLOUD_SA_JSON_B64",
    ):
        assert f"<code>{name}</code>" in text, name
