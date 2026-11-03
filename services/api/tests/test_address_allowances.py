"""Per-address allowances count an IPv6 /64 network as one client address.

A client with IPv6 normally holds a whole /64, so an allowance keyed on its full
address gives it billions of allowances. Every per-address limit therefore goes
through ``address_window_allow``; a contract test refuses a client address in
any other allowance key, so a new limit cannot reintroduce the full-address key.
The forwarded-address chain reads every X-Forwarded-For field line.
"""

import ast
from pathlib import Path

import fakeredis
import pytest
from app.core.config import settings
from app.security.client_ip import request_ip
from app.security.rate_limit import address_allowance, address_window_allow
from starlette.requests import Request

APP = Path(__file__).resolve().parents[1] / "app"

# Where a client address comes from, and the parameter names that carry one.
_ADDRESS_CALLS = {"request_ip", "client_ip", "_client_ip", "normalise_ip"}
_ADDRESS_ATTRIBUTES = {"client_ip", "host"}
_ADDRESS_PARAMETERS = {"client_ip", "ip", "address", "addr", "peer"}


def _called(node):
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _holds_address(node, tainted):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and _called(sub) in _ADDRESS_CALLS:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr in _ADDRESS_ATTRIBUTES:
            return True
        if isinstance(sub, ast.Name) and sub.id in tainted:
            return True
    return False


def _offenders(tree, label):
    """Calls to fixed_window_allow whose key is built from a client address."""
    found = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if func.name == "address_window_allow":
            continue  # the one place that may key on an address
        arguments = func.args.posonlyargs + func.args.args + func.args.kwonlyargs
        tainted = {a.arg for a in arguments} & _ADDRESS_PARAMETERS
        # Names assigned from an address, followed until nothing changes.
        changed = True
        while changed:
            changed = False
            for node in ast.walk(func):
                if isinstance(node, ast.Assign) and _holds_address(node.value, tainted):
                    for target in node.targets:
                        for name in ast.walk(target):
                            if isinstance(name, ast.Name) and name.id not in tainted:
                                tainted.add(name.id)
                                changed = True
        for node in ast.walk(func):
            if not (
                isinstance(node, ast.Call) and _called(node) == "fixed_window_allow"
            ):
                continue
            key = node.args[1] if len(node.args) > 1 else None
            for keyword in node.keywords:
                if keyword.arg == "key":
                    key = keyword.value
            if key is not None and _holds_address(key, tainted):
                found.add(f"{label}:{node.lineno}")
    return found


def test_the_detector_finds_an_address_keyed_allowance():
    # The shape every per-address limit had before address_window_allow.
    direct = """
def login(request, r):
    fixed_window_allow(r, f"keen:rl:login:ip:{request_ip(request)}", 5, 60)
"""
    indirect = """
def change(context, r):
    ip = context.client_ip or "unknown"
    key = "keen:rl:x:" + ip
    fixed_window_allow(r, key, 5, 60)
"""
    parameter = """
def limit(r, prefix, address):
    fixed_window_allow(r, prefix + address, 5, 60)
"""
    other = """
def renew(r, agent):
    fixed_window_allow(r, "keen:agent:id:" + agent.id, 5, 60)
"""
    assert _offenders(ast.parse(direct), "direct") == {"direct:3"}
    assert _offenders(ast.parse(indirect), "indirect") == {"indirect:5"}
    assert _offenders(ast.parse(parameter), "parameter") == {"parameter:3"}
    assert _offenders(ast.parse(other), "other") == set()


def test_every_per_address_allowance_goes_through_address_window_allow():
    offenders = set()
    callers = set()
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text())
        label = str(path.relative_to(APP))
        offenders |= _offenders(tree, label)
        if any(
            isinstance(node, ast.Call) and _called(node) == "address_window_allow"
            for node in ast.walk(tree)
        ):
            callers.add(label)

    assert sorted(offenders) == []
    # Every per-address limit the application has is keyed this way.
    assert callers >= {
        "main.py",
        "api/routes/auth.py",
        "api/routes/agents.py",
        "api/routes/webhooks.py",
        "security/mfa.py",
        "security/oidc.py",
        "services/me.py",
        "services/agent_enrollment.py",
    }


@pytest.mark.parametrize(
    "first,second,shared",
    [
        ("2001:db8:1:2::1", "2001:db8:1:2:ffff:ffff:ffff:ffff", True),
        ("2001:db8:1:2::1", "2001:db8:1:3::1", False),
        ("198.51.100.4", "198.51.100.5", False),
        ("::ffff:198.51.100.4", "198.51.100.4", True),
        (None, "not-an-address", True),
    ],
)
def test_one_allowance_per_ipv4_address_and_ipv6_network(first, second, shared):
    r = fakeredis.FakeRedis()
    assert address_window_allow(r, "probe", first, 1, 60) == (True, 0)
    allowed, _ = address_window_allow(r, "probe", second, 1, 60)
    assert allowed is (not shared)


def test_unknown_addresses_share_one_allowance():
    assert address_allowance(None) is None
    assert address_allowance("testclient") is None
    assert address_allowance("2001:db8::7") == "2001:db8::/64"


def _request(lines, peer):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"x-forwarded-for", line.encode()) for line in lines],
            "client": (peer, 1234),
        }
    )


def test_every_forwarded_field_line_is_read(monkeypatch):
    # A trusted proxy that appends its own field line after the client's does
    # not let the client's line stand in for the nearest hop.
    monkeypatch.setattr(settings, "security_trusted_proxy_cidrs", "10.0.0.0/8")
    spoofed_then_proxy = _request(["10.20.30.40", "203.0.113.9"], "10.0.0.1")
    assert request_ip(spoofed_then_proxy) == "203.0.113.9"
    assert request_ip(_request(["203.0.113.9, 10.0.0.2"], "10.0.0.1")) == "203.0.113.9"
    assert request_ip(_request([], "10.0.0.1")) == "10.0.0.1"
