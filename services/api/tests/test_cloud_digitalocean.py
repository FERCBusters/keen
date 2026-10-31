"""DigitalOcean posture and activity against a fake DigitalOcean API behind
httpx's mock transport. No network is used."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from app.core.config import settings
from app.ingest.cloud import digitalocean as do
from app.ingest.cloud.base import Unavailable, parse_time

TOKEN = "dop_v1_synthetic0token0value0000"
# DigitalOcean team and account IDs are 40 hexadecimal digits.
TEAM = "5df3e3004a17e242b7c20ca6c9fc25b701a47ece"
ACCOUNT = "b6fc48dbf6d9906cace5f3c78dc9851e757381ef"
DB_OPEN = "0f0e0d0c-0b0a-4908-8706-050403020100"
DB_SAFE = "1f1e1d1c-1b1a-4918-8716-151413121110"
NOW = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)


def ago(hours):
    return (NOW - timedelta(hours=hours)).isoformat() + "Z"


def anywhere(*ports, action="allow"):
    return [
        {
            "protocol": "tcp",
            "ports": port,
            "action": action,
            "sources": {"addresses": ["0.0.0.0/0", "::/0"]},
        }
        for port in ports
    ]


def default_data():
    return {
        "/v2/account": {
            "account": {
                "uuid": ACCOUNT,
                "email": "owner@example.org",
                "team": {"uuid": TEAM, "name": "Example Ltd"},
            }
        },
        "/v2/droplets": [
            {"id": 1, "name": "web-1", "tags": ["web"], "features": ["backups"]},
            {"id": 2, "name": "staging-web", "tags": [], "features": []},
            {"id": 3, "name": "bastion", "tags": [], "features": ["backups"]},
        ],
        "/v2/firewalls": [
            {
                "id": "f1",
                "name": "web",
                "tags": ["web"],
                "inbound_rules": anywhere("443"),
            },
            {
                "id": "f2",
                "name": "legacy-ssh",
                "droplet_ids": [3],
                "inbound_rules": anywhere("20-30"),
            },
            {
                "id": "f3",
                "name": "unused-open",
                "droplet_ids": [],
                "inbound_rules": anywhere("all"),
            },
        ],
        "/v2/databases": [
            {"id": DB_OPEN, "name": "open-db"},
            {"id": DB_SAFE, "name": "orders"},
        ],
        f"/v2/databases/{DB_OPEN}/firewall": {"rules": []},
        f"/v2/databases/{DB_SAFE}/firewall": {
            "rules": [{"type": "droplet", "value": "1"}]
        },
        "/v2/kubernetes/clusters": [
            {"id": "k1", "name": "apps", "ha": True, "auto_upgrade": True},
            {"id": "k2", "name": "old", "ha": False, "auto_upgrade": True},
        ],
        "/v2/security/scans/latest": {
            "scan": {"status": "COMPLETED", "findings": [{"severity": "HIGH"}]}
        },
        "/v2/actions": [
            {
                "id": 9,
                "type": "snapshot",
                "status": "completed",
                "started_at": ago(1),
                "resource_type": "droplet",
                "region_slug": "ams3",
            },
            {
                "id": 8,
                "type": "power_cycle",
                "status": "errored",
                "started_at": ago(2),
                "resource_type": "droplet",
            },
            {"id": 7, "type": "resize", "status": "completed", "started_at": ago(30)},
        ],
    }


class Api:
    def __init__(self, data=None, *, page_size=200, status=None):
        self.data = default_data() if data is None else data
        self.page_size = page_size
        self.status = status or {}
        self.requests = []

    def handler(self, request: httpx.Request):
        self.requests.append(request)
        if request.url.host != "api.digitalocean.com":
            return httpx.Response(200, json={"droplets": [], "stolen": True})
        path = request.url.path
        if path in self.status:
            return httpx.Response(self.status[path], json={"id": "forbidden"})
        value = self.data.get(path)
        if value is None:
            return httpx.Response(404, json={"id": "not_found", "message": "secret"})
        if path == "/v2/security/scans/latest":
            severity = request.url.params.get("severity")
            scan = dict(value["scan"])
            scan["findings"] = [
                f for f in scan.get("findings", []) if f["severity"] == severity
            ]
            return httpx.Response(200, json={"scan": scan})
        if isinstance(value, dict):
            return httpx.Response(200, json=value)
        key = path.rsplit("/", 1)[-1]
        key = {"clusters": "kubernetes_clusters"}.get(key, key)
        page = int(request.url.params.get("page", 1))
        size = self.page_size
        rows = value[(page - 1) * size : page * size]
        body = {key: rows, "links": {}, "meta": {"total": len(value)}}
        if page * size < len(value):
            body["links"]["pages"] = {
                "next": f"https://api.digitalocean.com{path}?page={page + 1}"
            }
        return httpx.Response(200, json=body)

    def digitalocean(self):
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return do.DigitalOcean(
            do.ORIGIN, {"Authorization": f"Bearer {TOKEN}"}, client=client
        )


def by_key(checks):
    return {c.key: c for c in checks}


@pytest.fixture
def use(monkeypatch):
    monkeypatch.setattr(settings, "digitalocean_token", TOKEN)
    monkeypatch.setattr(settings, "demo_mode", False)

    def install(api):
        monkeypatch.setattr(do, "connect", api.digitalocean)
        return api

    return install


def test_a_token_is_required(monkeypatch):
    for value in ("", "short", "with space 1234567890abc"):
        monkeypatch.setattr(settings, "digitalocean_token", value)
        with pytest.raises(Unavailable) as caught:
            do.connect()
        assert caught.value.reason == "not_configured"


def test_collect_reads_the_team(use):
    api = use(Api())
    [scope] = do.collect()
    assert scope.key == TEAM and scope.label == "Example Ltd" and scope.kind == "team"
    assert scope.identities == []
    checks = by_key(scope.checks)
    assert set(checks) == set(do.CHECKS)
    # The firewall that protects no Droplet is left out.
    assert checks["network.admin_ports"].subjects == ["legacy-ssh"]
    assert checks["network.firewall_coverage"].subjects == ["staging-web"]
    # A database without trusted sources accepts every address.
    assert checks["network.databases"].subjects == ["open-db"]
    assert checks["resilience.backups"].subjects == ["staging-web"]
    assert checks["kubernetes.control_plane"].subjects == ["old: no high availability"]
    assert checks["findings"].status == "attention"
    assert checks["findings"].detail.startswith("0 critical and 1 high")
    assert all(r.url.host == "api.digitalocean.com" for r in api.requests)


def test_the_scope_is_the_team_or_else_the_account(use):
    data = default_data()
    data["/v2/account"]["account"].pop("team")
    use(Api(data))
    [scope] = do.collect()
    assert (scope.key, scope.label, scope.kind) == (
        ACCOUNT,
        "owner@example.org",
        "account",
    )
    for bad in ("", "not-an-id", "g" * 40, "../v2/droplets"):
        data["/v2/account"]["account"]["uuid"] = bad
        use(Api(data))
        with pytest.raises(Unavailable):
            do.collect()


def test_a_deny_rule_for_every_address_wins():
    wall = {"inbound_rules": anywhere("22") + anywhere("22", action="deny")}
    assert do.exposed_ports(wall) == []
    assert do.exposed_ports({"inbound_rules": anywhere("3389")}) == [3389]
    narrow = {
        "protocol": "tcp",
        "ports": "22",
        "action": "deny",
        "sources": {"addresses": ["198.51.100.0/24"]},
    }
    wall = {"inbound_rules": anywhere("0") + [narrow]}
    assert do.exposed_ports(wall) == [22, 3389]


def test_security_scans_that_are_off_read_as_not_set_up(use):
    data = default_data()
    data["/v2/security/scans/latest"] = {"scan": {"status": "CSPM_NOT_ENABLED"}}
    use(Api(data))
    [scope] = do.collect()
    finding = by_key(scope.checks)["findings"]
    assert finding.status == "unknown" and finding.reason == "not_configured"


def test_a_refused_scope_marks_only_its_checks(use):
    use(Api(status={"/v2/kubernetes/clusters": 403}))
    [scope] = do.collect()
    checks = by_key(scope.checks)
    assert checks["kubernetes.control_plane"].reason == "permission_denied"
    assert checks["network.databases"].status == "attention"


def test_listings_are_bounded_and_paging_links_are_not_followed(use, monkeypatch):
    monkeypatch.setattr(do, "MAX_PAGES", 1)
    api = use(Api(page_size=1))
    [scope] = do.collect()
    assert by_key(scope.checks)["resilience.backups"].status == "unknown"
    # Pages are requested by number on DigitalOcean's own host.
    assert all(r.url.host == "api.digitalocean.com" for r in api.requests)


def test_activity_stops_at_the_window(use):
    use(Api(page_size=2))
    entries, complete = do.activity(NOW - timedelta(hours=6), NOW)
    assert complete
    assert [(e.id, e.action, e.outcome) for e in entries] == [
        ("action:8", "power_cycle", "failure"),
        ("action:9", "snapshot", "success"),
    ]
    assert entries[0].scope == TEAM


def test_a_valid_token_opens_a_session_on_digitaloceans_host(monkeypatch):
    monkeypatch.setattr(settings, "digitalocean_token", TOKEN)
    with do.connect() as api:
        assert api.origin == do.ORIGIN


def test_rules_that_cannot_admit_the_port_are_ignored():
    def allow(protocol, ports, addresses):
        return {
            "protocol": protocol,
            "ports": ports,
            "action": "allow",
            "sources": {"addresses": addresses},
        }

    wall = {
        "inbound_rules": [
            allow("udp", "22", ["0.0.0.0/0"]),
            allow("tcp", "ssh", ["0.0.0.0/0"]),
            allow("tcp", "3389", ["not-an-address", "::/0"]),
        ]
    }
    assert do.exposed_ports(wall) == [3389]


def test_database_listings_must_be_bounded_and_well_formed(use, monkeypatch):
    for change in (
        {"/v2/databases": [{"id": "../v2/account", "name": "odd"}]},
        {f"/v2/databases/{DB_OPEN}/firewall": {"rules": "none"}},
    ):
        use(Api({**default_data(), **change}))
        [scope] = do.collect()
        assert by_key(scope.checks)["network.databases"].reason == "unavailable"
    monkeypatch.setattr(do, "MAX_DATABASES", 1)
    use(Api())
    [scope] = do.collect()
    assert by_key(scope.checks)["network.databases"].status == "unknown"


def test_clusters_without_upgrades_and_clean_scans(use):
    data = default_data()
    data["/v2/kubernetes/clusters"] = [
        {"id": "k3", "name": "edge", "ha": True, "auto_upgrade": False}
    ]
    data["/v2/security/scans/latest"] = {"scan": {"status": "COMPLETED"}}
    use(Api(data))
    [scope] = do.collect()
    checks = by_key(scope.checks)
    assert checks["kubernetes.control_plane"].subjects == [
        "edge: no automatic upgrades"
    ]
    assert checks["findings"].status == "met"


def test_unreadable_actions_are_skipped_and_activity_is_bounded(use, monkeypatch):
    assert parse_time("") is None and parse_time("yesterday") is None
    assert parse_time("2026-01-02T03:04:05") == datetime(2026, 1, 2, 3, 4, 5)
    assert do.action_entry({"id": "9", "type": "x", "started_at": ago(1)}, TEAM) is None
    monkeypatch.setattr(do, "MAX_ACTIVITY", 1)
    use(Api(page_size=2))
    entries, complete = do.activity(NOW - timedelta(hours=6), NOW)
    assert [e.id for e in entries] == ["action:9"] and complete is False


def test_help_documents_scopes_checks_and_settings():
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[2]
        / "ui/public/help/administrator-digitalocean.html"
    )
    text = page.read_text()
    for scope in do.SCOPES:
        assert f"<code>{scope}</code>" in text, scope
    for key, label in do.CHECKS.items():
        assert label in text and f"posture.{key}" in text, key
    for name in ("KEEN_DIGITALOCEAN_ENABLED", "DIGITALOCEAN_TOKEN"):
        assert f"<code>{name}</code>" in text, name
