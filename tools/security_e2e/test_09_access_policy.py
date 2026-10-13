"""Every API operation has a declared access rule, and the deployment enforces it.

access_policy.py lists who may call each operation. An operation that is
missing from it fails here, so a new endpoint needs an explicit decision about
its permissions before this suite passes.

The enforcement check uses one synthetic account whose permissions change
between groups of requests (sessions pick up permission changes immediately):

- reads are sent with each permission set that should be allowed, and must not
  be refused;
- reads and writes are sent with the largest permission sets that should be
  refused, and must be refused (401, 403 or 404);
- writes are never sent with a permission set that should be allowed.

Operations with rules beyond permissions (ownership, object visibility) are
listed in access_policy.OBJECT_RULES; the checks here cover only their
permission gate, and other modules cover the object rules.
"""

from __future__ import annotations

import secrets
from collections import defaultdict

import access_policy
import pytest
from keen_e2e.access import Resolver, denied_sets, request_kwargs
from keen_e2e.client import Session

DENIED = {401, 403, 404}
REFUSED = {401, 403}


def _operations(schema):
    return {
        f"{method.upper()} {path}": operation
        for path, item in schema["paths"].items()
        for method, operation in item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    }


@pytest.fixture(scope="module")
def schema(admin):
    return admin.ok(admin.get("/api/openapi.json"), 200).json()


def test_every_operation_has_an_access_rule(schema):
    declared = set(access_policy.POLICY)
    served = set(_operations(schema))
    unclassified = sorted(served - declared)
    stale = sorted(declared - served - access_policy.OUTSIDE_SCHEMA)
    assert not unclassified, (
        "Add these operations to access_policy.POLICY with who may call them: "
        f"{unclassified}"
    )
    assert not stale, f"Remove operations the server no longer has: {stale}"


@pytest.fixture(scope="module")
def probe(target, admin, needs_writes_module, run_id, cleanup):
    password = "e2e-" + secrets.token_urlsafe(18)
    username = f"{run_id}-access-probe"
    created = admin.ok(
        admin.post(
            "/api/v1/users",
            json={
                "username": username,
                "password": password,
                "role": "normal",
            },
        ),
        200,
    ).json()
    cleanup.add(f"/api/v1/users/{created['id']}")
    session = Session(target, "access-probe")
    session.login(username, password)

    def grant(codes):
        admin.ok(
            admin.put(
                f"/api/v1/users/{created['id']}/access",
                json={"explicit_permission_codes": sorted(codes)},
            ),
            200,
        )

    yield session, grant
    session.close()


@pytest.fixture(scope="module")
def needs_writes_module(writes_allowed):
    if not writes_allowed:
        pytest.skip(
            "The access check needs KEEN_E2E_ALLOW_WRITES=1 for its probe account"
        )


@pytest.fixture(scope="module")
def resolver(admin, seed):
    ids = seed.ids
    known = {
        "/v1/risks": ids["risk"],
        "/v1/pestle/items": ids["pestle"],
        "/v1/interested-parties": ids["party"],
        "/v1/isms/objectives": ids["objective"],
        "/v1/isms/documents": ids["document"],
        "/v1/isms/meetings": ids["meeting"],
        "/v1/isms/org-nodes": ids["org_role"],
        "/v1/audits": ids["audit"],
        "entity": ids["risk"],
    }
    if "event" in ids:
        known["/v1/events"] = ids["event"]
        thread = admin.post(
            f"/api/v1/events/{ids['event']}/questions",
            json={"body": "e2e access check"},
        )
        if thread.status_code in (200, 201):
            known["/v1/questions"] = thread.json().get("thread_id")
    return Resolver(admin, known)


def _plan(schema):
    """Group requests by the permission set they are sent with."""
    every = frozenset(access_policy.PERMISSIONS)
    plan = defaultdict(list)  # permission set -> [(operation key, expectation)]
    for key, rule in access_policy.POLICY.items():
        if rule.kind in ("public", "outside") or key not in _operations(schema):
            continue
        method = key.split(" ", 1)[0]
        if rule.kind == "signed-in":
            if method == "GET":
                plan[frozenset()].append((key, "allowed"))
            continue
        if method == "GET":
            for alternative in rule.alternatives:
                plan[frozenset(alternative)].append((key, "allowed"))
        for refused in denied_sets(rule.alternatives, every):
            plan[refused].append((key, "denied"))
        if frozenset() not in denied_sets(rule.alternatives, every):
            plan[frozenset()].append((key, "denied"))
    return plan


def test_deployment_enforces_the_access_policy(probe, resolver, schema, payloads):
    session, grant = probe
    operations = _operations(schema)
    findings = []
    # Writes that should be refused go last, so a wrongly accepted change cannot
    # alter the records later reads depend on.
    plan = _plan(schema)
    ordered = sorted(plan.items(), key=lambda item: (len(item[0]), sorted(item[0])))
    for phase in ("GET", "write"):
        for codes, checks in ordered:
            selected = [
                (key, expect)
                for key, expect in checks
                if (
                    key.startswith("GET ")
                    if phase == "GET"
                    else not key.startswith("GET ")
                )
            ]
            if not selected:
                continue
            grant(codes)
            for key, expect in selected:
                method, path = key.split(" ", 1)
                url = "/api" + resolver.fill(path)
                response = session.request(
                    method, url, **request_kwargs(operations[key], schema)
                )
                status = response.status_code
                label = sorted(codes) or ["no permissions"]
                if expect == "allowed" and (status in REFUSED or status >= 500):
                    findings.append(
                        (
                            key,
                            label,
                            "refused" if status < 500 else "server error",
                            status,
                        )
                    )
                elif expect == "denied" and status not in DENIED:
                    outcome = "server error" if status >= 500 else "accepted"
                    findings.append((key, label, outcome, status))
                elif (
                    expect == "allowed"
                    and not codes
                    and payloads.marker in response.text
                ):
                    # An account without permissions sees seeded records nowhere.
                    findings.append((key, label, "shows restricted records", status))
    grant([])
    if findings:
        lines = [
            f"{key} with {', '.join(label)}: {outcome} ({status})"
            for key, label, outcome, status in findings
        ]
        pytest.fail(f"{len(findings)} access findings:\n" + "\n".join(lines))
