"""The source list behind source badges and colour preferences.

Sources are found with one index probe per source, so the listing has to name
each source once, in order, however many events it has, and include the diary
source when any of its entries is visible to the caller.
"""

import uuid
from datetime import datetime

import pytest
from app.core.config import settings
from app.db.models import Event, Permission, User
from app.services import sources as sources_service


@pytest.fixture
def listing(contract_db, monkeypatch):
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 0)
    db = contract_db

    def event(source, n, **columns):
        db.add(
            Event(
                source=source,
                summary=f"{source} {n}",
                external_id=columns.pop("external_id", f"{source}-{n}"),
                timestamp=datetime(2026, 3, 1),
                **columns,
            )
        )

    for source, count in (("loki", 30), ("ci", 3), ("zabbix", 1), ("gitlab", 12)):
        for n in range(count):
            event(source, n)
    event(
        "diary",
        "risk",
        system="isms",
        external_id=f"isms-link:risk:{uuid.uuid4()}",
    )
    db.commit()

    def user(*codes, active=True):
        row = User(
            id=uuid.uuid4(),
            username=f"meta-{uuid.uuid4().hex[:8]}",
            password_hash="unused",
            role="normal",
            is_active=active,
        )
        db.add(row)
        for code in codes:
            permission = db.query(Permission).filter_by(code=code).one_or_none()
            if permission is None:
                permission = Permission(code=code, description=code)
                db.add(permission)
            row.permissions.append(permission)
        db.commit()
        return row

    def sources(row):
        # Display labels and colours are covered with the assembled application.
        return sources_service._visible_sources(db, row)

    return db, event, user, sources


def test_each_source_is_listed_once_in_order(listing):
    _, _, user, sources = listing
    risk_reader = user("events.read", "risk.read")
    assert sources(risk_reader) == ["ci", "diary", "gitlab", "loki", "zabbix"]


def test_the_diary_source_follows_the_callers_visible_entries(listing):
    db, event, user, sources = listing
    reader = user("events.read")
    inactive = user("events.read", "risk.read", active=False)
    # Only a risk link entry exists, which this reader cannot open.
    assert sources(reader) == ["ci", "gitlab", "loki", "zabbix"]
    # A manual diary entry is visible to every active reader.
    event("diary", "manual", system="diary")
    db.commit()
    assert sources(reader) == ["ci", "diary", "gitlab", "loki", "zabbix"]
    assert sources(inactive) == ["ci", "gitlab", "loki", "zabbix"]
