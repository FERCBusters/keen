"""Parsing collected documents stays inside the HTTP worker's memory limit.

A parsed document costs many times its size, and how many depends on its shape:
short attributes on one element cost the most for XML, and empty objects or
arrays for JSON. These tests build the costliest shapes at the largest size each
parser accepts, parse them in a fresh interpreter, and require the growth to
leave room for the worker's own baseline inside the limit Compose gives it. A
change to a response limit, a document limit or a parser that breaks the bound
fails here, whichever shape it affects.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from app.ingest.http import MAX_RESPONSE_BYTES
from app.security.xml import MAX_DOCUMENT_BYTES

API = Path(__file__).resolve().parents[1]
COMPOSE = API.parents[1] / "docker-compose.yml"
# The imported application, the buffered response and its decoded text.
BASELINE_MIB = 256


def _limit_mib():
    worker = yaml.safe_load(COMPOSE.read_text())["services"]["keen-http-worker"]
    limit = str(worker["mem_limit"]).lower()
    assert limit.endswith("m"), limit
    return int(limit[:-1])


BUDGET_MIB = _limit_mib() - BASELINE_MIB

_MEASURE = """
import json, resource, sys
sys.path.insert(0, {api!r})
size, kind, shape = {size}, {kind!r}, {shape!r}
if kind == "xml":
    from app.security.xml import parse_untrusted_xml as parse
    if shape == "attributes":
        parts, used, i = [], 6, 0
        while True:
            part = b" a%x=''" % i
            if used + len(part) + 2 > size:
                break
            parts.append(part); used += len(part); i += 1
        body = b"<r" + b"".join(parts) + b"/>"
    else:
        unit = {{"elements": b"<a/>", "mixed": b"<a b='' c='' d=''/>"}}[shape]
        body = b"<r>" + unit * ((size - 7) // len(unit)) + b"</r>"
else:
    parse = json.loads
    unit = {{"objects": b"{{}},", "arrays": b"[],", "numbers": b"1.5,"}}[shape]
    body = b"[" + unit * ((size - 8) // len(unit)) + unit.rstrip(b",") + b"]"
assert len(body) <= size, len(body)
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
tree = parse(body)
after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(json.dumps({{"bytes": len(body), "growth_mib": (after - before) / 1024}}))
"""


def _growth(kind, shape, size):
    code = _MEASURE.format(api=str(API), size=size, kind=kind, shape=shape)
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        env={"KEEN_PUBLIC_BASE_URL": "https://keen.example", "PATH": "/usr/bin"},
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout)


@pytest.mark.parametrize("shape", ["attributes", "elements", "mixed"])
def test_the_costliest_accepted_xml_parses_within_the_worker_budget(shape):
    measured = _growth("xml", shape, MAX_DOCUMENT_BYTES)
    assert measured["bytes"] > MAX_DOCUMENT_BYTES * 0.99
    assert measured["growth_mib"] < BUDGET_MIB, measured


@pytest.mark.parametrize("shape", ["objects", "arrays", "numbers"])
def test_the_costliest_collected_json_parses_within_the_worker_budget(shape):
    measured = _growth("json", shape, MAX_RESPONSE_BYTES)
    assert measured["bytes"] > MAX_RESPONSE_BYTES * 0.99
    assert measured["growth_mib"] < BUDGET_MIB, measured
