"""Purge gates and transaction lock order; run SQL cases against PostgreSQL too."""
from datetime import datetime, timedelta
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from app.services.ingestion_pause import purge_pause_status, require_purge_receiving, is_paused
from app.db.session import _coordinate_evidence_purge


@pytest.mark.parametrize('active,offset,paused',[(True,None,True),(False,299,True),(False,-1,False),(False,None,False)])
def test_active_job_and_cooldown_gate(active,offset,paused):
    now=datetime(2026,10,8)
    db=Mock();db.get_bind.return_value.dialect.name='postgresql'
    db.execute.return_value.mappings.return_value.one.return_value={
        'active':active,'resume_at':now+timedelta(seconds=offset) if offset is not None else None,'current_time':now}
    assert purge_pause_status(db)['paused'] is paused
    if paused:
        assert is_paused(db,'rss')
        db.scalar.assert_not_called()
        with pytest.raises(HTTPException) as exc:require_purge_receiving(db)
        assert exc.value.status_code==503 and exc.value.headers['Retry-After']=='60'
    else:
        require_purge_receiving(db)


def test_reader_shared_and_purge_exclusive_lock_before_any_application_sql():
    connection=Mock();connection.dialect.name='postgresql'
    session=Mock();session.info={}
    transaction=Mock();transaction.nested=False
    _coordinate_evidence_purge(session,transaction,connection)
    assert 'pg_advisory_xact_lock_shared' in str(connection.execute.call_args.args[0])
    connection.reset_mock();session.info={'evidence_purge_batch':True}
    _coordinate_evidence_purge(session,transaction,connection)
    calls=connection.execute.call_args_list
    assert 'lock_timeout' in str(calls[0].args[0])
    assert 'pg_advisory_xact_lock(' in str(calls[1].args[0])
    connection.reset_mock();transaction.nested=True
    _coordinate_evidence_purge(session,transaction,connection)
    connection.execute.assert_not_called()
