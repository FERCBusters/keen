"""Operator pause controls, independent of environment enablement and cursors."""
from functools import wraps
from fastapi import HTTPException
from sqlalchemy import select
from app.db.models import SourceIngestionState

POLLING_SOURCES = frozenset(('loki', 'cloudwatch_logs', 'github', 'forgejo', 'gitea',
    'gitlab', 'redmine', 'jenkins', 'taiga', 'bookstack', 'rss', 'google_workspace'))
PAUSABLE_SOURCES = POLLING_SOURCES | {'webhooks', 'keen-agent', 'api-ingesters'}


def is_paused(db, source):
    return db.scalar(select(SourceIngestionState.paused).where(
        SourceIngestionState.source == source)) is True


def require_receiving(db, source):
    if is_paused(db, source):
        raise HTTPException(503, 'Source ingestion is paused by an administrator; retry later.',
                            headers={'Retry-After': '60'})


def pausable(source):
    """Gate both scheduled and manually invoked adapter runs before any I/O."""
    def decorate(function):
        @wraps(function)
        def wrapped(db, *args, **kwargs):
            if db is not None and is_paused(db, source):
                return [{'source': source, 'skipped': True, 'paused': True,
                         'reason': 'Ingestion paused by an administrator'}]
            return function(db, *args, **kwargs)
        return wrapped
    return decorate
