"""Real PostgreSQL concurrency regression. Runs in the normal GitHub CI database."""
from app.core.datetime_utils import utc_now_naive
import os
import threading
import time
import uuid
from datetime import datetime
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text, MetaData, DefaultClause
from sqlalchemy.orm import sessionmaker
from tests.db_helpers import schema_engine
from app.db.models import Base, Event, SourceIngestionState
from app.services import evidence_retention as retention
from app.services.ingestion_pause import is_paused, require_receiving


@pytest.fixture
def database():
    url=os.environ.get('KEEN_RETENTION_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set KEEN_RETENTION_TEST_DATABASE_URL for real PostgreSQL concurrency tests')
    schema='purge_coord_'+uuid.uuid4().hex
    admin=create_engine(url)
    with admin.begin() as c:c.execute(text(f'CREATE SCHEMA {schema}'))
    engine=schema_engine(url,schema)
    metadata=MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    for table in metadata.tables.values():
        for column in table.columns:
            if column.server_default is not None and str(column.server_default.arg) == "now() AT TIME ZONE 'UTC'":
                column.server_default=DefaultClause(text("(now() AT TIME ZONE 'UTC')"))
    metadata.create_all(engine)
    factory=sessionmaker(bind=engine)
    with engine.begin() as c:
        c.execute(text("INSERT INTO evidence_retention_policy (id,mode) VALUES (1,'disabled')"))
    with patch.object(retention,'SessionLocal',factory),patch.object(retention,'clear_stats_caches'):
        yield engine,factory
    engine.dispose()
    with admin.begin() as c:c.execute(text(f'DROP SCHEMA {schema} CASCADE'))
    admin.dispose()


def test_purge_drains_transactions_rejects_writes_and_preserves_manual_pause(database):
    engine,factory=database
    with factory() as db:
        db.add(Event(source='test',external_id='before',timestamp=utc_now_naive(),summary='Before purge'))
        db.add(SourceIngestionState(source='rss',paused=True))
        db.commit()
        retention.enqueue(db,'all',None,False,'test');db.commit()
    with factory() as db:
        assert is_paused(db,'keen-agent')
        with pytest.raises(HTTPException):require_receiving(db,'webhooks')
        db.add(Event(source='test',external_id='during',timestamp=utc_now_naive(),summary='Must not arrive'))
        with pytest.raises(HTTPException):db.flush()
        db.rollback()
    held=factory()
    held.execute(text('SELECT 1'))  # Holds the ordinary transaction's shared lease.
    finished=threading.Event();started=threading.Event();errors=[]
    def purge():
        started.set()
        try:retention.database_batch()
        except Exception as error:errors.append(error)
        finally:finished.set()
    worker=threading.Thread(target=purge,daemon=True);worker.start()
    try:
        assert started.wait(2)
        time.sleep(.15)
        assert not finished.is_set(), 'Purge must wait for the active transaction'
    finally:
        held.rollback();held.close()
        worker.join(8)
    assert finished.is_set() and not errors
    assert not retention.database_batch()  # Empty batch marks completion.
    with factory() as db:
        assert db.query(Event).count()==0
        assert is_paused(db,'keen-agent')  # Completion starts a full cooldown.
        assert db.get(SourceIngestionState,'rss').paused
        db.execute(text("UPDATE evidence_purge_jobs SET updated_at=clock_timestamp() AT TIME ZONE 'UTC' - interval '6 minutes'"))
        db.commit()
        assert not is_paused(db,'keen-agent')
        assert is_paused(db,'rss')


def test_cancellation_also_cools_down(database):
    _,factory=database
    with factory() as db:
        retention.enqueue(db,'all',None,False,'test');db.commit()
        db.execute(text("UPDATE evidence_purge_jobs SET status='cancelled',updated_at=clock_timestamp() AT TIME ZONE 'UTC'"));db.commit()
        assert is_paused(db,'webhooks')
