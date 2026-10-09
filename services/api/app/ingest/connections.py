"""Connection-scoped adapter configuration and trusted evidence identity.

Context variables isolate concurrent runs without mutating process-wide settings.
Only entry points establish a scope; upstream event payloads cannot choose it.
"""
from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace, field
from functools import wraps
import hashlib
import os
from pathlib import Path
import re
import yaml
from sqlalchemy import select, text
from app.core.config import settings as deployment_settings
from app.core.managed_configuration import load_document as deployment_document
from app.db.models import SourceConnection
from app.integrations.transport import decrypt

# Public endpoint/account fields and secret fields are deliberately enumerated.
FIELDS = {
    'webhooks': ('provider secret_header', 'secret'),
    'loki': ('base_url username header_name', 'password header_value'),
    'github': ('base_url username', 'token'),
    'gitlab': ('base_url', 'token'),
    'forgejo': ('base_url username auth_mode', 'token cookie'),
    'gitea': ('base_url username auth_mode', 'token cookie'),
    'jenkins': ('base_url username', 'api_token'),
    'redmine': ('base_url', 'api_key'),
    'riskledger': ('', 'api_key'),
    'bookstack': ('base_url', 'token_id token_secret'),
    'taiga': ('base_url username', 'token password'),
    'rss': ('base_url username header_name user_agent', 'password header_value'),
    'google_workspace': ('impersonate', 'sa_json'),
    'cloudwatch_logs': ('region', 'aws_access_key_id aws_secret_access_key aws_session_token'),
}
_current = ContextVar('ingestion_connection', default=None)

@dataclass(frozen=True)
class Connection:
    id: str
    source: str
    name: str
    configuration: dict
    credentials: dict = field(repr=False)
    inputs: dict | None
    enabled: bool = True
    managed_by: str = 'database'
    encrypted_credentials: str | None = field(default=None, repr=False)
    credential_references: dict | None = field(default=None, repr=False)

@contextmanager
def connection_scope(connection):
    token = _current.set(connection)
    try:
        yield connection
    finally:
        _current.reset(token)

def current_connection():
    return _current.get()

def identity():
    connection = _current.get()
    return ({'connection_id': connection.id, 'connection_name': connection.name}
            if connection else {})

def namespace(value):
    connection = _current.get()
    if connection is None:
        return value
    return connection.id + ':' + hashlib.sha256(str(value).encode()).hexdigest()[:32]

def artifact_key(value):
    connection = _current.get()
    return f'connections/{connection.id}/{value}' if connection else value

class ScopedSettings:
    def __setattr__(self, key, value):
        setattr(deployment_settings, key, value)

    def __delattr__(self, key):
        delattr(deployment_settings, key)

    def __getattr__(self, key):
        connection = _current.get()
        if connection and connection.managed_by != 'environment' and key.startswith(connection.source + '_'):
            field = key[len(connection.source) + 1:]
            public, secret = FIELDS[connection.source]
            if field in (public + ' ' + secret).split():
                return connection.credentials.get(field, connection.configuration.get(field, ''))
            # Never inherit a different Google account's file/base64 credentials.
            if connection.source == 'google_workspace' and field in ('sa_keyfile', 'sa_json_b64'):
                return ''
        return getattr(deployment_settings, key)

settings = ScopedSettings()

def load_document(name, path, *, db=None):
    connection = _current.get()
    if connection and name == connection.source and connection.inputs is not None:
        return deepcopy(connection.inputs)
    return deployment_document(name, path, db=db)

def _secret_reference(value):
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError('Credentials in the connections file require an env or file reference')
    if 'env' in value:
        result = os.environ.get(value['env'])
        if result is None:
            raise ValueError('A connection credential environment variable is missing')
        return result
    if 'file' in value:
        return Path(value['file']).read_text().strip()
    raise ValueError('Unknown credential reference')

def connections(db, source=None):
    sources = [source] if source else list(FIELDS)
    result = [Connection('env:' + s, s, 'Environment default', {}, {}, None,
                        bool(getattr(deployment_settings, s + '_enabled', False)), 'environment')
              for s in sources]
    path = os.environ.get('KEEN_SOURCE_CONNECTIONS_FILE')
    if path:
        document = yaml.safe_load(Path(path).read_text()) or {}
        for entry in document.get('connections', []):
            if entry.get('source') not in sources:
                continue
            public, secret = (set(v.split()) for v in FIELDS[entry['source']])
            if set(entry.get('configuration', {})) - public or set(entry.get('credentials', {})) - secret:
                raise ValueError('Unsupported field in source connections file')
            key = str(entry.get('id', ''))
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', key):
                raise ValueError('Connection file IDs must be stable alphanumeric identifiers')
            result.append(Connection('file:' + key, entry['source'], entry['name'],
                entry.get('configuration', {}),
                {}, entry.get('inputs', {}), entry.get('enabled', True), 'file',
                credential_references=entry.get('credentials', {})))
    if db is not None:
        rows = db.scalars(select(SourceConnection).where(SourceConnection.source.in_(sources))).all()
        result.extend(Connection(c.id, c.source, c.name, c.configuration,
            {}, c.inputs, c.enabled, encrypted_credentials=c.encrypted_credentials) for c in rows)
    ids = [c.id for c in result]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate source connection ID')
    return result

def resolve(connection):
    credentials = decrypt(connection.encrypted_credentials) if connection.encrypted_credentials else dict(connection.credentials)
    credentials.update({k:_secret_reference(v) for k,v in (connection.credential_references or {}).items()})
    return replace(connection, credentials=credentials)

@contextmanager
def run_lock(db, key):
    """Keep a session lock on a dedicated handle across adapter commits."""
    if db is None or db.get_bind().dialect.name != 'postgresql':
        yield True
        return
    number = int.from_bytes(hashlib.sha256(('keen:connection:'+key).encode()).digest()[:8], 'big') & ((1 << 63)-1)
    with db.get_bind().engine.connect() as handle:
        acquired = bool(handle.execute(text('SELECT pg_try_advisory_lock(:key)'), {'key':number}).scalar())
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    handle.execute(text('SELECT pg_advisory_unlock(:key)'), {'key':number})
                    handle.commit()
                except Exception:
                    handle.invalidate()
                    raise

def connection_runs(source):
    """Fan out a scheduled/manual run; one failed connection cannot starve others."""
    def decorate(function):
        @wraps(function)
        def run(db, *args, connection_id=None, **kwargs):
            if not getattr(deployment_settings, source + '_enabled', False):
                return [{'source': source, 'skipped': True, 'reason': 'Source type is disabled'}]
            results = []
            for connection in connections(db, source):
                if connection_id and connection.id != connection_id:
                    continue
                if not connection.enabled:
                    continue
                with connection_scope(connection):
                    try:
                        with run_lock(db, connection.id) as acquired:
                            if not acquired:
                                results.append({**identity(), 'skipped':True, 'reason':'Connection is already running'})
                                continue
                            with connection_scope(resolve(connection)):
                                rows = function(db, *args, **kwargs)
                        results.extend({**row, **identity()} for row in rows)
                    except Exception:
                        if db is not None:
                            db.rollback()
                        # Adapter exception strings can contain URLs or credentials.
                        results.append({**identity(), 'source': source, 'error': 'Connection ingestion failed'})
            return results
        return run
    return decorate

def aws_credentials():
    connection = _current.get()
    if not connection or connection.managed_by == 'environment':
        return {}
    values = connection.credentials
    if not values.get('aws_access_key_id') or not values.get('aws_secret_access_key'):
        raise ValueError('This AWS connection requires its own access key and secret')
    return {k:v for k,v in values.items() if k.startswith('aws_') and v}
