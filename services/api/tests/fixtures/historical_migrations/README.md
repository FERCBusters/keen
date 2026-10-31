# Historical migrations used as test fixtures

KEEN's release migrations (`alembic/versions`) create the schema from
`release_schema.sql` and seed it from `release_seeds.json.gz`. The modules here
are earlier migrations that a few tests still run to build one feature's
tables or catalogue rows in isolation. They are not part of the Alembic chain
and never run against an installation.
