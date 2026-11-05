"""The application is measured whole: no line or branch is excluded from coverage.

tests.sh requires full statement and branch coverage, and an exclusion would
meet that without a test. coverage.py skips lines marked ``# pragma: no cover``
and branches marked ``# pragma: no branch`` by default, and its configuration
can add patterns or omit files.
"""

import re
import tomllib
from pathlib import Path

API = Path(__file__).resolve().parents[1]
EXCLUSION = re.compile(r"#\s*pragma:\s*no\s*(?:cover|branch)", re.IGNORECASE)


def exclusions(source: str) -> int:
    return len(EXCLUSION.findall(source))


def test_no_application_line_is_excluded_from_coverage():
    found = {
        path.relative_to(API).as_posix(): count
        for path in sorted((API / "app").rglob("*.py"))
        if (count := exclusions(path.read_text()))
    }
    assert found == {}


def test_no_configuration_excludes_code_from_coverage():
    pyproject = tomllib.loads((API / "pyproject.toml").read_text())
    assert "coverage" not in pyproject["tool"]
    assert [
        name
        for name in (".coveragerc", "setup.cfg", "tox.ini")
        if (API / name).exists()
    ] == []


def test_the_check_finds_an_exclusion():
    source = (
        "if found is None:  # pragma: no cover - the index guarantees one\n"
        "    raise LookupError\n"
        "for row in rows:  # Pragma: No Branch\n"
        "    pass\n"
    )
    assert exclusions(source) == 2
