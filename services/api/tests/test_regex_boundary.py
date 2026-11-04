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
from pathlib import Path

import pytest
import regex
from app.security.redaction import mask_event_data_str, redact_bytes, redact_str
from app.security.regex import SCAN_REPEATS_HELD, check_scan_pattern, scan_pattern

APP = Path(__file__).resolve().parents[1] / "app"
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
        where = path.relative_to(root)
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
        where = path.relative_to(root)
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
        with pytest.raises(ValueError):
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
