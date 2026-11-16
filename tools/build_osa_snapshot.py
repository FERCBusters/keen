"""Build Keen's frozen snapshot of Open Security Architecture's crosswalks.

    python tools/build_osa_snapshot.py OSA_DATA_CHECKOUT RETRIEVED OUT \
        [--previous PREVIOUS_SNAPSHOT]

``OSA_DATA_CHECKOUT`` is a clone of
https://github.com/opensecurityarchitecture/osa-data, ``RETRIEVED`` the date of
the snapshot (YYYY-MM-DD) and ``OUT`` the file to write, by convention
``services/api/alembic/catalogues/osa-<RETRIEVED>.json``. A migration then
applies the snapshot; see ``0056_osa_snapshot_2026_11``.

OSA publishes no prose description of a framework. One that an earlier
snapshot carried is kept from ``--previous``; a framework new to Keen is
described by OSA's own note on how it built the crosswalk.

OSA's data is CC BY-SA 4.0. Record the commit in
``alembic/catalogues/OSA-NOTICE.txt`` when a new snapshot is shipped.
"""

import argparse
import json
import subprocess
from pathlib import Path

CLAUSE_FIELDS = ("id", "title", "controls", "rationale", "gaps", "coverage_pct")


def build(checkout: Path, retrieved: str, previous: Path | None) -> dict:
    described = {}
    if previous:
        described = {
            framework["id"]: framework["description"]
            for framework in json.loads(previous.read_text())["frameworks"]
        }
    frameworks = []
    for path in sorted((checkout / "data/framework-coverage").glob("*.json")):
        source = json.loads(path.read_text())
        slug = source["framework_id"]
        frameworks.append(
            {
                "id": slug,
                "slug": path.stem,
                "name": source["framework_name"],
                "description": described.get(slug) or source["metadata"]["disclaimer"],
                "metadata": source["metadata"],
                "clauses": [
                    {key: clause[key] for key in CLAUSE_FIELDS if key in clause}
                    for clause in source["clauses"]
                ],
            }
        )
    commit = subprocess.run(  # noqa: S603 - fixed command, the caller's own checkout
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "source": "https://www.opensecurityarchitecture.org/",
        "license": "CC-BY-SA-4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "retrieved": retrieved,
        "source_commit": commit,
        "frameworks": frameworks,
    }


def render(snapshot: dict) -> str:
    """JSON with one clause to a line, so a later snapshot diffs clause by clause."""
    head = {key: value for key, value in snapshot.items() if key != "frameworks"}
    lines = [json.dumps(head, ensure_ascii=False)[:-1] + ', "frameworks": [']
    frameworks = snapshot["frameworks"]
    for position, framework in enumerate(frameworks):
        about = {key: value for key, value in framework.items() if key != "clauses"}
        lines.append(json.dumps(about, ensure_ascii=False)[:-1] + ', "clauses": [')
        clauses = framework["clauses"]
        lines.extend(
            json.dumps(clause, ensure_ascii=False)
            + ("," if index < len(clauses) - 1 else "")
            for index, clause in enumerate(clauses)
        )
        lines.append("]}" + ("," if position < len(frameworks) - 1 else ""))
    lines.append("]}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checkout", type=Path)
    parser.add_argument("retrieved")
    parser.add_argument("out", type=Path)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    args.out.write_text(render(build(args.checkout, args.retrieved, args.previous)))


if __name__ == "__main__":
    main()
