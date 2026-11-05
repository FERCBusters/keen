"""The README badges: coverage, mypy and ruff, rendered from KEEN CI's reports.

tools/coverage_badges.py reads the reports from a downloaded CI artifact, so
these tests build such a directory and check what each badge says, and hold
the CI workflow to writing the reports the badges read.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CLEAN_MYPY = "Success: no issues found in 315 source files\n"


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "coverage_badges", ROOT / "tools" / "coverage_badges.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


badges = _load_tool()


def results(tmp_path, *, mypy=CLEAN_MYPY, ruff="[]"):
    """A CI artifact laid out as the upload step writes it."""
    root = tmp_path / "results" / "test-results"
    (root / "frontend-coverage").mkdir(parents=True)
    (root / "backend-coverage.json").write_text(
        json.dumps({"totals": {"percent_covered": 98.33}})
    )
    (root / "frontend-coverage" / "coverage-summary.json").write_text(
        json.dumps({"total": {"lines": {"pct": 99.16}}})
    )
    (root / "mypy-report.txt").write_text(mypy)
    (root / "ruff-findings.json").write_text(ruff)
    return tmp_path / "results"


def rendered(tmp_path, **reports):
    out = tmp_path / "badges"
    badges.main(["coverage_badges.py", str(results(tmp_path, **reports)), str(out)])
    return {path.name: path.read_text() for path in sorted(out.glob("*.svg"))}


def test_a_clean_run_renders_four_passing_badges(tmp_path):
    svgs = rendered(tmp_path)
    assert sorted(svgs) == [
        "coverage-api.svg",
        "coverage-ui.svg",
        "mypy.svg",
        "ruff.svg",
    ]
    assert 'aria-label="API coverage: 98%"' in svgs["coverage-api.svg"]
    assert 'aria-label="UI coverage: 99%"' in svgs["coverage-ui.svg"]
    assert 'aria-label="mypy: 0 errors"' in svgs["mypy.svg"]
    assert 'aria-label="ruff: 0 issues"' in svgs["ruff.svg"]
    assert 'fill="#4c1"' in svgs["mypy.svg"] and 'fill="#4c1"' in svgs["ruff.svg"]


def test_findings_are_counted_and_shown_as_failing(tmp_path):
    mypy = (
        "app/a.py:3: error: Incompatible return value type  [return-value]\n"
        "app/b.py:9: error: Name 'x' is not defined  [name-defined]\n"
        "Found 2 errors in 2 files (checked 315 source files)\n"
    )
    ruff = json.dumps([{"code": "F401"}])
    svgs = rendered(tmp_path, mypy=mypy, ruff=ruff)
    assert 'aria-label="mypy: 2 errors"' in svgs["mypy.svg"]
    assert 'aria-label="ruff: 1 issue"' in svgs["ruff.svg"]
    assert 'fill="#e05d44"' in svgs["mypy.svg"] and 'fill="#e05d44"' in svgs["ruff.svg"]


@pytest.mark.parametrize(
    "summary,errors",
    [
        ("Success: no issues found in 1 source file", 0),
        ("Found 1 error in 1 file (checked 1 source file)", 1),
        ("Found 12 errors in 3 files (checked 315 source files)", 12),
    ],
)
def test_the_mypy_count_comes_from_its_summary_line(summary, errors):
    assert badges.mypy_errors("app/x.py:1: note: detail\n" + summary + "\n\n") == errors


@pytest.mark.parametrize(
    "reports,refusal",
    [
        ({"mypy": ""}, "without a summary"),
        ({"mypy": "app/x.py:1: error: boom  [misc]\n"}, "without a summary"),
        ({"ruff": '{"findings": []}'}, "not a list"),
    ],
)
def test_a_report_the_badges_cannot_read_stops_the_publish(tmp_path, reports, refusal):
    with pytest.raises(SystemExit, match=refusal):
        rendered(tmp_path, **reports)


def test_a_missing_report_stops_the_publish(tmp_path):
    root = results(tmp_path)
    next(root.rglob("mypy-report.txt")).unlink()
    with pytest.raises(SystemExit, match="mypy-report.txt"):
        badges.main(["coverage_badges.py", str(root), str(tmp_path / "out")])


def test_ci_writes_the_reports_the_badges_read():
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
    assert "--output-file ../../test-results/ruff-findings.json" in workflow
    assert "poetry run mypy | tee ../../test-results/mypy-report.txt" in workflow
    # The type check's pipe keeps mypy's exit status.
    types = workflow.split("- name: Check Python types", 1)[1].split("- name:", 1)[0]
    assert "set -o pipefail" in types
