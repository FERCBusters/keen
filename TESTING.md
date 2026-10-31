# Testing Keen

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

The Keen Agent wire-format fixture is committed under services/api/tests/fixtures;
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
responses. These DOM integration tests run in jsdom; real-browser end-to-end
behaviour is outside their scope.

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

Keen requires `KEEN_PUBLIC_BASE_URL` at start-up; the suite sets it to the
test client's origin, `http://testserver`, unless the environment provides one.

This intentionally skips PostgreSQL-only tests and is not the release gate.
The new database contracts use SQLite locally; `tests.sh` sets
`KEEN_CONTRACT_TEST_DATABASE_URL` so CI runs them against the migrated release
schema in PostgreSQL. Each module gets a random schema, each test gets a rolled
back transaction, and route commits use savepoints. Existing migration,
retention and concurrency tests continue using their native PostgreSQL fixtures.

The full runner writes backend HTML, JSON and XML coverage under `test-results/`
and frontend reports alongside them. Both suites run even if backend tests fail.
Coverage measures the whole application, including modules a change leaves
untouched. Each suite has a coverage floor a little below its current level, so
a change that leaves its behaviour untested fails the run: `tests.sh` gives
pytest a minimum for combined statement and branch coverage, and `tests-ui.sh`
gives c8 minimums for lines, branches and functions. Raise a floor when coverage
rises, and explain any reduction in the change that makes it. Use the HTML
reports to find uncovered behaviour, then add assertions that exercise it
through the page or API.

## Python types

mypy checks `services/api/app` with the settings in
`services/api/pyproject.toml`, including the pydantic plugin. Code written
before the check joined CI still has findings, which
`services/api/mypy-baseline.txt` lists by file and message. From
`services/api`, `poetry run python ../../tools/mypy_baseline.py check` fails on
a finding the baseline does not list, and on a listed finding that has been
fixed. After fixing findings, run the script's `update` command and commit the
smaller baseline. CI and the pre-commit hooks run the check.

## Readable frontend source

Install the pinned frontend tools with `npm ci --prefix services/ui/tests`.
Run `bash format-ui.sh --write` to format first-party JavaScript and CSS, and
`bash format-ui.sh --check` to check it. Pre-commit uses the same formatter for
staged files; CI checks all first-party source, build scripts and UI tests.
Generated files, dependencies and vendored code are excluded.

The UI build writes minified JavaScript and CSS into `services/ui/dist/`.
The files under `services/ui/public/` remain readable source. The Docker image
serves the generated `dist/` directory.

## Security tests against a running installation

`tools/security_e2e` is a pytest and Playwright suite that tests a running Keen
installation through its nginx front end, API, PostgreSQL database and a real
Chromium browser. It creates synthetic accounts with single permissions, seeds
hostile text, and checks authentication coverage, CSRF and Origin enforcement,
the authorisation matrix, browser rendering under the supplied CSP, redirects,
uploads, parameter fuzzing and concurrent use of single-use state. It is not part
of `tests.sh`. Run it only against an installation you control, preferably a
disposable one; its README lists the interlocks, configuration and the records
it leaves behind. `tools/staging` restores a staging host to a golden dataset
before each run and removes everything the run created afterwards.
