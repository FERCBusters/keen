"""Release migration runs in an isolated schema on the PostgreSQL CI service."""
import gzip
import json
import os
from pathlib import Path
import uuid
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from app.core.config import settings


def test_release_schema_and_final_seed_data(monkeypatch):
    url = os.environ.get('KEEN_RETENTION_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires PostgreSQL integration database')
    root = Path(__file__).resolve().parents[1]
    engine = create_engine(url)
    schema = 'release_' + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    for field in type(settings).model_fields:
        value = getattr(settings, field)
        if isinstance(value, str) and value.startswith('/app/config/'):
            monkeypatch.setattr(settings, field, str(root.parents[1]/'config'/Path(value).name))
    monkeypatch.setattr(settings, 'default_framework_slug', 'KEEN-AF:1.0')
    monkeypatch.setattr(settings, 'enabled_frameworks', '')
    config = Config(str(root/'alembic.ini'))
    config.set_main_option('script_location', str(root/'alembic'))
    try:
        with engine.connect() as conn:
            conn.execute(text(f'SET search_path TO {schema},public'));conn.commit()
            config.attributes['connection'] = conn
            command.upgrade(config, 'head')
            expected = json.loads(gzip.decompress((root/'alembic/release_seeds.json.gz').read_bytes()))
            for table, rows in expected.items():
                actual = list(conn.scalars(text(f'SELECT row_to_json(t) FROM {table} t')))
                canonical = lambda items: sorted(json.dumps(row, sort_keys=True) for row in items)
                assert canonical(actual) == canonical(rows), table
            selection = conn.scalar(text("SELECT document FROM managed_configurations WHERE name='organisation-frameworks'"))
            assert selection == {'enabled':['KEEN-AF:1.0'], 'default':'KEEN-AF:1.0'}
            conn.execute(text("UPDATE control_items SET title='Local edit' WHERE framework_slug='soc2_tsc' AND ref='CC6.1'"));conn.commit()
            command.upgrade(config, 'head')
            assert conn.scalar(text("SELECT title FROM control_items WHERE framework_slug='soc2_tsc' AND ref='CC6.1'")) == 'Local edit'
    finally:
        with engine.begin() as conn:conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()
