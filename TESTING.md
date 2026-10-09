# Testing KEEN

Run the suite from a checkout, using a disposable PostgreSQL database and a
Valkey service. Never use production services or production credentials for CI.

## Local entry point

Install Python 3.11 or later, Poetry 2.x, Node.js 20 or later, and the API/test dependencies:

```sh
cd services/api
poetry install --with dev --no-root
cd ../..
npm ci --prefix services/ui/tests --ignore-scripts
export KEEN_TEST_DATABASE_URL='postgresql+psycopg2://keen_test:keen_test@localhost:5432/keen_test'
export KEEN_REDIS_URL='redis://localhost:6379/0'
bash tests.sh
```

The database user must be allowed to create and drop schemas in this disposable
database. tests.sh sets the integration-test database variables, waits for
PostgreSQL, runs all API tests with pytest, rejects skipped tests, runs the frontend DOM suite, and checks
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

## Frontend behaviour and coverage

Run frontend tests independently with `bash tests-ui.sh` after installing the
locked dependencies above. `npm test --prefix services/ui` runs the same tests
without collecting coverage. Test dependencies live under `services/ui/tests`,
so they do not add DOM emulation or coverage packages to the production bundle.

The suite exercises mapping-editor draft creation and reset against the actual
admin HTML, current-event versus other-sample field groups, sample changes,
structured field boundaries, filter pill interactions, pagination, status
formatting, and rich-text sanitization. The DOM harness imports complete local
modules and replaces the external design-system/API boundary with controlled
responses. These are DOM integration tests, not real-browser end-to-end tests.

`test-results/frontend.xml` contains JUnit results. Frontend HTML and LCOV
reports live in `test-results/frontend-coverage/`. Coverage includes all
first-party page modules, including untested pages; vendor code is excluded.
The VM-module flag used by the harness can emit a Node experimental warning.

## Backend contracts and coverage

Additional contracts cover authentication/session boundaries, authorization,
CSRF, trusted proxies, rate limits, caching, OTLP and agent payloads, structured
mapping conditions, collector configuration, redaction, webhook replay
protection, event filtering/exports/facets, and cross-framework control links.
External services use controlled fakes; no live provider credentials are needed.

For a fast local pass without PostgreSQL:

```sh
cd services/api
KEEN_DATABASE_URL=sqlite:// KEEN_BOOTSTRAP_ADMIN_USERNAME=test \
  KEEN_BOOTSTRAP_ADMIN_PASSWORD=test-only AWS_EC2_METADATA_DISABLED=true \
  poetry run pytest tests -q --cov=app --cov-branch
```

This intentionally skips PostgreSQL-only tests and is not the release gate.
The new database contracts use SQLite locally; `tests.sh` sets
`KEEN_CONTRACT_TEST_DATABASE_URL` so CI runs them against the migrated release
schema in PostgreSQL. Each module gets a random schema, each test gets a rolled
back transaction, and route commits use savepoints. Existing migration,
retention and concurrency tests continue using their native PostgreSQL fixtures.

The full runner writes backend HTML, JSON and XML coverage under `test-results/`
and frontend reports alongside them. Both suites run even if backend tests fail.
Coverage measures the whole application rather than only selected new modules;
there is no arbitrary global percentage gate yet. Use the HTML reports to find
uncovered behaviour before adding assertions, rather than tests that only
repeat implementation details.
