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
            command.upgrade(config, '0002_release_seed')
            expected = json.loads(gzip.decompress((root/'alembic/release_seeds.json.gz').read_bytes()))
            for table, rows in expected.items():
                actual = list(conn.scalars(text(f'SELECT row_to_json(t) FROM {table} t')))
                canonical = lambda items: sorted(json.dumps(row, sort_keys=True) for row in items)
                assert canonical(actual) == canonical(rows), table
            assert conn.scalar(text('SELECT count(*) FROM frameworks')) == 94
            assert conn.scalar(text("SELECT count(*) FROM control_items WHERE framework_slug='KEEN-AF:1.0'")) == 253
            conn.execute(text("INSERT INTO keen_agents (id,name,token_hash,enabled,created_at,expires_at,health) VALUES ('baseline-pet','static-pet',:hash,true,now(),now()+interval '90 days','{}'::jsonb)"), {'hash':'a'*64})
            conn.commit()
            command.upgrade(config, 'head')
            assert conn.scalar(text('SELECT count(*) FROM agent_enrollment_profiles')) == 0
            pet = conn.execute(text("SELECT enrollment_profile_id,enrollment_labels,token_hash FROM keen_agents WHERE id='baseline-pet'")).one()
            assert tuple(pet) == (None, {}, 'a'*64)
            assert conn.scalar(text('SELECT count(*) FROM source_connections')) == 0
            assert conn.scalar(text("SELECT count(*) FROM control_items WHERE framework_slug='KEEN-AF:1.0'")) == 253
            assert 'Apache' in conn.scalar(text("SELECT description FROM frameworks WHERE slug='KEEN-AF:1.0'"))
            conn.execute(text('SELECT connection_id, connection_name FROM events LIMIT 1'))
            assert conn.scalar(text('SELECT count(*) FROM frameworks')) == 94
            for slug, count in (('iso_9001_2015', 37), ('iso_9001_2026', 39)):
                assert conn.scalar(text('SELECT count(*) FROM framework_clauses WHERE framework_slug=:slug'), {'slug':slug}) == count
            selection = conn.scalar(text("SELECT document FROM managed_configurations WHERE name='organisation-frameworks'"))
            assert selection == {'enabled':['KEEN-AF:1.0'], 'default':'KEEN-AF:1.0'}
            conn.execute(text("UPDATE control_items SET title='Local edit' WHERE framework_slug='soc2_tsc' AND ref='CC6.1'"));conn.commit()
            command.upgrade(config, 'head')
            assert conn.scalar(text("SELECT title FROM control_items WHERE framework_slug='soc2_tsc' AND ref='CC6.1'")) == 'Local edit'
    finally:
        with engine.begin() as conn:conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()


def test_release_revision_chain_contains_no_catalogue_transformations():
    from alembic.script import ScriptDirectory
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root/'alembic.ini'))
    config.set_main_option('script_location', str(root/'alembic'))
    script = ScriptDirectory.from_config(config)
    assert [r.revision for r in script.walk_revisions()] == [
        '0005_agent_enrollment', '0004_source_connections', '0002_release_seed', '0001_release_schema']
