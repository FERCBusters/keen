"""Isolated contracts: migrated PostgreSQL in CI, SQLite for quick local runs."""
import os
import uuid
from pathlib import Path
import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool,NullPool
from tests.db_helpers import create_sqlite_schema,schema_engine

@pytest.fixture(scope='module')
def contract_postgres_engine():
    url=os.getenv('KEEN_CONTRACT_TEST_DATABASE_URL')
    if not url:
        yield None
        return
    # A schema per module and an outer transaction per test keep committed
    # route writes isolated while avoiding dozens of full schema migrations.
    schema='contracts_'+uuid.uuid4().hex
    admin=create_engine(url,poolclass=NullPool)
    with admin.begin() as conn:conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()
    engine=schema_engine(url,schema)
    try:
        from alembic import command
        from alembic.config import Config
        root=Path(__file__).resolve().parents[1]
        config=Config(str(root/'alembic.ini'))
        config.set_main_option('script_location',str(root/'alembic'))
        with engine.begin() as connection:
            config.attributes['connection']=connection
            command.upgrade(config,'0001_release_schema')
        yield engine
    finally:
        try:
            with engine.begin() as conn:conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        finally:engine.dispose()

@pytest.fixture
def contract_db(contract_postgres_engine):
    if contract_postgres_engine is not None:
        with contract_postgres_engine.connect() as connection:
            transaction=connection.begin()
            try:
                with Session(bind=connection,expire_on_commit=False,join_transaction_mode='create_savepoint') as db:
                    yield db
            finally:transaction.rollback()
    else:
        engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        try:
            create_sqlite_schema(engine)
            with Session(engine,expire_on_commit=False) as db:
                yield db
        finally:engine.dispose()
