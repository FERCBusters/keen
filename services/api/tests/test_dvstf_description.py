"""The description migration preserves local edits and other frameworks."""

import importlib.util
from pathlib import Path

import pytest
from app.db.models import Framework

from tests.test_assembled_authority_regressions import keen as keen


@pytest.mark.parametrize("existing", [None, "", "Our local description"])
def test_description_upgrade_and_downgrade(keen, monkeypatch, existing):
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/0024_dvstf_description.py"
    )
    spec = importlib.util.spec_from_file_location("dvstf_description", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    db = keen.db
    row = db.query(Framework).filter_by(slug="UK-DVSTF:1.0").one_or_none()
    if row is None:
        row = Framework(slug="UK-DVSTF:1.0", name="UK DVSTF")
        db.add(row)
    row.description = existing
    other = Framework(
        slug="description-test-other", name="Other", description="Keep me"
    )
    db.add(other)
    db.flush()
    monkeypatch.setattr(migration.op, "get_bind", db.connection)
    migration.upgrade()
    migration.upgrade()
    db.expire_all()
    assert row.description == (existing or migration.DESCRIPTION)
    assert other.description == "Keep me"
    migration.downgrade()
    db.expire_all()
    assert row.description == (existing or None)
    row.description = "Edited after upgrade"
    db.flush()
    migration.downgrade()
    db.expire_all()
    assert row.description == "Edited after upgrade"
