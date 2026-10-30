"""Akamai Cloud (Linode) posture and activity against a fake Linode API behind
httpx's mock transport. No network is used."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from app.core.config import settings
from app.ingest.cloud import linode as ln
from app.ingest.cloud.base import Unavailable

TOKEN = "synthetic0linode0token0value0000"
EUUID = "a1b2c3d4-e5f6-4789-9abc-def012345678"
NOW = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)


def ago(days=0, hours=0):
    return (NOW - timedelta(days=days, hours=hours)).isoformat()


def default_data():
    return {
        "/v4/account": {"euuid": EUUID, "company": "Example Ltd", "email": "x@e.org"},
        "/v4/account/settings": {"backups_enabled": True},
        "/v4/account/users": [
            {
                "username": "alex",
                "email": "alex@example.org",
                "restricted": False,
                "tfa_enabled": True,
                "last_login": {"login_datetime": ago(1), "status": "successful"},
            },
            {
                "username": "ci-deploy",
                "email": "not an email",
                "restricted": False,
                "tfa_enabled": False,
                "last_login": {"login_datetime": ago(100), "status": "successful"},
            },
            {
                "username": "sam",
                "email": "sam@example.org",
                "restricted": True,
                "tfa_enabled": True,
                "last_login": None,
                "password_created": ago(200),
            },
        ],
        "/v4/networking/firewalls": [
            {
                "id": 1,
                "label": "web",
                "status": "enabled",
                "rules": {
                    "inbound_policy": "DROP",
                    "inbound": [
                        {
                            "action": "ACCEPT",
                            "protocol": "TCP",
                            "ports": "80,443",
                            "addresses": {"ipv4": ["0.0.0.0/0"]},
                        },
                        {
                            "action": "ACCEPT",
                            "protocol": "TCP",
                            "ports": "22",
                            "addresses": {"ipv4": ["203.0.113.0/24"]},
                        },
                    ],
                },
                "entities": [{"type": "linode", "id": 10}],
            },
            {
                "id": 2,
                "label": "legacy",
                "status": "enabled",
                "rules": {
                    "inbound_policy": "DROP",
                    "inbound": [
                        {
                            "action": "ACCEPT",
                            "protocol": "TCP",
                            "ports": "20-25",
                            "addresses": {"ipv6": ["::/0"]},
                        }
                    ],
                },
                "entities": [
                    {
                        "type": "linode_interface",
                        "id": 99,
                        "parent_entity": {"type": "linode", "id": 11},
                    }
                ],
            },
            {
                "id": 3,
                "label": "disabled-open",
                "status": "disabled",
                "rules": {"inbound_policy": "ACCEPT", "inbound": []},
                "entities": [{"type": "linode", "id": 12}],
            },
        ],
        "/v4/linode/instances": [
            {"id": 10, "label": "web-1", "backups": {"enabled": True}},
            {"id": 11, "label": "bastion", "backups": {"enabled": True}},
            {"id": 12, "label": "build-runner", "backups": {"enabled": False}},
        ],
        "/v4/databases/instances": [
            {"id": 5, "label": "orders", "allow_list": ["203.0.113.4/32"]},
            {"id": 6, "label": "open-db", "allow_list": ["0.0.0.0/0"]},
        ],
        "/v4/lke/clusters": [
            {"id": 7, "label": "apps", "control_plane": {"high_availability": True}},
            {"id": 8, "label": "old", "control_plane": {"high_availability": False}},
        ],
        "/v4/lke/clusters/7/control_plane_acl": {
            "acl": {"enabled": True, "addresses": {"ipv4": ["203.0.113.0/24"]}}
        },
        "/v4/account/logins": [
            {
                "id": 1,
                "datetime": ago(hours=1),
                "username": "alex",
                "ip": "203.0.113.9",
                "status": "successful",
            },
            {
                "id": 2,
                "datetime": ago(days=30),
                "username": "alex",
                "ip": "203.0.113.9",
                "status": "successful",
            },
        ],
        "/v4/account/events": [
            {
                "id": 41,
                "created": ago(hours=2),
                "action": "firewall_update",
                "status": "notification",
                "username": "alex",
                "entity": {"type": "firewall", "label": "web"},
            },
            {
                "id": 42,
                "created": ago(hours=3),
                "action": "linode_boot",
                "status": "failed",
                "username": None,
                "entity": {"type": "linode", "label": "web-1"},
            },
        ],
    }


class Api:
    def __init__(self, data=None, *, page_size=500, status=None):
        self.data = default_data() if data is None else data
        self.page_size = page_size
        self.status = status or {}
        self.requests = []

    def handler(self, request: httpx.Request):
        self.requests.append(request)
        if request.url.host != "api.linode.com":
            return httpx.Response(200, json={"data": [], "stolen": True})
        path = request.url.path
        if path in self.status:
            return httpx.Response(self.status[path], json={"errors": [{"reason": "x"}]})
        value = self.data.get(path)
        if value is None:
            return httpx.Response(404, json={"errors": [{"reason": "Not found"}]})
        if isinstance(value, dict):
            return httpx.Response(200, json=value)
        page = int(request.url.params.get("page", 1))
        size = self.page_size
        rows = value[(page - 1) * size : page * size]
        pages = max(1, -(-len(value) // size))
        return httpx.Response(
            200,
            json={"data": rows, "page": page, "pages": pages, "results": len(value)},
        )

    def linode(self):
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return ln.Linode(ln.ORIGIN, {"Authorization": f"Bearer {TOKEN}"}, client=client)


def by_key(checks):
    return {c.key: c for c in checks}


@pytest.fixture
def use(monkeypatch):
    monkeypatch.setattr(settings, "linode_token", TOKEN)
    monkeypatch.setattr(settings, "demo_mode", False)

    def install(api):
        monkeypatch.setattr(ln, "connect", api.linode)
        return api

    return install


def test_a_token_is_required_and_sent_as_a_bearer(monkeypatch):
    for value in ("", "short", "has spaces in it 1234567890"):
        monkeypatch.setattr(settings, "linode_token", value)
        with pytest.raises(Unavailable) as caught:
            ln.connect()
        assert caught.value.reason == "not_configured"
    monkeypatch.setattr(settings, "linode_token", TOKEN)
    api = ln.connect()
    try:
        assert api.origin == "https://api.linode.com"
        assert api._headers["Authorization"] == f"Bearer {TOKEN}"
    finally:
        api.close()


def test_collect_reads_the_account(use):
    api = use(Api())
    [scope] = ln.collect()
    assert scope.key == EUUID.upper() and scope.label == "Example Ltd"
    checks = by_key(scope.checks)
    assert set(checks) == set(ln.CHECKS)
    assert checks["identity.mfa"].status == "attention"
    assert checks["identity.mfa"].subjects == ["ci-deploy"]
    assert checks["identity.admin_mfa"].subjects == ["ci-deploy"]
    assert checks["identity.admins"].status == "info"
    assert sorted(checks["identity.admins"].subjects) == ["alex", "ci-deploy"]
    # ci-deploy last signed in 100 days ago; sam has never signed in and
    # set a password 200 days ago.
    assert sorted(checks["identity.inactive"].subjects) == ["ci-deploy", "sam"]
    # The legacy firewall's IPv6 rule admits SSH from anywhere through an
    # interface; the disabled firewall protects nothing.
    assert checks["network.admin_ports"].subjects == ["legacy"]
    assert checks["network.firewall_coverage"].subjects == ["build-runner"]
    assert checks["network.databases"].subjects == ["open-db"]
    assert checks["resilience.backups"].subjects == ["build-runner"]
    assert "by default" in checks["resilience.backups"].detail
    # Cluster 8 has no access list endpoint, so its API is open.
    assert checks["kubernetes.control_plane"].subjects == [
        "old: no high availability, API open to every address"
    ]
    people = {p.name: p for p in scope.identities}
    assert people["ci-deploy"].email == "" and people["ci-deploy"].admin
    assert people["sam"].detail == "Restricted"
    assert all(r.url.host == "api.linode.com" for r in api.requests)


def test_a_refused_read_marks_only_its_checks(use):
    use(Api(status={"/v4/account/users": 403}))
    [scope] = ln.collect()
    checks = by_key(scope.checks)
    for key in ("identity.mfa", "identity.admin_mfa", "identity.admins"):
        assert checks[key].status == "unknown"
        assert checks[key].reason == "permission_denied"
    assert checks["network.databases"].status == "attention"


def test_firewall_rules_apply_in_order_against_the_inbound_policy():
    def wall(policy, *rules):
        return {"rules": {"inbound_policy": policy, "inbound": list(rules)}}

    anywhere = {"ipv4": ["0.0.0.0/0"]}
    assert ln.exposed_ports(wall("ACCEPT")) == [22, 3389]
    assert ln.exposed_ports(wall("DROP")) == []
    drop_ssh = {
        "action": "DROP",
        "protocol": "TCP",
        "ports": "22",
        "addresses": anywhere,
    }
    accept_all = {"action": "ACCEPT", "protocol": "TCP", "addresses": anywhere}
    assert ln.exposed_ports(wall("DROP", drop_ssh, accept_all)) == [3389]
    udp = {"action": "ACCEPT", "protocol": "UDP", "ports": "22", "addresses": anywhere}
    assert ln.exposed_ports(wall("DROP", udp)) == []


def test_listings_are_bounded(use, monkeypatch):
    monkeypatch.setattr(ln, "MAX_PAGES", 1)
    use(Api(page_size=1))
    [scope] = ln.collect()
    checks = by_key(scope.checks)
    assert checks["identity.mfa"].status == "unknown"
    assert checks["network.databases"].status == "unknown"


def test_activity_reads_sign_ins_and_events_in_the_window(use):
    api = use(Api())
    entries, complete = ln.activity(NOW - timedelta(hours=6), NOW)
    assert complete
    assert [(e.id, e.action, e.outcome) for e in entries] == [
        ("event:42", "linode_boot", "failure"),
        ("event:41", "firewall_update", "success"),
        ("login:1", "login", "success"),
    ]
    assert entries[2].actor == "alex"
    [events] = [r for r in api.requests if r.url.path == "/v4/account/events"]
    window = json.loads(events.headers["X-Filter"])
    assert window["+order"] == "desc"
    assert {"created": {"+lt": NOW.isoformat()}} in window["+and"]


def test_refusals_carry_reasons_without_the_token(use):
    use(Api(status={"/v4/account": 401}))
    with pytest.raises(Unavailable) as caught:
        ln.collect()
    assert caught.value.reason == "not_configured"
    assert TOKEN not in repr(caught.value)


def test_times_ports_addresses_and_entities_are_read_strictly():
    assert ln._when("") is None and ln._when("yesterday") is None
    assert ln._when("2026-01-02T13:04:05+10:00") == datetime(2026, 1, 2, 3, 4, 5)
    anywhere = {"ipv4": ["not-an-address", "0.0.0.0/0"]}

    def wall(*rules):
        return {"rules": {"inbound_policy": "DROP", "inbound": list(rules)}}

    def accept(ports, addresses=anywhere):
        return {
            "action": "ACCEPT",
            "protocol": "TCP",
            "ports": ports,
            "addresses": addresses,
        }

    assert ln.exposed_ports(wall(accept("22,,23"))) == [22]
    assert ln.exposed_ports(wall(accept("ssh"))) == []
    assert ln.exposed_ports(wall(accept("3389", "0.0.0.0/0"))) == []
    entities = [
        "junk",
        {"type": "linode_interface", "id": 9},
        {"type": "nodebalancer", "id": 3},
        {"type": "linode", "id": 4},
    ]
    assert ln._protected({"entities": entities}) == {"4"}
    assert ln.login_entry({"id": "1", "datetime": ago(1)}, EUUID) is None
    assert ln.event_entry({"id": 1, "created": ago(1)}, EUUID) is None


def test_account_listings_are_bounded_and_identified(use, monkeypatch):
    data = default_data()
    data["/v4/account/settings"] = {"backups_enabled": False}
    data["/v4/lke/clusters"] = [{"id": "../account", "label": "odd"}]
    use(Api(data))
    monkeypatch.setattr(ln, "MAX_IDENTITIES", 1)
    [scope] = ln.collect()
    checks = by_key(scope.checks)
    assert checks["identity.mfa"].reason == "unavailable"
    assert "by default" not in checks["resilience.backups"].detail
    assert checks["kubernetes.control_plane"].reason == "unavailable"
    monkeypatch.setattr(ln, "MAX_CLUSTERS", 1)
    use(Api())
    [scope] = ln.collect()
    assert by_key(scope.checks)["kubernetes.control_plane"].status == "unknown"
    data["/v4/account"] = {"euuid": "short"}
    use(Api(data))
    with pytest.raises(Unavailable):
        ln.collect()


def test_activity_beyond_the_ceiling_keeps_the_newest(use, monkeypatch):
    monkeypatch.setattr(ln, "MAX_ACTIVITY", 1)
    use(Api())
    entries, complete = ln.activity(NOW - timedelta(hours=6), NOW)
    assert [e.id for e in entries] == ["login:1"] and complete is False


def test_help_documents_scopes_checks_and_settings():
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[2] / "ui/public/help/administrator-linode.html"
    )
    text = page.read_text()
    for scope in ln.SCOPES:
        assert f"<code>{scope}</code>" in text, scope
    for key, label in ln.CHECKS.items():
        assert label in text and f"posture.{key}" in text, key
    for name in ("KEEN_LINODE_ENABLED", "LINODE_TOKEN"):
        assert f"<code>{name}</code>" in text, name
