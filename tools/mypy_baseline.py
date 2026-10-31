#!/usr/bin/env python3
"""Hold the API's type-checking findings at or below a reviewed baseline.

Usage, from services/api:
  poetry run python ../../tools/mypy_baseline.py check
  poetry run python ../../tools/mypy_baseline.py update

mypy checks services/api/app with the settings in services/api/pyproject.toml.
Code written before the type checker joined CI still has findings, and
services/api/mypy-baseline.txt lists them by file and message. Line numbers
are left out, so moving code does not change the baseline.

check   exits 1 when a file has a finding the baseline does not list, and when
        the baseline lists a finding that is gone, so each fix is recorded and
        the count only falls.
update  rewrites the baseline from the current findings. Run it after fixing
        findings, and review the diff: the baseline should only shrink.

Both commands exit 2 when mypy itself fails to run.
"""

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

API = Path(__file__).resolve().parents[1] / "services" / "api"
BASELINE = API / "mypy-baseline.txt"
FINDING = re.compile(r"^(?P<path>[^:\s]+\.py):\d+(?::\d+)?: error: (?P<message>.+)$")
LINE_REFERENCE = re.compile(r"\bline \d+\b")


def findings() -> Counter[str]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--no-error-summary",
            "--no-pretty",
            "--hide-error-context",
            "--show-error-codes",
        ],
        cwd=API,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(2)
    found: Counter[str] = Counter()
    for line in result.stdout.splitlines():
        match = FINDING.match(line)
        if match:
            message = LINE_REFERENCE.sub("line N", match["message"])
            found[f"{match['path']}: {message}"] += 1
    if result.returncode == 1 and not found:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(2)
    return found


def recorded() -> Counter[str]:
    if not BASELINE.exists():
        return Counter()
    lines = BASELINE.read_text(encoding="utf-8").splitlines()
    return Counter(line for line in lines if line and not line.startswith("#"))


def write(found: Counter[str]) -> None:
    header = (
        "# mypy findings that predate the type check in CI; see\n"
        "# tools/mypy_baseline.py. Fix entries and run its update command.\n"
    )
    body = "".join(f"{line}\n" for line in sorted(found.elements()))
    BASELINE.write_text(header + body, encoding="utf-8")


def check() -> int:
    found, baseline = findings(), recorded()
    new, fixed = found - baseline, baseline - found
    for line in sorted(new.elements()):
        print(f"new type finding: {line}")
    for line in sorted(fixed.elements()):
        print(f"fixed, still in the baseline: {line}")
    if fixed:
        print(
            "Record the fixes with: poetry run python ../../tools/mypy_baseline.py update"
        )
    if new or fixed:
        return 1
    print(f"mypy: {sum(found.values())} findings, all in the baseline")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "update"))
    args = parser.parse_args(argv)
    if args.command == "update":
        found = findings()
        write(found)
        print(f"mypy baseline: {sum(found.values())} findings")
        return 0
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
