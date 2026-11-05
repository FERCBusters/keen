"""The scheduled and requested cloud posture and activity worker tasks.

A scheduled task reads every provider; a requested one reads the provider it
names. Each runs the real cloud runner against the test database, with every
provider switched off or paused, so no vendor is called.
"""

import pytest
from app.core.config import settings
from app.db.models import SourceIngestionState
from app.ingest.cloud import KEYS
from app.ingest.cloud import provider as cloud_provider
from app.worker import tasks
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def worker_db(contract_db, monkeypatch):
    factory = sessionmaker(
        bind=contract_db.get_bind(), join_transaction_mode="create_savepoint"
    )
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(settings, "demo_mode", False)
    for key in KEYS:
        monkeypatch.setattr(settings, cloud_provider(key).setting, False)
    return contract_db


def test_the_scheduled_posture_refresh_reads_every_provider(worker_db):
    assert tasks.cloud_posture_refresh_task() == [
        {"provider": key, "skipped": True, "reason": "disabled"} for key in KEYS
    ]


def test_a_requested_posture_refresh_reads_only_the_named_provider(
    worker_db, monkeypatch
):
    monkeypatch.setattr(settings, cloud_provider("aws").setting, True)
    worker_db.add(SourceIngestionState(source="aws", paused=True))
    worker_db.commit()

    assert tasks.cloud_posture_refresh_task("aws") == [
        {"provider": "aws", "skipped": True, "reason": "paused"}
    ]


def _skipped_activity(key):
    reason = "no activity" if cloud_provider(key).activity is None else "disabled"
    return {"provider": key, "skipped": True, "reason": reason}


def test_the_scheduled_activity_read_covers_every_provider(worker_db):
    assert tasks.cloud_activity_task() == [_skipped_activity(key) for key in KEYS]


def test_a_requested_activity_read_reads_only_the_named_provider(worker_db):
    assert tasks.cloud_activity_task("aws") == [_skipped_activity("aws")]
