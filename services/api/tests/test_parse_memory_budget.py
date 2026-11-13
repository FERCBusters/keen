"""Parsing collected documents stays inside the HTTP worker's memory limit.

A parsed document costs many times its size, and how many depends on its shape:
short attributes on one element, and short names in the longest namespace name
the parser accepts, cost the most for XML, and empty objects or arrays for JSON. These tests build the costliest shapes at the largest size each
parser accepts, parse them in a fresh interpreter, and require the growth to
leave room for the worker's own baseline inside the limit Compose gives it. A
change to a response limit, a document limit or a parser that breaks the bound
fails here, whichever shape it affects.
"""

import json
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
import yaml
from app.ingest.http import MAX_RESPONSE_BYTES
from app.security.xml import (
    MAX_DOCUMENT_BYTES,
    MAX_NAMESPACE_NAME,
    parse_untrusted_xml,
)

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
import json, sys
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
    elif shape.startswith("namespace"):
        # Every distinct qualified name holds the whole namespace name.
        # A "-wide" shape spends the bound on four-byte characters, the widest
        # a stored name holds; the bound counts bytes, so both cost the same.
        wide = shape.endswith("-wide")
        name = ("\U0001f600" * ({namespace} // 4)).encode() if wide else b"u" * {namespace}
        assert len(name) == {namespace}
        head, unit, tail = {{
            "namespace-elements": (b"<r xmlns:p='" + name + b"'>", b"<p:a%x/>", b"</r>"),
            "namespace-default": (b"<r xmlns='" + name + b"'>", b"<a%x/>", b"</r>"),
            "namespace-attributes": (b"<r xmlns:p='" + name + b"'", b" p:a%x=''", b"/>"),
        }}[shape.removesuffix("-wide")]
        parts, used, i = [], len(head) + len(tail), 0
        while True:
            part = unit % i
            if used + len(part) > size:
                break
            parts.append(part); used += len(part); i += 1
        body = head + b"".join(parts) + tail
    elif shape.startswith("many-"):
        # A tree costs its number of distinct qualified names times what each
        # is held in. Any element can declare another namespace, so a document
        # reuses the shortest local names under many namespace names of the
        # longest accepted size: twice the names one namespace allows.
        first = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
        later = first + "0123456789.-"
        short = [a for a in first] + [a + b for a in first for b in later]
        if shape == "many-wide-text":
            # One-character names above U+00FF, which widen the stored name.
            short = [chr(c) for c in range(0x4E00, 0x9FA6)]
        parts, used, k = ["<r>"], 7, 0
        while used < size:
            if shape == "many-astral":
                tag = chr(0x10400 + k // 40) + chr(0x10400 + k % 40)
                name = tag + "\U0001f600" * ({namespace} // 4 - 2)
            else:
                name = "%05x" % k + "u" * ({namespace} - 5)
            group = ["<g xmlns='" + name + "'>"]
            cost = len(group[0].encode()) + 4
            for local in short:
                part = "<" + local + "/>"
                if used + cost + len(part.encode()) > size:
                    break
                group.append(part); cost += len(part.encode())
            parts.append("".join(group) + "</g>"); used += cost; k += 1
            if len(group) <= len(short):
                break
        text = "".join(parts) + "</r>"
        # Text reaches the parser from the Atom and repository feed callers.
        body = text if shape.endswith("-text") else text.encode()
    else:
        unit = {{"elements": b"<a/>", "mixed": b"<a b='' c='' d=''/>"}}[shape]
        body = b"<r>" + unit * ((size - 7) // len(unit)) + b"</r>"
else:
    parse = json.loads
    unit = {{"objects": b"{{}},", "arrays": b"[],", "numbers": b"1.5,"}}[shape]
    body = b"[" + unit * ((size - 8) // len(unit)) + unit.rstrip(b",") + b"]"
octets = len(body.encode()) if isinstance(body, str) else len(body)
assert octets <= size, octets
def peak():
    # This process's own peak resident size. getrusage's ru_maxrss starts at
    # the peak of the process that started this one, so it shows no growth
    # until this process outgrows the test runner.
    with open("/proc/self/status") as status:
        return next(int(line.split()[1]) for line in status if line.startswith("VmHWM:"))
before = peak()
tree = parse(body)
after = peak()
print(json.dumps({{"bytes": octets, "growth_mib": (after - before) / 1024}}))
"""


def _growth(kind, shape, size):
    code = _MEASURE.format(
        api=str(API), size=size, kind=kind, shape=shape, namespace=MAX_NAMESPACE_NAME
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        env={"KEEN_PUBLIC_BASE_URL": "https://keen.example", "PATH": "/usr/bin"},
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "shape",
    [
        "attributes",
        "elements",
        "mixed",
        "namespace-elements",
        "namespace-default",
        "namespace-attributes",
        "namespace-elements-wide",
        "namespace-default-wide",
        "namespace-attributes-wide",
        "many-ascii",
        "many-ascii-text",
        "many-astral",
        "many-wide-text",
    ],
)
def test_the_costliest_accepted_xml_parses_within_the_worker_budget(shape):
    measured = _growth("xml", shape, MAX_DOCUMENT_BYTES)
    assert measured["bytes"] > MAX_DOCUMENT_BYTES * 0.99
    assert measured["growth_mib"] < BUDGET_MIB, measured


@pytest.mark.parametrize("shape", ["objects", "arrays", "numbers"])
def test_the_costliest_collected_json_parses_within_the_worker_budget(shape):
    measured = _growth("json", shape, MAX_RESPONSE_BYTES)
    assert measured["bytes"] > MAX_RESPONSE_BYTES * 0.99
    assert measured["growth_mib"] < BUDGET_MIB, measured


def test_the_namespace_bound_is_what_keeps_namespaced_names_within_the_budget():
    # Without the bound, a document of about 80 KiB that declares one long
    # namespace name and uses it on many short names costs thousands of times
    # its size once parsed. The parser Keen uses refuses that document.
    name = b"u" * (64 * 1024)
    body = (
        b"<r xmlns:p='"
        + name
        + b"'>"
        + b"".join(b"<p:a%x/>" % i for i in range(1600))
        + b"</r>"
    )
    code = (
        "import sys\n"
        "from xml.etree import ElementTree as ET\n"
        "def peak():\n"
        "    with open('/proc/self/status') as status:\n"
        "        return next(int(l.split()[1]) for l in status if l.startswith('VmHWM:'))\n"
        "body = sys.stdin.buffer.read()\n"
        "before = peak()\n"
        "tree = ET.fromstring(body)\n"
        "print((peak() - before) * 1024 / len(body))\n"
    )
    unbounded = subprocess.run(
        [sys.executable, "-I", "-c", code], input=body, capture_output=True, timeout=120
    )
    assert unbounded.returncode == 0, unbounded.stderr[-2000:]
    # Far beyond the several dozen bytes per byte the budget allows for.
    assert float(unbounded.stdout) > 1000

    with pytest.raises(ET.ParseError, match="^XML namespace names over"):
        parse_untrusted_xml(body)
