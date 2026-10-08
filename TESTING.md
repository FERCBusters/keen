# Testing KEEN

Run the suite from a checkout, using a disposable PostgreSQL database and a
Valkey service. Never use production services or production credentials for CI.

## Local entry point

Install Python 3.11 or later, Poetry 2.x, Node.js and the API dependencies:

```sh
cd services/api
poetry install --with dev --no-root
cd ../..
export KEEN_TEST_DATABASE_URL='postgresql+psycopg2://keen_test:keen_test@localhost:5432/keen_test'
export KEEN_REDIS_URL='redis://localhost:6379/0'
bash tests.sh
```

The database user must be allowed to create and drop schemas in this disposable
database. tests.sh sets all three integration-test database variables, waits for
PostgreSQL, runs all API tests with pytest, rejects skipped tests, and checks
first-party UI JavaScript syntax. pytest discovers both pytest functions and
unittest classes. The script returns nonzero on any failure and writes JUnit XML
to `test-results/pytest.xml`.

Tests run serially. PostgreSQL suites use randomly named schemas, verify their
selected schema, and drop their schema afterwards. SQLite adapter fixtures clone
ORM metadata and translate the PostgreSQL UTC timestamp default only in that
copy. PostgreSQL-specific retention SQL and triggers remain covered by the real
PostgreSQL suites. Do not substitute SQLite for those suites.

The KEEN Agent wire-format fixture is committed under services/api/tests/fixtures;
there is no requirement to check out the separate agent repository. Adapter tests
use fixtures/mocks and do not require real Forgejo, Redmine, Auth0, SMTP or AWS
credentials. MFA tests use synthetic keys and mocked delivery.

The frontend check is a syntax check. There is currently no repository-wide npm
browser-test command. UI behaviour tests from separate patch bundles are not
silently counted as part of this Python suite.

## GitHub Actions

`.github/workflows/tests.yml` runs on a GitHub-hosted Ubuntu runner with a Debian
13 job container, PostgreSQL 16 and Valkey services. Upstream actions use full
commit-SHA pins. It is also reusable by the release workflow to test the exact
release tag before publication. The optional failure webhook is preserved via
`NODERED_WEBHOOK_URL`.

The suite includes the offline OSA catalogue's counts, node editing, one-hop
inheritance and event deduplication, plus PostgreSQL tests for migration from
an existing pre-production catalogue and for purge transaction coordination.
The concurrency test holds an application transaction open and verifies that
purge waits, rejects new evidence, and preserves manually paused sources after
its five-minute cooldown expires.
