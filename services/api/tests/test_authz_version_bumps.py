"""Changes to who holds what invalidate permissions through one helper.

mosp.security.permissions.bump_authz_versions raises each affected account's
authorisation version, which makes its sessions recompute their permissions
on their next request. A second copy of that update could drift, so the
application raises the version only there.
"""

import ast

from tests.source_tree import SOURCES, name_in

APP = SOURCES


def version_raises(source: str) -> list[int]:
    """Lines that raise an authorisation version, as an update or in place."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Add)
            and isinstance(node.left, ast.Attribute)
            and node.left.attr == "authz_version"
        ):
            found.append(node.lineno)
        if (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Attribute)
            and node.target.attr == "authz_version"
        ):
            found.append(node.lineno)
    return sorted(found)


def test_only_the_shared_helper_raises_authorisation_versions():
    found = {
        name_in(path, APP).as_posix(): lines
        for path in sorted(APP.rglob("*.py"))
        if (lines := version_raises(path.read_text()))
    }
    assert {path: len(lines) for path, lines in found.items()} == {
        "security/permissions.py": 1
    }


def test_the_check_finds_a_hand_rolled_version_raise():
    source = (
        "db.query(User).update({User.authz_version: User.authz_version + 1})\n"
        "user.authz_version += 1\n"
        "snapshot = user.authz_version\n"
    )
    assert version_raises(source) == [1, 2]
