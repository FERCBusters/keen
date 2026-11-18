# KEEN developer manual

Nov 16, 2026 · @Miguel

## Start here

Keen collects evidence that an organisation's security controls are working, and keeps the ISMS records (risks, policies, audits, people) that sit beside that evidence. This manual is for a developer who knows Python, knows some JavaScript, and has never opened this repository.

It describes the tree after the clean-up and source-registry patches, which sit on top of commit `ebeafe9`.

### The one idea

Everything Keen collects becomes an **Event**. An Event is one thing that happened: a commit, a log line, a sign-in, a failed posture check. It carries a few indexed labels (`source`, `system`, `actor`, `action`, `outcome`, `severity`, `summary`), and the original bytes are kept beside it as an **Artifact** with a SHA-256 checksum.

**Mapping rules** then attach Events to **controls** in a compliance **framework** such as ISO 27001. A `Mapping` row is that attachment. A control with recent mapped Events has evidence; one without has a gap.

Nearly every screen is a view over those four things, plus the records people write by hand and link to the same controls: risks, PESTLE(E) items, policies, objectives, audits.

### Three rules that shape the code

1. **Routes are thin; services hold the logic.** A route validates input and checks permission, then calls one function in `app/services/`. Services never import FastAPI. A test enforces this.
2. **One helper, one home.** A second copy of a helper is treated as a defect. Before writing one, search for it; the shared homes are listed in `AGENTS.md`.
3. **Everything is tested, and the tests read the source.** CI requires 100% line and branch coverage on the backend and 100% statement, branch and function coverage on the UI. Many tests are *contract tests* that scan the code itself, for example to refuse a route with no permission rule.

### How to read this manual

Read "Run it and test it" and "Repository map" first. Then read "A request end to end", because every feature follows that path. After that, go to the section for the area you are changing; each ends with a recipe.

`AGENTS.md` is the rule book and `SECURITY.md` is the threat model. This manual explains how the pieces fit; those two say what you must not break.

## Run it and test it

You need Python 3.11 or later, Poetry 2, Node 20 or later, PostgreSQL and Valkey (or Redis). The tests never touch a real provider; every external service is faked.

### Set up once

```sh
cd services/api && poetry install --with dev --no-root && cd ../..
npm ci --prefix services/ui/tests --ignore-scripts
export KEEN_TEST_DATABASE_URL='postgresql+psycopg2://keen_test:keen_test@localhost:5432/keen_test'
export KEEN_REDIS_URL='redis://localhost:6379/0'
```

The database must be disposable: the suite creates and drops schemas in it.

### The commands you will use

| Task | Command | Notes |
| --- | --- | --- |
| Everything, as CI runs it | `bash tests.sh` | Backend with coverage, then UI with coverage, then a syntax check of every UI script. About 35 minutes. |
| Backend only, one file | `poetry run pytest tests/test_features.py -q` from `services/api` | Needs the five `KEEN_*_TEST_DATABASE_URL` variables; `TESTING.md` has the full line. |
| UI only | `bash tests-ui.sh` | About 8 minutes, with coverage. |
| UI, one file | `node --experimental-vm-modules --test services/ui/tests/risk-page.test.mjs` | Seconds. Use this while working. |
| Python lint and format | `poetry run ruff check ../..` and `poetry run ruff format ../..` | Run from `services/api`. |
| Python types | `poetry run mypy` | Checks `app/`. |
| UI format | `bash format-ui.sh --write` | Prettier. |
| UI lint | `bash lint-ui.sh --check` | Refuses new unescaped `innerHTML`. |
| Run the stack | `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build` | Needs a filled-in `.env`; start from `.env.example`. |

### What CI will fail you for

- A backend line or branch that no test runs. Coverage must be exactly 100%.
- A UI statement, branch or function that no test runs. Also 100%.
- A skipped test. `tests.sh` treats a skip as a failure, so a missing database variable cannot turn the run green.
- A route with no entry in `tools/security_e2e/access_policy.py`.
- A new `innerHTML` assignment that is not on the suppression list.
- A ruff, mypy or prettier complaint.

The practical consequence: when you delete code, delete its tests; when you add a branch, add the test that takes it. Coverage is also the fastest dead-code detector here. After a removal, run the suite with `--cov=app --cov-branch --cov-report=term-missing:skip-covered` and anything left uncovered is a helper that just lost its last caller.

## Repository map

The repository holds two deployable services and their tests. Paths below are relative to the root.

| Path | What lives there |
| --- | --- |
| `services/api/app/main.py` | The FastAPI application: middleware, router registration, error handlers. |
| `services/api/app/api/routes/` | HTTP adapters, one module per area. Thin by rule. |
| `services/api/app/api/dependencies/` | Route guards such as `require_risk_read`, used through `Depends(...)`. |
| `services/api/app/api/mcp/` | The MCP server for AI agents. Its tools call ordinary routes. |
| `services/api/app/services/` | The domain logic. One module per feature, or a package (`isms/`, `audits/`, `quizzes/`) for large ones. |
| `services/api/app/db/models.py` | Every SQLAlchemy model, in one file. |
| `services/api/alembic/versions/` | Migrations. `0001` loads the 1.0 schema; later ones are incremental. |
| `services/api/app/security/` | Authentication, permissions, sessions, rate limits, redaction, safe regular expressions, encoding helpers. |
| `services/api/app/ingest/` | One module per evidence source, plus `registry.py`, `common.py` (event storage) and `connections.py`. |
| `services/api/app/ingest/cloud/` | Cloud posture providers, one module each. |
| `services/api/app/integrations/` | Custom API ingesters that administrators define without code. |
| `services/api/app/agents/` | The Keen Agent protocol: enrolment, signatures, encryption. |
| `services/api/app/mapping/` | Mapping rules: parsing, matching, collector identity. |
| `services/api/app/worker/` | Celery app, tasks and the rule backfill. |
| `services/api/app/core/` | Settings, datetime helpers, configuration documents, source labels. |
| `services/api/scripts/` | Start script, demo data seeder, admin recovery tools. |
| `services/api/tests/` | About 320 pytest files. |
| `services/ui/public/*.html` | One static page per screen. |
| `services/ui/public/pages/*.js` | One ES module per page, plus shared `*-common.js` modules. |
| `services/ui/public/app.js` | Shared UI helpers: API calls, escaping, navigation, DOM building. |
| `services/ui/public/help/` | The Help site, static HTML with a search index. |
| `services/ui/nginx/default.conf` | The reverse proxy: page gating, `/api/` forwarding, security headers. |
| `services/ui/tests/` | About 150 Node test files running pages in jsdom. |
| `config/` | Shipped defaults for each source and for mapping rules. Mostly near-empty; administrators override them in the database. |
| `tools/security_e2e/` | The route permission inventory and a security suite that runs against a deployment. |

### The processes

One image runs as several containers, each with a different job.

| Compose service | Job |
| --- | --- |
| `keen-ui` | nginx: serves static files and proxies `/api/`. |
| `keen-api` | Runs migrations, then uvicorn with `app.main:app`. |
| `keen-worker` | Celery worker for internal jobs on the default queue. |
| `keen-http-worker` | Celery worker for all outbound collection, on the `ingest-http` queue. One task at a time. |
| `keen-imap-worker` | Optional Celery worker for mail collection, on the `imap` queue. |
| `keen-beat` | Celery beat: enqueues scheduled tasks. |

PostgreSQL holds the records. Valkey holds sessions, rate-limit counters, caches and the task queue.

## A request end to end

Every feature follows the same seven steps from the browser to the database. Learn this path once and most of the backend reads the same way.

1. **nginx receives the request.** For an HTML page it first makes an `auth_request` subrequest to `/v1/auth/check` (or `/v1/auth/check-admin` for admin pages), so a signed-out visitor never receives page markup.
2. **nginx forwards `/api/...`.** It strips the `/api` prefix and passes the rest exactly as sent. The browser calls `/api/v1/risks`; FastAPI sees `/v1/risks`.
3. **Middleware runs in `app/main.py`.** From the outside in: security response headers, body size limits, demo expiry, the audit trail, authentication (session cookie, trusted proxy header or MCP token), then CSRF and Origin checks.
4. **Policy checks run before the handler.** `security/route_policy.py` decides whether the route is public. `security/feature_policy.py` refuses a route whose feature an administrator has switched off.
5. **The route handler runs.** It lives in `app/api/routes/`, declares its inputs as typed parameters, and gets its permission check from a `Depends(...)` guard.
6. **The handler calls one service function.** The function in `app/services/` owns the queries, the writes and the transaction.
7. **Errors become JSON.** A service raises `DomainError(status_code, detail)`. Every router is an `app.api.routing.Router`, which turns that into a consistent error response.

### What that looks like in code

The route that lists the risks linked to a clause is the whole pattern in nine lines:

```python
# app/api/routes/risks.py
@router.get("/v1/clauses/{clause_id}/risks")
def clause_risks(
    clause_id: str,
    user=Depends(require_risk_read),
    db: Session = Depends(get_db),
):
    return service.clause_risks(clause_id=clause_id, user=user, db=db)
```

The guard `require_risk_read` authenticates the caller and checks the `risk.read` permission. The service does the work and knows nothing about HTTP:

```python
# app/services/risks.py
def clause_risks(*, clause_id: str, user, db: Session):
    cid = _try_uuid(clause_id)
    if not cid:
        raise DomainError(status_code=400, detail="clause_id must be a UUID")
    clause = db.get(FrameworkClause, cid)
    if not clause:
        raise DomainError(status_code=404, detail="Clause not found")
    ...
```

Service functions take keyword-only arguments, and `user` and `db` are always among them.

## Backend conventions

These are the rules a reviewer, human or automated, will hold you to. `AGENTS.md` states them in full.

- **Routes are thin.** Validate, authorise, call a service, return. No queries in a route.
- **Services never import FastAPI.** They raise `DomainError`. `tests/test_service_boundaries.py` checks the imports.
- **The operation owns its transaction.** A service function commits once, at the end. Shared helpers it calls flush but do not commit.
- **Every router is `app.api.routing.Router`.** It parses JSON and form bodies off the event loop and maps `DomainError` to a response.
- **Coroutines await only asynchronous calls.** Most routes are plain `def`, which FastAPI runs in a thread pool. An `async def` route must hand database and Valkey work to `run_in_threadpool`. A test scans for violations.
- **Imports go at the top of the module.** A few function-level imports exist to break import cycles; do not add one for convenience.
- **Read settings directly.** Write `settings.loki_enabled`, never `getattr(settings, "loki_enabled", False)`. Every setting is declared in `core/config.py` with a default.
- **Record changes.** A write to a user-visible record calls `record_entity_changelog(...)` with the state before and after, so the record's history tab can show it.
- **British or Australian spelling** in text a user reads.

### Reuse before you write

Search for a helper before writing one. The usual homes:

| Need | Where |
| --- | --- |
| Permission and role checks | `security/permissions.py`, `security/roles.py` |
| Route guards | `api/dependencies/` |
| Clean or bound user text, validate an email | `services/text.py`, `core/email_address.py` |
| Dates and ISO serialisation | `core/datetime_utils.py` (`utc_now_naive`, `iso_or_none`, `iso_utc`) |
| SHA-256 digests and base64url | `security/encoding.py` |
| Compile a regular expression | `security/regex.py` only (`scan_pattern`), which bounds match time |
| Control and clause lookups and summaries | `services/control_refs.py` |
| Store an event with its artifact | `ingest/common.py` |
| Per-address rate limits | `security/rate_limit.py` |

### Recipe: add an endpoint

1. Write the service function in the module that owns the feature. Keyword-only arguments, `user` and `db` included. Raise `DomainError` for refusals.
2. Add the route in `app/api/routes/<area>.py`, with a guard from `api/dependencies/`.
3. Add the route to `tools/security_e2e/access_policy.py` with its permission rule. A test fails until you do.
4. If the route's permission is decided inside the operation rather than by a guard, list it in `security/route_policy.py`.
5. If an MCP tool should reach it, add it to `INNER_ROUTES` in `security/mcp_tokens.py`.
6. Write tests through the assembled application: an allowed caller, a caller without the permission (403) and an anonymous caller (401), plus each refusal branch.
7. Call it from the page with `apiGet` or `apiPost` from `app.js`.

## Data model and migrations

Every table is a SQLAlchemy model in the single file `app/db/models.py`, and every schema change is an Alembic migration in `alembic/versions/`.

### The models you will meet first

| Model | What a row is |
| --- | --- |
| `Framework`, `ControlItem` | A compliance framework and one control in it. Clauses are also stored as `ControlItem` rows of type `clause`. |
| `FrameworkClause`, `ControlClauseLink` | A clause of a framework (ISO management clauses), and how far a control applies to it. |
| `Event`, `Artifact`, `Mapping` | One collected occurrence, its original bytes, and its link to a control. |
| `Risk`, `RiskAsset`, `RiskControlLink`, `RiskClauseLink` | A risk, the asset it concerns, and its links to controls and clauses. |
| `PestleItem`, `PestleClauseRelevance`, `PestleControlRelevance` | A PESTLE(E) item and how relevant it is to each clause and control. |
| `Isms*` | The ISMS registers: objectives, documents, organisation chart, people, vendors, access matrix, measures, meetings, SWOT. |
| `IsmsEntityControlLink`, `IsmsEntityClauseLink` | One generic pair of link tables that ties any ISMS record to controls and clauses. |
| `EntityChangelog` | One recorded change to any record, with the state before and after as JSON. |
| `User`, `Group`, `Permission` | Accounts, groups and the permission codes they hold. |
| `ManagedConfiguration` | A named JSON document an administrator saved in the UI: feature switches, collector settings, mapping rules. |
| `SourceConnection` | One named connection to a source, with its credentials encrypted. |

Timestamps are stored as naive UTC. Use `utc_now_naive()` from `core/datetime_utils.py`; do not call `datetime.utcnow()`.

### How the schema is built

`0001_release_schema.py` loads the whole 1.0 schema from `release_schema.sql`. `0002_release_seed.py` loads the shipped frameworks and defaults from a compressed JSON file. Everything from `0004` onward is an ordinary incremental migration. The API container runs `alembic upgrade head` before it starts.

### Recipe: change the schema

1. Edit the model in `models.py`.
2. Add `alembic/versions/00NN_short_name.py`. The revision id must be 32 characters or fewer, because that is the width of Alembic's version column here.
3. Write `upgrade()` and a real `downgrade()`. If the downgrade cannot restore data, say so in its docstring.
4. Add the revision to the ordered list in `tests/test_release_baseline.py::test_release_revision_chain_contains_no_catalogue_transformations`.
5. Test the migration in a scratch schema: insert rows at the previous revision, upgrade, assert, downgrade, assert. `tests/test_link_migrations.py` is a short example to copy.

Two traps when a migration rewrites change history. A state that was never recorded is stored as a JSON `null`, not an SQL `NULL`, so guard JSON operators with `jsonb_typeof(column) = 'object'`. And the stored feature settings accept only keys the code defines, so a migration that removes a feature must also remove its key from the `features` document, or the API answers 503.

## Permissions, features and the route inventory

Three separate checks decide whether a request may proceed: is the caller signed in, do they hold the permission, and is the feature switched on. Each has one home.

### Permissions

An account is either an administrator or it is not. Beyond that it holds permission codes, granted directly or through its groups. Codes come in read and manage pairs per domain: `events.read`, `isms.read` and `isms.manage`, `risk.read` and `risk.manage`, `pestle.read` and `pestle.manage`, `audits.read`, `audits.manage` and `audits.admin`, and a few more. A manage permission implies its read permission.

- `security/permissions.py` resolves what an account effectively holds (`has_permission`, `with_implied_permissions`).
- `api/dependencies/` wraps those checks as route guards. Use a guard; do not check permissions by hand in a route.
- The UI receives the same answers as booleans on the signed-in user (`me.can_view_risks`, `me.can_manage_pestle`, `me.is_admin`) and uses them only to hide things. The API check is the one that counts.

### Feature switches

An administrator can switch whole areas off: risks, PESTLE(E), each ISMS register, quizzes, stories and so on. `services/features.py` defines the features and reads their state from the `features` managed configuration. `security/feature_policy.py` maps each route module to the feature it needs and refuses the request when that feature is off.

In the UI, `featureEnabled(me, 'pestle')` from `pages/feature-availability.js` answers the same question, so a page can skip a request it knows will be refused.

### The route inventory

`tools/security_e2e/access_policy.py` lists every served route with its rule: `SIGNED_IN`, `ADMIN`, `RISK_READ`, `PESTLE_MANAGE` and so on. It is deliberately a second, independent statement of the rules.

- `tests/test_access_policy_inventory.py` fails when a route exists without an entry, or an entry exists without a route.
- The security suite in `tools/security_e2e/` replays the inventory against a running deployment.
- `security/route_policy.py` holds the short list of exceptions: public routes, and routes whose permission is decided inside the operation rather than by a guard.

So adding, renaming or removing a route always means editing the inventory in the same change.

## Evidence sources

Evidence reaches Keen five ways, and every one of them ends in the same function: `ingest/common.py::store_event_with_artifact`. It redacts secrets, bounds field sizes, stores the Artifact, inserts the Event and applies the mapping rules.

| Way in | Code | What starts it |
| --- | --- | --- |
| Built-in collectors (GitHub, Loki, Jenkins and 14 more) | `app/ingest/<source>.py` | Celery beat, or an administrator's Run button |
| Mail | `app/ingest/imap.py` and `imap_support/` | `keen.dispatch_imap`, every five minutes, on its own worker |
| Webhooks and signed metric submissions | `ingest/webhooks.py`, `services/webhooks.py` | An inbound HTTP request |
| Custom API ingesters | `app/integrations/` | `integration_tick_task`, every minute |
| Cloud posture (AWS, Azure, Google and others) | `app/ingest/cloud/<provider>.py` | Beat, every 15 minutes |
| Keen Agent on servers and workstations | `app/agents/`, `services/ueu_evidence.py` | An inbound request, encrypted and signed |

### The source registry

Each source type declares itself once, in its own module, and nothing else lists source names. The declaration sits at the bottom of the module, under its collector:

```python
# app/ingest/jenkins.py
register(
    Source(
        name="jenkins",
        module=__name__,
        queue=HTTP_QUEUE,
        schedule=4500.0,
        config_setting="jenkins_config_path",
        public_fields=("base_url", "username"),
        secret_fields=("api_token",),
        input_sections=("jobs",),
        section="jobs",
        id_field="name",
        definition_sections=("jobs",),
        key_field="name",
    )
)
```

`app/ingest/registry.py` holds the `Source` dataclass and the lookup functions. The first time anything reads the registry, it imports every module in `app/ingest/`, which is what makes each declaration run.

| Field | Meaning | Who reads it |
| --- | --- | --- |
| `queue` | `HTTP_QUEUE` for a polled source, `IMAP_QUEUE` for mail, `None` for a source that is sent to Keen | Celery task routing |
| `schedule` | Seconds between runs, or a crontab minute expression such as `"*/30"` | The beat schedule |
| `config_setting` | The settings field naming its file of shipped defaults | Configuration documents, source labels |
| `public_fields`, `secret_fields` | What a named connection may store, and which values are encrypted | Connection forms and validation, secret detection |
| `input_sections` | Every section of its configuration that lists things to collect | Whether a connection has anything to collect |
| `section`, `id_field` | Where new collection items go, and the field that names one | The configuration editor |
| `definition_sections`, `key_field` | The sections edited as evidence definitions, and what identifies an item to a mapping rule | Evidence definitions |
| `demo` | `False` hides the source in a demonstration installation | Enablement |

Three properties are derived from those, so a source never states them: `collected` (it has a queue), `polled` (its queue is the HTTP one) and `enabled` (its `KEEN_<SOURCE>_ENABLED` setting, unless a demo withholds it). `run_seconds` reads `KEEN_<SOURCE>_RUN_SECONDS`.

Everything below is computed from the registry, and none of it needs editing when a source is added:

- The Celery task `ingest_<source>_all_task`, its queue, its time limit and its beat entry (`worker/celery_app.py`, `worker/tasks.py`).
- The one manual-run route, `POST /v1/admin/ingest/{source}/run`.
- The set of sources an administrator can pause (`ingestion_pause.pausable_sources()`).
- Connection field validation and the connection form (`ingest/connections.py`, `services/source_connections.py`).
- Configuration documents and evidence definitions (`services/managed_configurations.py`).
- The enabled flags the Admin page shows (`services/admin.py`).

`tests/test_source_registry.py` holds the contract: every module with an `ingest_<name>_all` function is registered, every registered source has the settings its kind needs, and the worker routes, limits and schedules every polled source.

### What a collector looks like

A collector is one function, `ingest_<source>_all(db)`, wrapped in two decorators:

```python
@pausable("jenkins")
@connection_runs("jenkins")
def ingest_jenkins_all(db: Session) -> list[dict[str, Any]]: ...
```

`@pausable` skips the run when an administrator has paused the source or an evidence purge is under way. `@connection_runs` calls the function once per named connection, with that connection's settings and credentials in scope, and records each run's health. Inside, read `settings` from `app.ingest.connections`, not from `app.core.config`: that scoped object answers with the running connection's values.

Collectors keep a cursor per input (`IngestionCursor`) and commit as they go, so a run stopped at its time limit loses nothing and the next run resumes.

Each record goes through `ingest/common.py::RecordReader`, so one record Keen cannot read never stops the records after it:

```python
reader = RecordReader()

def read(build):
    # Turn one upstream record into what is stored, or None to pass over it.
    # No network or database work happens here.
    ts = utc_from_timestamp_naive(build["timestamp"] / 1000.0)
    return dict(timestamp=ts, source="jenkins", ...)

for event in reader.each(builds, read):
    store_event_with_artifact(db, **event)

return {"created_events": created, "invalid_records": reader.invalid}
```

A record whose shape or values `read` cannot handle (a missing member, a value of another type, a number out of range, nesting deeper than Python recurses) is counted in `reader.invalid` and the next record is read. A storage or upstream failure is raised from the loop body, so the input fails with its cursor kept, as before. Decode an upstream response with `security/json_shape.decode_json(response.content)`, never `response.json()` or `json.loads`: a value nested past the interpreter's recursion limit would otherwise make the whole page unreadable, while `decode_json` keeps the page and stores that value's deeper parts as `[nested too deeply]`. `tests/test_record_containment.py` scans the whole application and fails on any loop that writes events without going through `RecordReader.each`, and on an ingester that decodes upstream JSON any other way. The Keen Agent receivers are the only exception: an agent keeps its evidence queued until Keen accepts it, so they store a request whole or refuse it.

### Recipe: add a polled source

1. Add settings to `core/config.py`: `<source>_enabled`, `<source>_run_seconds`, `<source>_config_path`, and its base URL and credentials.
2. Create `app/ingest/<source>.py` with `ingest_<source>_all(db)` under the two decorators, reading each record through `RecordReader.each` and calling `store_event_with_artifact` for each event it yields, and reporting `reader.invalid` as `invalid_records`.
3. Declare it with `register(Source(...))` at the bottom of that module.
4. Add `config/<source>.yml` with shipped defaults, and document the settings in `.env.example`.
5. Teach `mapping/collector.py::event_collector` how to read the source's `raw_pointer`, so rules can target one collection item.
6. Add the source to the Admin page's evidence configuration (`pages/evidence-config.js`, `pages/connection-hub.js`) and write its Help page.
7. Test the collector against a fake upstream. `tests/test_source_registry.py` will already be checking your declaration.

Steps 5 and 6 are the parts the registry does not yet cover: the collector-identity chain in `mapping/collector.py` and the source lists in the UI are still written per source.

## Mapping rules

A mapping rule says "an event that looks like this is evidence for these controls". Applying the rules to an event writes `Mapping` rows, and those rows are what every control page, graph and audit reads.

| Piece | File | Job |
| --- | --- | --- |
| Rule loading and matching | `app/mapping/rules.py` | `load_rules` reads the rules; `evaluate_by_framework` decides which controls one event maps to |
| The event a rule sees | `rules.py::event_fields` | The one flattened view of an event that every matcher reads |
| Targets | `app/mapping/targets.py` | Turns a rule's control and clause references into records |
| Collector identity | `app/mapping/collector.py` | Works out which collection item produced an event, so a rule can target one repository or one job |
| Field conditions | `app/mapping/fields.py` | Conditions on payload fields, and the fields the rule editor offers |
| Shipped rules | `config/rules.yml` | Defaults; administrators edit a managed copy in the database |
| Remapping | `services/admin.py::admin_remap_run` | Re-applies the rules to stored events after an edit |

Rules are a managed configuration: the YAML file in `config/` is the shipped default, and an administrator's edits are stored as versions in `managed_configurations`. Code reads the effective document through `services/managed_configurations.py`, never the file directly.

Two things to know before changing a matcher:

- Read event values through `event_fields(event)`. A second way of reading the same field is how two matchers come to disagree about one event.
- A rule change does not touch existing mappings until a remap runs. The Admin page offers the remap, and tests that expect new mappings must run it.

## Domain tour

Each row is a feature area, the service that owns it and its pages. Routes live in `app/api/routes/` under the same name as the service unless noted.

| Area | Service | Pages |
| --- | --- | --- |
| Frameworks, controls, clauses | `services/controls.py`, `clauses.py`, `frameworks.py`, `control_links.py` | `controls.html`, `control.html`, `clauses.html`, `clause.html` |
| Statement of Applicability | `services/soa.py` | `statement-of-applicability.html` |
| Events and artifacts | `services/events.py`, `event_filters.py`, `artifacts.py` | `events.html`, `event.html` |
| Sources and connections | `services/sources.py`, `source_connections.py` | `sources.html`, `source.html`, `admin.html` |
| Risks | `services/risks.py`, `risk_register.py`, `risk_mitigator.py` | `risks.html`, `risk.html`, `risk-wizard.html`, `mitigator.html` |
| PESTLE(E) | `services/pestle.py` | `pestle.html`, `pestle_item.html` |
| ISMS records | `services/isms/` (objectives, documents, meetings, assets, organisation, effectiveness, SWOT) | `isms.html` and the `isms-*.html` pages, `swot.html` |
| Audits | `services/audits/` | `audits.html`, `audit.html`, `audit-wizard.html` |
| People, vendors, quizzes | `services/people.py`, `assurance.py`, `services/quizzes/` | `assurance.html`, `person.html`, `vendor.html`, `quizzes.html` |
| Questions and stories | `services/questions.py`, `stories.py` | `questions.html`, `stories.html`, `story.html` |
| Graphs | `services/graph.py`, `stats.py` | The Visualisations tab of several pages |
| Administration | `services/admin.py`, `managed_configurations.py`, `users.py` | `admin.html` |
| Accounts and sign-in | `services/login.py`, `me.py`, `mfa_management.py` | `login.html`, `mfa.html`, `account.html` |

### The three kinds of risk

Risks are the area where the vocabulary matters most, because three things are called a risk.

| Kind | Stored as | How to tell |
| --- | --- | --- |
| Simple risk | A `Risk` row | `risk_types` is empty; `method` is `general` |
| CIA risk | A `Risk` row | `risk_types` names Confidentiality, Integrity or Availability; `method` is `cia` |
| PESTLE(E) item | A `PestleItem` row | A separate record, scoped to one framework; `method` is `pestle` where the two are listed together |

Simple and CIA risks are one record type, the register. Use `services/risks.py::risk_method(risk)` on the backend and `methodOf(risk)` from `pages/risk-common.js` in the UI to name the kind; do not test `risk_types` yourself. Any screen that lists register risks lists both kinds and labels each with `methodBadgeHtml`. A screen that shows only CIA risks says so and filters for them, as the CIA risks tab of the Risks dashboard does.

All three link to both controls and clauses:

| Record | To controls | To clauses |
| --- | --- | --- |
| Risk (simple or CIA) | `RiskControlLink` | `RiskClauseLink` |
| PESTLE(E) item | `PestleControlRelevance` | `PestleClauseRelevance` |

A risk's link either exists or does not. A PESTLE(E) item's link carries a relevance (high, medium, low), and a rating of N/A means no link; `services/pestle.py::rated_links` filters those out.

The pictures follow the same rule. The Visualisations tab of the Risks dashboard draws all three kinds against controls, with a Show filter for one kind, from `GET /v1/graph/risk_control`. The risk page and the PESTLE(E) item page each have a Visualisation tab drawn by the same module, `pages/record-links-graph.js`.

The record is called a PESTLE(E) item everywhere: on screen, in Help and in the code (`PestleItem`, `pestle_items`, `/v1/pestle/items`, `pestle_item.html`). Do not call it a item.

## MCP

Keen serves a Model Context Protocol endpoint at `/v1/mcp` so an AI assistant can read the ISMS and, with consent, change it. The code is in `app/api/mcp/`.

| File | Job |
| --- | --- |
| `server.py` | Builds the MCP application and its instructions |
| `tools.py` | The read tools, and the shaping of what each returns |
| `changes.py` | The tools that add and update records |
| `runtime.py` | Makes a tool's request to Keen's own API, in process |
| `provider.py`, `services/mcp_oauth.py` | OAuth sign-in and consent |
| `security/mcp_tokens.py` | Scopes, and `INNER_ROUTES` |

An MCP tool does not query the database. It calls one of Keen's ordinary API routes in process, as the signed-in user, so every permission check, feature switch and audit entry applies exactly as it does to the browser.

`INNER_ROUTES` is the list of routes a tool may call, each with the scope it needs. A tool cannot reach a route that is not on it. That list is the answer to "does MCP use this route?", and a test fails if a route on it stops existing.

To add a tool:

1. Check the route it needs exists and returns what you want. Add one the usual way if not.
2. Add the route to `INNER_ROUTES` with its scope.
3. Write the tool in `tools.py` (reading) or `changes.py` (changing), and return a shaped summary, not the raw response.
4. Extend `tests/test_mcp_tools.py`, and `test_mcp_access.py` or `test_mcp_write_access.py` for the scope.

## The UI

The UI has no framework and no build step. Each screen is a static HTML file and one ES module, served by nginx, talking to the API with `fetch`. If you know HTML, the DOM and `async`/`await`, you can read all of it.

| Where | What |
| --- | --- |
| `services/ui/public/<page>.html` | The markup of one screen. It loads Bootstrap and `pages/<page>.js` |
| `public/pages/<page>.js` | That screen's behaviour: about 100 modules |
| `public/app.js` | Shared helpers every page imports: API calls, escaping, the navbar, dates, toasts, tabs, tables |
| `public/pages/*-common.js` | Helpers shared by one family of pages: `risk-common.js`, `isms-detail-common.js`, `affected-common.js`, `quiz-common.js` |
| `public/vendor/` | Bootstrap, D3 and the MOSP design system. Not first-party code; never edited here |
| `public/help/` | The Help site: hand-written HTML pages and a search index built from them with `bash build-help-index.sh` |
| `services/ui/tests/` | One `*.test.mjs` per behaviour, run under Node with jsdom |

### How a page module is built

Every page module follows the same order. `pages/risk.js` is a short example.

1. Imports, all at the top.
2. `const me = await initNavbar();` draws the navigation and returns the signed-in user with their permissions.
3. `document.getElementById` lookups for the elements the page fills.
4. Render functions that build HTML strings from API data.
5. Load functions that call `apiGet` and hand the result to a render function.
6. Event listeners, then the first load.

Rules that the tests and linter enforce:

- **Escape everything.** Any value from the API that goes into an HTML string passes through `esc()`. Pages are tested with hostile strings such as `<img src=x onerror=alert(1)>`.
- **Use the API helpers.** `apiGet`, `apiPost`, `apiPatch`, `apiDelete` from `app.js` add the CSRF token, handle sign-out and refuse unsafe paths. Never call `fetch` yourself.
- **Ask through `confirmAction`.** Never the browser's `confirm()`.
- **No inline scripts or handlers.** The Content Security Policy forbids them. Listeners are attached in the module.
- **Keep the framework in links.** Build internal links with `withFramework(path, framework)` so the selected framework follows the user.
- **Hide what a user cannot do, but never rely on it.** The page reads flags such as `me.can_manage_risks` to hide buttons. The API still decides.

### Feature switches in the UI

An administrator can switch features off. Two things make the UI follow:

- `pages/feature-availability.js` maps each page to its feature, and hides links to a page whose feature is off.
- An element with `data-feature="<key>"` is hidden when that feature is off. Use it on tabs and buttons that belong to another feature, as the Visualisation tabs do with `data-feature="visualisations"`.

### Recipe: add a page

1. Copy the HTML of the closest existing page to `public/<name>.html` and point its module script at `/pages/<name>.js`.
2. Write `public/pages/<name>.js` in the order above. Import from `app.js` and the `-common` module of its family before writing a helper.
3. Add the page to `pages/feature-availability.js` under its feature, and to the navigation if it needs an entry.
4. Write `services/ui/tests/<name>-page.test.mjs` with the `page()` fixture described below.
5. Run `bash format-ui.sh --write`, `bash lint-ui.sh --check` and `bash tests-ui.sh`.

### Recipe: add a tab to an existing page

1. Add the tab button and its pane to the HTML, with ids that match the page's other tabs.
2. Add the tab to the page's `lookupTab` map, so `?tab=<name>` opens it.
3. Load its data in a `shown.bs.tab` listener, so a hidden tab costs nothing, and again at start-up when the address names it.
4. Test both ways in: the tab being shown, and the address opening it.

## Tests and guard rails

Both suites must pass with 100% coverage, and a pull request that lowers either fails. That sounds severe, and in practice it means one thing: every branch you write needs a test that takes it. Write the branch only if you can say when it happens.

| Suite | Command | Gate |
| --- | --- | --- |
| Backend | `bash tests.sh`, or `pytest tests` in `services/api` | 100% of lines and branches of `app/` |
| UI | `bash tests-ui.sh` | 100% of statements, branches and functions of first-party `public/**/*.js` |
| Python style | `ruff check`, `ruff format`, `mypy app` | Clean |
| UI style | `bash format-ui.sh --write`, `bash lint-ui.sh --check` | Clean |

### Backend tests

There are about 310 test files in `services/api/tests/`. Most drive the real application over HTTP against a real PostgreSQL database; very little is mocked.

The fixture to learn first is `keen`, from `tests/test_assembled_authority_regressions.py`. It gives you the assembled app, a database session and helpers to make users:

```python
from tests.test_assembled_authority_regressions import keen  # noqa: F401


def test_a_reader_sees_the_register(keen):
    reader = keen.client(keen.user("risk.read"))
    assert reader.get("/v1/risks").status_code == 200
    assert keen.client(keen.user()).get("/v1/risks").status_code == 403
```

`keen.user(*permission_codes)` creates an account holding exactly those permissions, and `keen.client(user)` returns a signed-in HTTP client. Always test the user who is refused as well as the one who is allowed.

### UI tests

UI tests load the real HTML file and the real module into jsdom and answer its API calls from a function you write:

```js
import { page, settle } from './support/page-fixture.mjs';

const ui = await page(t, 'risk', {
  query: '&id=r1',
  me: { is_admin: true },
  get: async (url) => {
    if (url.pathname === '/api/v1/risks/r1') return risk;
    throw new Error('Unexpected GET ' + url);
  },
});
await settle(() => ui.el('riskTitle').textContent !== 'Risk');
```

`ui.el(id)` finds an element, `ui.calls` lists every request the page made, and `settle(condition)` waits for the page to reach a state. An unexpected request fails the test, so a page cannot quietly call something new.

### Tests that guard the structure

These fail when a rule of the codebase is broken, whatever the feature. When one fails on your change, it is telling you about a rule and not about a bug in the test.

| Test | The rule it holds |
| --- | --- |
| `test_access_policy_inventory.py` | Every route is listed in `tools/security_e2e/access_policy.py` with who may call it |
| `test_authorization_contracts.py`, `test_shared_route_guards.py` | Routes use the shared guards; no hand-rolled permission check |
| `test_event_loop_blocking.py` | No `async def` makes a blocking call |
| `test_source_registry.py` | Every source is declared once and fully |
| `test_link_migrations.py` | Migrations carry real data forward |
| `test_mcp_access.py` | MCP reaches only `INNER_ROUTES` |
| `test_redaction_contracts.py` | Secrets are redacted before storage |
| `rich-text-editors.test.mjs` (UI) | One way to start a rich-text editor |

## Traps

Things that have caught people, each with what to do instead.

**Reading the source registry at import time.** The registry imports every ingest module on first read, and those modules import services. A module-level `SOURCES = registry.sources()` in a service creates an import cycle. Read it inside a function. Only `worker/celery_app.py` and `worker/tasks.py` read it at import, because the worker needs its routes and tasks to exist then.

**`settings` in a collector.** `app.ingest.connections.settings` answers with the running connection's values; `app.core.config.settings` answers with the installation's. A collector that imports the second one ignores every named connection.

**A route that is not in the access policy.** Adding a route without its line in `tools/security_e2e/access_policy.py` fails the inventory test. So does removing a route and leaving its line.

**A new feature that is always on.** A route under a switchable feature needs its entry in `security/feature_policy.py`, or switching the feature off hides the page and leaves the API open.

**Framework scope.** Controls, clauses and PESTLE(E) items belong to one framework. Risks do not, but their links to controls and clauses do. A query that joins a risk to its controls without filtering `framework_slug` mixes catalogues.

**Cached graphs.** `cached_json` keys a result by the parts you give it. If what a user may see changes the result, that has to be one of the parts. The risk graph includes PESTLE(E) items only for a reader allowed to see them, so `pestle_items` is part of its key.

**JSON null in a migration.** A JSONB column can hold SQL `NULL` or the JSON value `null`, and `IS NULL` only finds the first. Migrations that read stored JSON check `jsonb_typeof`.

**Revision ids.** An Alembic revision id longer than 32 characters does not fit the version table.

**Eslint suppressions.** `services/ui/tests/eslint-suppressions.json` is maintained by `lint-ui.sh --prune`. Do not edit it by hand.

**The test database.** The suite rewrites its database. Point it only at a disposable one, and do not run two test runs against the same database at once: each will see the other's rows.

**Help pages.** `public/help/` is hand-written HTML, and Keen's copy is the source: keen.cloud mirrors it with the portal's `scripts/sync-help.py`. The search index is built from the pages. After editing a page, run `bash build-help-index.sh`; the UI tests fail while `search-index.js` differs from what the pages produce, so never edit the index by hand.

**The OSA catalogues.** 96 of the bundled frameworks come from Open Security Architecture, as a frozen snapshot in `services/api/alembic/catalogues/`. To take newer OSA data, build a snapshot with `tools/build_osa_snapshot.py` from a checkout of OSA's `osa-data` repository, then write a migration that applies it, as `0056_osa_snapshot_2026_11.py` does. That migration is the pattern to copy: it keeps every control's id and whatever the installation attached to it, never touches a framework that is not OSA's, and keeps a dropped control that is still in use, marked as superseded. Update `pages/framework-regions.js` and `OSA-NOTICE.txt` with the snapshot; a test compares the regions with it. OSA's notes about a control are a crosswalk to NIST SP 800-53, shown as one, and are never a control's description.
