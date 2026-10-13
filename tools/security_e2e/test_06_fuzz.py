"""Bounded parameter and body fuzzing. A server error or a slow response is a finding.

The request budget, seed and pacing are configurable; results list the route,
parameter and status only.
"""

from __future__ import annotations

import json
import os
import random
import time
from urllib.parse import quote

import pytest
from keen_e2e.inventory import concrete, is_public
from keen_e2e.payloads import Payloads

BUDGET = int(os.environ.get("KEEN_E2E_FUZZ_CASES", "400"))
SEED = int(os.environ.get("KEEN_E2E_SEED", "1"))
SLOW_SECONDS = float(os.environ.get("KEEN_E2E_SLOW_SECONDS", "15"))
# Read endpoints that intentionally do expensive work are still bounded by these checks.


def _query_cases(openapi):
    cases = []
    for method, path, op in openapi:
        if method != "GET" or is_public(method, path):
            continue
        for param in op.get("parameters", []):
            if param.get("in") == "query":
                cases.append((path, param["name"]))
    return cases


def test_query_parameter_fuzzing(admin, openapi):
    rng = random.Random(SEED)
    values = Payloads.fuzz_values()
    cases = _query_cases(openapi)
    plan = [(path, name, rng.choice(values)) for path, name in cases for _ in range(3)]
    rng.shuffle(plan)
    findings = []
    for path, name, value in plan[:BUDGET]:
        url = "/api" + concrete(path) + "?" + quote(name) + "=" + quote(value, safe="")
        started = time.monotonic()
        try:
            response = admin.get(url)
        except Exception as exc:
            findings.append((path, name, values.index(value), type(exc).__name__))
            continue
        elapsed = time.monotonic() - started
        if response.status_code >= 500:
            findings.append((path, name, values.index(value), response.status_code))
        elif elapsed > SLOW_SECONDS:
            findings.append((path, name, values.index(value), f"slow {elapsed:.1f}s"))
        elif response.status_code == 200 and "html" in response.headers.get(
            "content-type", ""
        ):
            if value in response.text and "<" in value:
                findings.append((path, name, values.index(value), "reflected"))
    assert not findings, f"value index refers to Payloads.fuzz_values(): {findings}"


def test_path_parameter_fuzzing(admin, openapi):
    rng = random.Random(SEED + 1)
    values = [v for v in Payloads.fuzz_values() if v and "\x00" not in v]
    findings = []
    templated = [
        (m, p) for m, p, _ in openapi if m == "GET" and "{" in p and not is_public(m, p)
    ]
    for path in sorted({p for _, p in templated}):
        for value in rng.sample(values, min(6, len(values))):
            response = admin.get("/api" + concrete(path, quote(value, safe="")))
            if response.status_code >= 500:
                findings.append((path, values.index(value), response.status_code))
    assert not findings, findings


def _nested(depth):
    return "[" * depth + "]" * depth


@pytest.mark.parametrize(
    "path",
    ["/api/v1/me/preferences", "/api/v1/auth/login", "/api/v1/me/saved-searches"],
)
def test_malformed_json_bodies_are_rejected_cleanly(admin, anonymous, path):
    session = anonymous if path.endswith("/login") else admin
    method = "PATCH" if path.endswith("preferences") else "POST"
    bodies = [
        b"{",
        b'{"a":',
        b"\xff\xfe\x00",
        _nested(100000).encode(),
        ('{"x":' * 5000 + "1" + "}" * 5000).encode(),
        b'{"username":' + b'"' + b"a" * 20000 + b'"}',
        json.dumps({"username": ["list"], "password": {"dict": 1}}).encode(),
        b"1e999999",
        b"NaN",
    ]
    findings = []
    for index, body in enumerate(bodies):
        response = session.request(
            method, path, content=body, headers={"Content-Type": "application/json"}
        )
        if response.status_code >= 500:
            findings.append((index, response.status_code))
    assert not findings, (path, findings)


def test_create_payload_type_confusion(admin, needs_writes, cleanup, run_id):
    """Wrong JSON types and boundary values are validation errors, never server errors."""
    weird = [
        None,
        0,
        -1,
        2**63,
        1e308,
        True,
        [],
        {},
        ["x"],
        {"$ne": 1},
        "x" * 300000,
        "\x00",
        "😀" * 1000,
    ]
    endpoints = {
        "/api/v1/pestle/items": {
            "type": "Political",
            "lens": "Internal",
            "item": run_id,
        },
        "/api/v1/isms/objectives": {"requirement": run_id, "goal": run_id},
        "/api/v1/interested-parties": {"name": run_id, "nature": run_id},
        "/api/v1/audits": {"title": run_id},
    }
    findings = []
    for path, base in endpoints.items():
        for field in list(base) + ["status", "framework", "controls"]:
            for value in weird:
                body = dict(base, **{field: value})
                response = admin.post(path, json=body)
                if response.status_code >= 500:
                    findings.append(
                        (path, field, repr(value)[:20], response.status_code)
                    )
                elif response.status_code in (200, 201):
                    created = response.json()
                    ident = created.get("id") if isinstance(created, dict) else None
                    if ident:
                        cleanup.add(f"{path}/{ident}")
    assert not findings, findings


def test_chart_window_budget(accounts):
    session = accounts("events_reader")
    for params in (
        "start_date=1000-01-01&end_date=1999-12-31",
        "start_date=2026-01-01&end_date=9999-12-31",
        "start_ts=1000-01-01T00:00:00&end_ts=2026-01-01T00:00:00&interval=second",
    ):
        started = time.monotonic()
        response = session.get("/api/v1/charts/event_volume_timeseries?" + params)
        assert response.status_code == 400, (params, response.status_code)
        assert time.monotonic() - started < 5
