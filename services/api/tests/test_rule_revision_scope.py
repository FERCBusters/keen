"""Rule history isolates edits, restoration and reused rule IDs."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from app.api.routes.managed_configurations import (
    RestoreInput,
    list_rule_revisions,
    restore_rule_revision,
)
from app.db.models import (
    ControlItem,
    ManagedConfiguration,
    ManagedConfigurationRevision,
)
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tests.db_helpers import create_sqlite_schema


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    create_sqlite_schema(engine)
    with Session(engine) as session:
        session.add(
            ControlItem(framework_slug="TEST", type="custom", ref="1", title="Test")
        )
        session.commit()
        yield session
    engine.dispose()


def rule(identifier, description="Original"):
    return dict(
        id=identifier,
        description=description,
        when={"source": "jenkins"},
        map_to=[{"framework": "TEST", "ref": "1"}],
        enabled=True,
        confidence=0.8,
    )


def snapshot(db, version, rules):
    document = {"rules": deepcopy(rules)}
    row = db.get(ManagedConfiguration, "rules")
    if row:
        row.document = document
        row.version = version
    else:
        db.add(ManagedConfiguration(name="rules", version=version, document=document))
    db.add(
        ManagedConfigurationRevision(name="rules", version=version, document=document)
    )
    db.commit()


def test_new_rule_at_global_revision_97_has_one_revision(db):
    for version in range(1, 97):
        snapshot(db, version, [rule("other", str(version))])
    snapshot(db, 97, [rule("other", "96"), rule("new")])
    history = list_rule_revisions("new", db)
    assert history["total"] == 1
    assert history["items"][0]["revision"] == 1
    assert history["items"][0]["version"] == 97
    snapshot(db, 98, [rule("other", "Changed"), rule("new")])
    assert list_rule_revisions("new", db)["total"] == 1


def test_restore_changes_only_selected_rule_and_checks_conflicts(db):
    snapshot(db, 1, [rule("a"), rule("b")])
    snapshot(db, 2, [rule("a", "Changed A"), rule("b")])
    snapshot(db, 3, [rule("a", "Changed A"), rule("b", "Keep B")])
    user = SimpleNamespace(id=None)
    with pytest.raises(HTTPException) as exc:
        restore_rule_revision("a", 1, RestoreInput(version=2), user, db)
    assert exc.value.status_code == 409
    db.rollback()
    restore_rule_revision("a", 1, RestoreInput(version=3), user, db)
    saved = db.get(ManagedConfiguration, "rules")
    assert saved.version == 4
    assert {r["id"]: r["description"] for r in saved.document["rules"]} == {
        "a": "Original",
        "b": "Keep B",
    }
    assert list_rule_revisions("a", db)["total"] == 3
    assert list_rule_revisions("b", db)["total"] == 2


def test_unrelated_snapshot_cannot_be_restored_as_rule_revision(db):
    snapshot(db, 1, [rule("a")])
    snapshot(db, 2, [rule("a"), rule("b")])
    for identifier, version in [("b", 1), ("a", 2)]:
        with pytest.raises(HTTPException) as exc:
            restore_rule_revision(
                identifier,
                version,
                RestoreInput(version=2),
                SimpleNamespace(id=None),
                db,
            )
        assert exc.value.status_code == 404


def test_delete_then_reuse_id_starts_new_history(db):
    snapshot(db, 1, [rule("a", "Prior lifetime")])
    snapshot(db, 2, [])
    snapshot(db, 3, [rule("a", "New lifetime")])
    assert [
        (r["revision"], r["version"]) for r in list_rule_revisions("a", db)["items"]
    ] == [(1, 3)]
    with pytest.raises(HTTPException) as exc:
        restore_rule_revision(
            "a", 1, RestoreInput(version=3), SimpleNamespace(id=None), db
        )
    assert exc.value.status_code == 404


def test_noop_and_default_normalization_do_not_increment_rule_revision(db):
    first = rule("a")
    first.pop("confidence")
    first.pop("enabled")
    snapshot(db, 1, [first])
    snapshot(db, 2, [rule("a")])
    assert list_rule_revisions("a", db)["total"] == 1
