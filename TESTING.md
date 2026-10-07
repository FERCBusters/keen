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

## Forgejo Actions

`.forgejo/workflows/tests.yml` targets a Docker-backed runner labelled `docker`,
with a Debian 13 job container. Install system dependencies before checkout so
Node and Git are available for the checkout action. Poetry is installed in its
own virtual environment, then locked application/dev dependencies are installed
in the project's Poetry environment.

PostgreSQL 17 and Valkey 8 run as disposable Forgejo service containers. They need
no published host ports and no Docker socket mounted into the job. The existing
Docker-in-Docker runner can manage these containers. Its `container.network`
setting must be empty so Forgejo can create the per-job service network.

The workflow runs on pushes, pull requests and manual dispatch. JUnit results are
uploaded even when tests fail, using Forgejo's compatible v3 artifact uploader.
If your server mirrors Actions to another location, adapt that action URL to your
mirror.
