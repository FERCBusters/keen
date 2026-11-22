"""Every regular expression the application runs compiles through one boundary.

scan_pattern rejects a pattern that holds matching state for each repetition
of a group, capture or alternatives, or whose nested repeats backtrack
exponentially, so no pattern needs more memory, or exponentially more time, as
the text it scans grows. Fixed patterns compile at import, so a rejected one
stops the application before it serves a request.
"""

import ast
import itertools
import re
import tracemalloc

import pytest
import regex
from app.mapping.rules import evaluate_by_framework, parse_rules
from app.services.managed_configurations import _validate_regex
from app.services.rule_sources import reads_source
from myf.security import regex as regex_boundary
from myf.security.redaction import mask_event_data_str, redact_bytes, redact_str
from myf.security.regex import (
    SCAN_REPEATS_HELD,
    check_scan_pattern,
    compile_pattern,
    scan_pattern,
    validate_pattern,
)
from myf.services.errors import DomainError

from tests.source_tree import SOURCES, name_in

APP = SOURCES
MIB = 1024 * 1024
# Calls that compile or run a pattern without the boundary's checks.
DIRECT = {
    "compile",
    "findall",
    "finditer",
    "fullmatch",
    "match",
    "search",
    "split",
    "sub",
    "subn",
}
# Pydantic and FastAPI declare string patterns as keyword arguments.
DECLARED = {"Body", "Cookie", "Field", "Form", "Header", "Path", "Query", "constr"}


def direct_regex_uses(root):
    """List pattern compilation and matching outside the boundary module."""
    uses = []
    for path in sorted(root.rglob("*.py")):
        if path == root / "security/regex.py":
            continue
        where = name_in(path, root)
        nodes = sorted(
            (n for n in ast.walk(ast.parse(path.read_text())) if hasattr(n, "lineno")),
            key=lambda n: (n.lineno, n.col_offset),
        )
        for node in nodes:
            if isinstance(node, ast.ImportFrom) and node.module in ("re", "regex"):
                uses.append(f"{where}:{node.lineno}: from {node.module} import")
            elif isinstance(node, ast.Import):
                uses.extend(
                    f"{where}:{node.lineno}: import {alias.name} as {alias.asname}"
                    for alias in node.names
                    if alias.name in ("re", "regex") and alias.asname
                )
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                module, name = node.value.id, node.attr
                if (module == "re" and name in DIRECT) or (
                    module == "regex" and name != "escape"
                ):
                    uses.append(f"{where}:{node.lineno}: {module}.{name}")
    return uses


def test_application_code_compiles_patterns_only_through_the_boundary():
    assert direct_regex_uses(APP) == []


def test_the_boundary_check_finds_direct_pattern_use(tmp_path):
    (tmp_path / "security").mkdir()
    (tmp_path / "security/regex.py").write_text("import re\nre.compile('a')\n")
    (tmp_path / "feeds.py").write_text(
        "import re\n"
        "import re as pattern\n"
        "import regex\n"
        "from re import sub\n"
        "TITLE = re.compile('a')\n"
        "find = re.search\n"
        "re.escape('a')\n"
        "regex.compile('a')\n"
        "regex.escape('a')\n"
    )

    assert direct_regex_uses(tmp_path) == [
        "feeds.py:2: import re as pattern",
        "feeds.py:4: from re import",
        "feeds.py:5: re.compile",
        "feeds.py:6: re.search",
        "feeds.py:8: regex.compile",
    ]


def _module_values(tree):
    """Module-level string constants, and the patterns compiled from them."""
    values = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        if isinstance(value, ast.Call) and _calls_scan_pattern(value) and value.args:
            values[target.id + ".pattern"] = _resolve(value.args[0], values)
        else:
            values[target.id] = _resolve(value, values)
    return values


def _calls_scan_pattern(node):
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    return name == "scan_pattern"


def _resolve(node, values):
    """Every text a pattern expression can take, with None for a runtime part.

    A runtime part is escaped text or a key list in the patterns that use one,
    so a letter stands in for it when the shape is checked.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return [node.value]
    if isinstance(node, ast.Name):
        return values.get(node.id, [None])
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return values.get(f"{node.value.id}.{node.attr}", [None])
    if isinstance(node, ast.IfExp):
        return _resolve(node.body, values) + _resolve(node.orelse, values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts = [_resolve(node.left, values), _resolve(node.right, values)]
    elif isinstance(node, ast.JoinedStr):
        parts = [
            _resolve(part, values) if isinstance(part, ast.Constant) else [None]
            for part in node.values
        ]
    else:
        return [None]
    return [_join(choice) for choice in itertools.product(*parts)]


def _join(parts):
    kind = next((type(p) for p in parts if p is not None), str)
    stand_in = b"x" if kind is bytes else "x"
    return stand_in[:0].join(stand_in if p is None else p for p in parts)


def _flags(node):
    if node is None:
        return 0
    if isinstance(node, ast.Attribute) and ast.unparse(node.value) == "re":
        return int(getattr(re, node.attr))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _flags(node.left) | _flags(node.right)
    raise AssertionError(f"Unresolved flags {ast.unparse(node)}")


def code_patterns(root):
    """Yield each pattern the application compiles or declares, as written."""
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        values = _module_values(tree)
        where = name_in(path, root)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "scan_pattern" and node.args:
                flags = node.args[1] if len(node.args) > 1 else None
                flags = next(
                    (k.value for k in node.keywords if k.arg == "flags"), flags
                )
                for pattern in _resolve(node.args[0], values):
                    yield f"{where}:{node.lineno}", pattern, _flags(flags)
            elif name in DECLARED:
                for keyword in node.keywords:
                    if keyword.arg == "pattern":
                        for pattern in _resolve(keyword.value, values):
                            yield f"{where}:{keyword.lineno}", pattern, 0


def test_every_pattern_in_the_application_passes_the_scan_checks():
    checked, refused = 0, []
    for where, pattern, flags in code_patterns(APP):
        if pattern is None:
            continue
        checked += 1
        try:
            check_scan_pattern(pattern, flags)
        except ValueError as exc:
            refused.append(f"{where}: {exc}")
    assert refused == []
    # The inventory reads the patterns themselves, not just the call sites.
    assert checked > 140


def test_patterns_built_at_run_time_are_checked_where_they_compile():
    built = [where for where, pattern, _ in code_patterns(APP) if pattern is None]
    # Every pattern resolves, with escaped text or configured keys standing in
    # as a letter; scan_pattern checks the text it is given in any case.
    assert built == []
    with pytest.raises(ValueError, match="possessively"):
        scan_pattern("(?:" + re.escape("a.b") + ")*")


HELD = "possessively"
NESTED = "nested repeats"


@pytest.mark.parametrize(
    "pattern, refusal",
    [
        ("(?:ab)*", HELD),
        ("(?:ab)+", HELD),
        ("(?:ab)*?$", HELD),
        ("(a)*", HELD),
        ("(?i)(?:a|bc)*", HELD),
        ('"(?:[^"\\\\]|\\\\[\\s\\S])*"', HELD),
        ("(?:ab){0,%d}" % (SCAN_REPEATS_HELD + 1), HELD),
        ("(?:(?:ab){0,100}c){0,100}", HELD),
        ("(?>(?:ab)*)", HELD),
        ("(?=(?:ab)*)", HELD),
        ("(a)?(?(1)(?:ab)*|b)", HELD),
        ("(a)?(?(1)b|(?:ab)*)", HELD),
        ("x|(?:ab)*", HELD),
        ("(?:a+){0,4}", NESTED),
        ("(?:a+b)*+(?:c+){2}", NESTED),
        ("(?:a|b+){2}", NESTED),
        ("(", "Invalid pattern"),
    ],
)
def test_the_scan_checks_refuse_shapes_that_grow(pattern, refusal):
    with pytest.raises(ValueError, match=re.escape(refusal)):
        check_scan_pattern(pattern)


@pytest.mark.parametrize(
    "pattern",
    [
        "(?:ab)*+",
        "(?:ab)++",
        '"(?:[^"\\\\]|\\\\[\\s\\S])*+"',
        "(?:a)*",
        "(?i:a)*",
        "(?:a|b)*",
        '[^"]*?$',
        "(?:[a-z]{1,40}\\.){0,1000}",
        "(?:(?:ab){0,30}c){0,30}",
        "(?:a++){0,4}",
        "(?:\\w+\\s?)*+",
        "(a)?(?(1)b)",
        "(?<!a)b+",
        rb"[1-9][0-9]* \((?:UID ([0-9]+) )?BODY\[",
    ],
)
def test_the_scan_checks_accept_shapes_that_stay_flat(pattern):
    check_scan_pattern(pattern)


def _peak(run):
    tracemalloc.start()
    try:
        run()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize(
    "pattern, unit, refused",
    [
        ("(?:ab)*", "ab", True),
        ("(?:ab)*+", "ab", False),
        ("(a)*", "a", True),
        ("(?:a)*", "a", False),
        ("(?i)(?:a|bc)*", "a", True),
        ("(?i)(?:a|bc)*+", "a", False),
        ("(?:a|b)*", "ab", False),
        ("(?>(?:ab)*)", "ab", True),
        ("(?:\\w+\\s?)*", "ab ", True),
        ("(?:\\w+\\s?)*+", "ab ", False),
    ],
)
def test_the_scan_checks_follow_the_engines_memory(pattern, unit, refused):
    """A refused shape holds memory per repetition; an accepted one does not.

    This keeps the checks honest if a later Python changes how its engine
    compiles a repeat.
    """
    text = unit * (256 * 1024 // len(unit))
    compiled = re.compile(pattern)
    peak = _peak(lambda: compiled.match(text))
    if refused:
        assert peak > 2 * MIB
        with pytest.raises(
            ValueError,
            match=r"^Repeat a group, capture or alternatives possessively \(\*\+, \+\+ or \{m,n\}\+\), or at most 1024 times in all: matching keeps state for every repetition$",
        ):
            check_scan_pattern(pattern)
    else:
        assert peak < MIB // 4
        check_scan_pattern(pattern)


def test_timed_patterns_compile_with_the_regex_package():
    tags = scan_pattern(r"<[^>]+>", timed=True)
    assert isinstance(tags, regex.Pattern)
    assert tags.sub(" ", "<p>text</p>", timeout=1) == " text "
    assert scan_pattern(r"<[^>]+>") is not tags
    with pytest.raises(ValueError, match="possessively"):
        scan_pattern(r"(?:<p>)*", timed=True)
    assert scan_pattern(r"<[^>]+>", timed=True) is tags


LARGE = 4 * MIB


@pytest.mark.parametrize(
    "text",
    [
        'password="' + "a" * LARGE,
        'password="' + "a" * LARGE + '"',
        "password='" + "\\a" * (LARGE // 2),
        '"token": "' + "a" * LARGE,
        '"token": "' + "\\a" * (LARGE // 2),
        "authorization: " + "a" * LARGE,
        "https://" + "a" * LARGE,
        "<" + "a" * LARGE,
        "1:" * (LARGE // 2),
    ],
    ids=[
        "key-value",
        "key-value-closed",
        "key-value-escapes",
        "json-pair",
        "json-pair-escapes",
        "header-line",
        "url",
        "mailbox",
        "address",
    ],
)
def test_redaction_memory_stays_in_proportion_to_its_text(text):
    """Megabytes of quoted evidence take a few copies of the text, no more.

    The key-value scan once held state for every character of a quoted value:
    a 4 MiB value took more than 700 MiB.
    """
    for run in (
        lambda: redact_str(text),
        lambda: redact_bytes(text.encode(), "text/plain"),
        lambda: mask_event_data_str(text),
    ):
        assert _peak(run) < 6 * len(text) + MIB


# Constructs CPython's parser and the regex package that matches supplied
# patterns read differently, or that only one of them reads.
DIALECT_PROBES = (
    r"\b{start}",
    r"\b{end}",
    r"\b{start,end}",
    r"\b{wb}",
    r"\p{L}",
    r"\P{Lu}",
    r"\G",
    r"\K",
    r"\X",
    "(?|a)",
    "(?V1)a",
    r"[\d-z]",
    "[[a]]",
    "[a--b]",
    "[a&&b]",
    "[a[:digit:]",
    "a{,3}",
    r"\N{DASH}",
    "(?(DEFINE)a)",
    "a{1,2}{3}",
    "x{2,1}",
    r"A",
)


def _reads(compile_with, pattern):
    try:
        compile_with(pattern)
    except Exception:  # noqa: BLE001 - either engine may refuse in its own way
        return False
    return True


def test_every_match_pattern_the_checks_accept_compiles_in_the_matching_engine():
    """A pattern the rule editor saves must compile where it is matched.

    The checks read a pattern with Python's parser and matching compiles it
    with the regex package. The probes include constructs the two disagree on,
    so a check that read with one engine only would accept one of them here.
    """
    divergent = [
        pattern
        for pattern in DIALECT_PROBES
        if _reads(re.compile, pattern)
        != _reads(lambda p: regex.compile(p, regex.VERSION0), pattern)
    ]
    assert {r"\b{start}", r"\b{end}", r"\p{L}", "[a[:digit:]"} <= set(divergent)
    for pattern in DIALECT_PROBES:
        try:
            validate_pattern(pattern)
        except ValueError:
            continue
        # Matching runs; whether it finds anything is the pattern's business.
        compile_pattern(pattern).search("A dash and some words")


def test_a_rule_whose_pattern_cannot_compile_never_stops_the_other_rules(
    monkeypatch, caplog
):
    """A rule saved before the editor compiled its pattern, or read from a rules
    file, matches nothing; the other rules still map the event."""
    document = {
        "rules": [
            {
                "id": "uncompilable",
                "when": {"source": "github", "summary_regex": r"\b{start}"},
                "map_to": {"CE": ["first"]},
            },
            {
                "id": "ordinary",
                "when": {"source": "github", "summary_regex": "push"},
                "map_to": {"CE": ["second"]},
            },
        ]
    }
    event = {"source": "github", "summary": "push to main"}
    # The shared parser refuses the pattern, so the rule is skipped.
    assert [rule.id for rule in parse_rules(document)] == ["ordinary"]
    # A rule that reached matching anyway is contained to itself.
    monkeypatch.setattr(regex_boundary, "validate_pattern", lambda pattern: None)
    saved_earlier = parse_rules(document)
    monkeypatch.undo()
    assert [rule.id for rule in saved_earlier] == ["uncompilable", "ordinary"]
    with caplog.at_level("WARNING", logger="app.mapping.rules"):
        assert evaluate_by_framework(event, saved_earlier) == {"CE": ["second"]}
    assert "uncompilable has a pattern that exceeded its time budget or cannot be" in (
        caplog.text
    )
    # The source a rule reads follows the same reading.
    assert reads_source({"source_regex": r"\b{start}"}, "github") is False
    assert reads_source({"source_regex": "^git"}, "github") is True
    assert reads_source({}, "github") is True


def test_the_rule_editor_refuses_a_pattern_the_matching_engine_cannot_compile():
    with pytest.raises(DomainError) as refused:
        _validate_regex(r"\b{start}", "summary_regex")
    assert (refused.value.status_code, refused.value.detail) == (
        400,
        "summary_regex: Invalid pattern: expected } at position 4",
    )
