"""Run fresh and upgrade-path migrations in a private PostgreSQL schema in CI."""
import os
import uuid
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app.core.config import settings
from tests.db_helpers import schema_engine


def test_full_migration_and_existing_preproduction_catalogue(monkeypatch):
    url=os.environ.get('KEEN_RETENTION_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set KEEN_RETENTION_TEST_DATABASE_URL for PostgreSQL migration rehearsal')
    root=Path(__file__).resolve().parents[1]
    schema='osa_migration_'+uuid.uuid4().hex
    admin=create_engine(url)
    with admin.begin() as conn:conn.execute(text(f'CREATE SCHEMA {schema}'))
    parsed=make_url(url)
    query={**parsed.query,'options':f'-csearch_path={schema}'}
    scoped=parsed.set(query=query).render_as_string(hide_password=False)
    monkeypatch.setenv('KEEN_DATABASE_URL',scoped)
    for field in type(settings).model_fields:
        value=getattr(settings,field)
        if isinstance(value,str) and value.startswith('/app/config/'):
            monkeypatch.setattr(settings,field,str(root.parents[1]/'config'/Path(value).name))
    config=Config(str(root/'alembic.ini'))
    config.set_main_option('script_location',str(root/'alembic'))
    engine=schema_engine(url,schema)
    connection=engine.connect()
    connection.execute(text(f'SET search_path TO {schema},public'))
    connection.execute(text('CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)'))
    connection.commit()
    config.attributes["connection"]=connection
    try:
        command.upgrade(config,'0089_hipaa_soc2')
        old_id=uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO frameworks (id,slug,name,created_at) VALUES (:id,'OLD:FRAMEWORK','Old',now())"),{'id':uuid.uuid4()})
            conn.execute(text("INSERT INTO control_items (id,framework_slug,type,ref,title,in_scope,tags,metadata,created_at) VALUES (:id,'OLD:FRAMEWORK','custom','old','Old',true,'{}','{}',now())"),{'id':old_id})
            retained=conn.scalar(text("SELECT count(*) FROM control_items WHERE framework_slug='KEEN-AF:1.0'"))
        with engine.connect() as conn:
            dvstf=conn.scalar(text("SELECT count(*) FROM control_items WHERE framework_slug='UK-DVSTF:1.0'"))
            assert dvstf > 0
        command.upgrade(config,'head')
        with engine.begin() as conn:
            assert conn.scalar(text('SELECT count(*) FROM frameworks'))==92
            assert conn.scalar(text("SELECT count(*) FROM control_items WHERE framework_slug='UK-DVSTF:1.0'"))==dvstf
            assert conn.scalar(text('SELECT count(*) FROM osa_control_mappings'))==23140
            assert conn.scalar(text("SELECT count(*) FROM control_items WHERE framework_slug='KEEN-AF:1.0'"))==retained
            assert conn.scalar(text('SELECT count(*) FROM control_items WHERE id=:id'),{'id':old_id})==0
            conn.execute(text("UPDATE control_items SET title='Administrator edit' WHERE framework_slug='soc2_tsc' AND ref='CC6.1'"))
        command.upgrade(config,'head')
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT title FROM control_items WHERE framework_slug='soc2_tsc' AND ref='CC6.1'"))=='Administrator edit'
    finally:
        connection.close()
        engine.dispose()
        with admin.begin() as conn:conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin.dispose()
