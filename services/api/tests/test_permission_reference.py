"""The Help permission reference and the starting groups name real permissions.

Help's table is what an administrator reads before granting a permission, and
migration 0049 grants permissions to the starting groups. Both must keep to the
permissions the API checks, which tools/security_e2e/access_policy.py declares
and tests/test_access_policy_inventory.py holds to the routes.
"""

import importlib.util
import re
from pathlib import Path

from tests.test_access_policy_inventory import _policy

ROOT = Path(__file__).resolve().parents[3]
HELP = ROOT / "services/ui/public/help/administrator-identity.html"
MIGRATION = ROOT / "services/api/alembic/versions/0049_default_groups.py"


def _section(start, end):
    page = HELP.read_text(encoding="utf-8")
    return page[page.index(start) : page.index(end)]


def _rows(section):
    table = section[section.index("<tbody>") : section.index("</tbody>")]
    return [re.findall(r"<td>(.*?)</td>", row, re.S) for row in table.split("<tr>")[1:]]


def _starting_groups():
    spec = importlib.util.spec_from_file_location("keen_default_groups", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.GROUPS


def test_help_lists_exactly_the_permissions_the_api_checks():
    rows = _rows(_section('id="permission-reference"', 'id="starting-groups"'))
    listed = [re.fullmatch(r"<code>([a-z.]+)</code>", row[0]).group(1) for row in rows]
    assert sorted(listed) == sorted(_policy().PERMISSIONS)
    assert len(set(listed)) == len(listed)
    # Every row says what the permission reads and what it changes.
    assert all(len(row) == 3 and row[1].strip() and row[2].strip() for row in rows)
    # Each implied permission is named in the row of the permission that implies it.
    by_code = dict(zip(listed, rows, strict=True))
    for code, implied in _policy().IMPLIED_PERMISSIONS.items():
        for other in implied:
            assert f"<code>{other}</code>" in by_code[code][1], (code, other)


def test_starting_groups_hold_real_permissions_and_help_describes_them():
    known = set(_policy().PERMISSIONS)
    rows = _rows(
        _section('id="starting-groups"', 'id="security-notification-delivery"')
    )
    described = {row[0]: re.findall(r"<code>([a-z.]+)</code>", row[1]) for row in rows}
    groups = {name: list(codes) for name, _, codes in _starting_groups()}
    assert described == groups
    for name, codes in groups.items():
        assert set(codes) <= known and len(set(codes)) == len(codes), name
    writes = {
        name: {c for c in codes if not c.endswith(".read")}
        for name, codes in groups.items()
    }
    # Staff change nothing; external auditors change audits and nothing else.
    assert writes["Staff"] == set()
    assert writes["External auditors"] == {"audits.manage"}
    # stories.read comments on stories, so the two read-only groups go without it.
    assert "stories.read" not in groups["Staff"] + groups["External auditors"]
    # Internal auditors reach every permission, directly or through one that
    # includes it, and no group sets the administrator role (the migration
    # inserts no role).
    reach = set(groups["Internal auditors"])
    covers = {
        "isms.manage": {"isms.read"},
        "risk.manage": {"risk.read"},
        "pestle.manage": {"pestle.read"},
        "audits.admin": {"audits.manage", "audits.read"},
        "stories.admin": {"stories.manage", "stories.read"},
        "quiz.manage": {"quiz.read"},
    }
    for code in list(reach):
        reach |= covers.get(code, set())
    assert reach == known
    assert (
        "role"
        not in MIGRATION.read_text()
        .split("def upgrade")[1]
        .split("INSERT INTO groups")[1][:80]
    )
