"""Every served route has a declared access rule.

tools/security_e2e/access_policy.py states who may call each operation, and the
security suite checks a running deployment against it. This test fails as soon
as a route is added, removed or made public without updating that file, so the
decision is made when the route is written.
"""

import importlib.util
import re
import sys
from pathlib import Path

from fastapi.routing import APIRoute, APIWebSocketRoute

POLICY_FILE = (
    Path(__file__).resolve().parents[3] / "tools/security_e2e/access_policy.py"
)


def _policy():
    spec = importlib.util.spec_from_file_location("keen_access_policy", POLICY_FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


def _pattern(path):
    # The schema writes {ref:path} as {ref}; the policy uses the schema's form.
    return re.sub(r"\{(\w+):[^}]+\}", r"{\1}", path)


def _served(routes, prefix=""):
    for route in routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                yield f"{method} {_pattern(prefix + route.path)}"
        elif isinstance(route, APIWebSocketRoute):
            yield f"WEBSOCKET {_pattern(prefix + route.path)}"
        elif hasattr(route, "original_router"):
            context = getattr(route, "include_context", None)
            yield from _served(
                route.original_router.routes,
                prefix + (getattr(context, "prefix", "") or ""),
            )
        elif hasattr(route, "routes"):
            yield from _served(route.routes, prefix + getattr(route, "path", ""))
        else:
            raise AssertionError(f"Unrecognised route type {type(route).__name__}")


def test_every_served_route_has_an_access_rule():
    from app import main

    policy = _policy()
    served = set(_served(main.app.routes))
    assert len(served) > 300  # the walk reached the included routers
    missing = sorted(served - set(policy.POLICY))
    stale = sorted(set(policy.POLICY) - served)
    assert not missing, f"Give these routes a rule in {POLICY_FILE.name}: {missing}"
    assert not stale, f"Remove rules for routes that no longer exist: {stale}"


def test_public_rules_match_the_route_policy():
    from app.security.route_policy import PUBLIC_HTTP_ROUTES

    policy = _policy()
    public = {f"{m} {p}" for m, paths in PUBLIC_HTTP_ROUTES.items() for p in paths}
    declared = {k for k, rule in policy.POLICY.items() if rule.kind == "public"}
    assert declared == public


def test_rules_cover_every_permission_the_application_checks():
    policy = _policy()
    known = set(policy.PERMISSIONS)
    for key, rule in policy.POLICY.items():
        for alternative in rule.alternatives:
            assert alternative <= known, (key, sorted(alternative - known))
    # A permission code the application checks but the policy does not know
    # would never be granted to the probe account, so its gate would go untested.
    pattern = re.compile(r"[\"']([a-z_]+\.(?:read|manage|admin|create|delete))[\"']")
    app_dir = Path(__file__).resolve().parents[1] / "app"
    used = {
        match
        for path in app_dir.rglob("*.py")
        for match in pattern.findall(path.read_text())
    }
    assert used <= known, sorted(used - known)
