"""One home each for the small helpers that were once typed out per module.

A second copy of a digest, a base64url codec or an ISO serialiser drifts from
the first, so the source tree is checked for hand-rolled ones.
"""

import ast
import hashlib
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from app.mapping.rules import evaluate_by_framework, event_fields, parse_rules
from myf.core.datetime_utils import iso_or_none, iso_utc, to_utc_naive
from myf.security import encoding
from myf.security.regex import scan_pattern

from tests.source_tree import SOURCES, name_in

APP = SOURCES
# The modules that own each helper.
OWNERS = {"security/encoding.py", "core/datetime_utils.py"}
ISO = scan_pattern(r"(\w+)\.isoformat\(\)(\+'Z')?if\1(isnotNone)?elseNone")


def _one_line_helpers(tree):
    """Each function whose whole body is one return, with the code it returns."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = [
            statement
            for statement in node.body
            if not (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
            )
        ]
        if len(body) == 1 and isinstance(body[0], ast.Return) and body[0].value:
            yield node.name, ast.unparse(body[0].value)


def _copies(source):
    """The helpers in ``source`` that restate one of the shared ones."""
    found = []
    for name, returned in _one_line_helpers(ast.parse(source)):
        text = returned.replace(" ", "")
        # A string's SHA-256 as hex, whatever the argument is called.
        if text.startswith("hashlib.sha256(") and text.endswith(
            ".encode()).hexdigest()"
        ):
            found.append(name)
        # Unpadded base64url, either way.
        elif "urlsafe_b64decode(" in text and "-len(" in text:
            found.append(name)
        elif "urlsafe_b64encode(" in text and "rstrip(" in text:
            found.append(name)
        # A date or time as ISO text, or nothing.
        elif ISO.fullmatch(text):
            found.append(name)
    return found


def test_no_module_restates_a_shared_digest_codec_or_serialiser():
    offenders = {}
    for path in APP.rglob("*.py"):
        label = str(name_in(path, APP))
        if label in OWNERS:
            continue
        copies = _copies(path.read_text())
        if copies:
            offenders[label] = copies
    assert offenders == {}


@pytest.mark.parametrize(
    "source",
    [
        "def token_hash(token):\n    return hashlib.sha256(token.encode()).hexdigest()",
        "def key(a, b):\n    return hashlib.sha256(f'{a}:{b}'.encode()).hexdigest()",
        "def unb64(v):\n    return base64.urlsafe_b64decode(v + '=' * (-len(v) % 4))",
        "def b64(d):\n    return base64.urlsafe_b64encode(d).rstrip(b'=').decode()",
        'def _iso(value):\n    """ISO."""\n    return value.isoformat() if value else None',
        "def _iso(v):\n    return v.isoformat() + 'Z' if v is not None else None",
    ],
)
def test_the_check_recognises_each_kind_of_copy(source):
    assert len(_copies(source)) == 1


def test_the_check_leaves_other_one_line_functions_alone():
    source = (
        "def label(row):\n    return row.name or 'Untitled'\n"
        "def started(row):\n    row.touch()\n    return row.at.isoformat() if row.at else None\n"
        "def nothing():\n    return\n"
    )
    assert _copies(source) == []


def test_the_shared_helpers_encode_as_their_callers_expect():
    assert encoding.digest("secret") == hashlib.sha256(b"secret").hexdigest()
    for value in (b"", b"a", b"ab", b"abc", bytes(range(256))):
        text = encoding.b64url(value)
        assert "=" not in text and "+" not in text and "/" not in text
        assert encoding.unb64url(text) == value

    moment = datetime(2026, 3, 1, 9, 30)
    assert iso_or_none(moment) == "2026-03-01T09:30:00"
    assert iso_or_none(date(2026, 3, 1)) == "2026-03-01"
    assert iso_or_none(None) is None
    assert iso_utc(moment) == "2026-03-01T09:30:00Z"
    assert iso_utc(None) is None
    aware = moment.replace(tzinfo=timezone.utc)
    assert to_utc_naive(aware) == moment and to_utc_naive(moment) == moment


def test_rules_read_one_view_of_an_event_wherever_they_are_evaluated():
    event = SimpleNamespace(
        source="github",
        connection_id="c1",
        connection_name="Main organisation",
        system="repo",
        actor="dana",
        action="push",
        outcome="success",
        severity="info",
        summary="Pushed to main",
        raw_pointer="github://repo/1",
        normalized_payload={"branch": "main"},
        id="not a rule field",
    )
    fields = event_fields(event)
    assert fields == {
        "source": "github",
        "connection_id": "c1",
        "connection_name": "Main organisation",
        "system": "repo",
        "actor": "dana",
        "action": "push",
        "outcome": "success",
        "severity": "info",
        "summary": "Pushed to main",
        "raw_pointer": "github://repo/1",
        "normalized_payload": {"branch": "main"},
    }
    rules = parse_rules(
        {
            "rules": [
                {
                    "id": "pushes",
                    "when": {"source": "github", "action": "push"},
                    "map_to": {"A": ["A.8.4"]},
                }
            ]
        }
    )
    assert evaluate_by_framework(fields, rules) == {"A": ["A.8.4"]}
