"""Verify the pre-release schema before explicitly adopting the release baseline."""

import argparse
import json
import os
import re
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
QUERIES = {
    "columns": "select c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated from pg_attribute a join pg_class c on c.oid=a.attrelid join pg_namespace n on n.oid=c.relnamespace left join pg_attrdef d on d.adrelid=c.oid and d.adnum=a.attnum where n.nspname='public' and c.relkind in ('r','p') and a.attnum>0 and not a.attisdropped and c.relname!='alembic_version' order by c.relname,a.attname",
    "constraints": "select c.relname,co.conname,pg_get_constraintdef(co.oid) from pg_constraint co join pg_class c on c.oid=co.conrelid join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and co.contype!='n' and c.relname!='alembic_version' order by 1,2",
    "indexes": "select tablename,indexname,indexdef from pg_indexes where schemaname='public' and tablename!='almbic_version' and tablename!='alembic_version' order by 1,2",
    "functions": "select p.proname,pg_get_functiondef(p.oid) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and p.proname like 'keen_%' order by 1",
    "triggers": "select c.relname,t.tgname,pg_get_triggerdef(t.oid) from pg_trigger t join pg_class c on c.oid=t.tgrelid join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and not t.tgisinternal order by 1,2",
    "sequences": "select sequencename,data_type::text,start_value,min_value,max_value,increment_by,cycle,cache_size from pg_sequences where schemaname='public' order by 1",
}


def normalise(value):
    if not isinstance(value, str):
        return value
    # PostgreSQL 16 distributes an array-to-text cast over enum literals;
    # PostgreSQL 17 retains the outer array cast. Only normalise that form.
    if value.startswith("CHECK (") and " = ANY (" in value and "ARRAY[" in value:
        value = re.sub(r"\('((?:[^']|'')*)'::character varying\)::text", r"'\1'", value)
        value = re.sub(r"'((?:[^']|'')*)'::character varying", r"'\1'", value)
        value = re.sub(r"\(ARRAY(\[[^]]*\])\)::text\[\]", r"ARRAY\1", value)
    return value


def verify(connection):
    schema = connection.scalar(sa.text("SELECT current_schema()"))
    if schema != "public":
        raise RuntimeError(
            "Adoption requires the public schema used by the supplied deployment dump"
        )
    connection.execute(sa.text("SET LOCAL search_path TO public"))
    expected = json.loads((ROOT / "alembic/release_schema_reference.json").read_text())
    differences = []
    for kind, query in QUERIES.items():
        actual = [list(row) for row in connection.execute(sa.text(query))]
        a = [[normalise(v) for v in row] for row in actual]
        b = [[normalise(v) for v in row] for row in expected[kind]]
        if a != b:
            differences.append(kind)
    if differences:
        raise RuntimeError(
            "Schema differs from the audited baseline: " + ", ".join(differences)
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--adopt",
        action="store_true",
        help="After verification, change only Alembic revision metadata",
    )
    args = parser.parse_args()
    engine = sa.create_engine(
        os.environ["KEEN_DATABASE_URL"], poolclass=sa.pool.NullPool
    )
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL lock_timeout='5s'"))
        connection.execute(sa.text("LOCK TABLE alembic_version IN EXCLUSIVE MODE"))
        versions = list(
            connection.scalars(sa.text("SELECT version_num FROM alembic_version"))
        )
        if versions == ["0002_release_seed"]:
            print("Already adopted; no changes made.")
            return
        if versions != ["0090_osa_catalogue"]:
            raise RuntimeError(
                "Expected exactly revision 0090_osa_catalogue; found " + repr(versions)
            )
        verify(connection)
        if args.adopt:
            connection.execute(
                sa.text("UPDATE alembic_version SET version_num='0002_release_seed'")
            )
            print(
                "Verified and adopted. Existing data and framework selections were preserved."
            )
        else:
            print(
                "Schema verified. No changes made. Use --adopt after taking a backup and stopping application services."
            )


if __name__ == "__main__":
    main()
