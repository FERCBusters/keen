# Keen (Keen Auditable Notation)

Keen is an evidence-collation engine for audit readiness, designed to turn day-to-day operational activity into **immutable, queryable evidence** mapped to controls/clauses across one or more frameworks (e.g. ISO/IEC 27001:2022, UK DVSTF).

This repo intentionally starts with the **core loop**:
1) store normalized events in PostgreSQL
2) store immutable evidence artifacts on a persistent local disk volume by default, or in S3-compatible storage, with SHA-256 hashing
3) ingest “today’s” logs from Grafana Loki via `query_range`
4) map events to controls using a simple YAML rules engine
5) accept inbound webhooks as first-class evidence

## Quick start (Docker Compose)

1. Copy env:
```bash
cp .env.example .env
```

2. Start:
```bash
docker compose up --build
```

3. Smoke test:
```bash
curl -s http://localhost:8181/health | jq .
```

4. Open the UI and log in:
   - UI: http://localhost:8081

5. Trigger Loki ingestion manually via API (optional):
```bash
# Log in and store the session cookie
curl -c cookies.txt -b cookies.txt \
  -H 'Content-Type: application/json' \
  -X POST http://localhost:8181/v1/auth/login \
  -d '{"username":"admin","password":"changeme-admin"}'

# Then run an admin action
curl -b cookies.txt -X POST http://localhost:8181/v1/admin/ingest/loki/run
```

## Services
- `keen-api` : FastAPI app
- `keen-worker` : Celery worker (tasks: Loki ingestion, mapping)
- `keen-beat` : Celery beat (scheduled ingestion)
- `postgres` : database
- `valkey` : Redis-compatible broker/backend (Valkey)
- `artifacts` volume: evidence files shared by the API and worker

## Key config
- Connectors: GitHub, Jenkins, Taiga, BookStack, RSS are *off by default*; enable with `KEEN_*_ENABLED=true`.
- Enable Loki ingestion: set `KEEN_LOKI_ENABLED=true` and point `KEEN_LOKI_BASE_URL` at your Loki.
- Enable CloudWatch Logs ingestion: set `KEEN_CLOUDWATCH_LOGS_ENABLED=true` and configure `./config/cloudwatch_logs.yml`.
- Loki base URL + auth: `KEEN_LOKI_*`
- Evidence storage: `KEEN_ARTIFACT_STORAGE_BACKEND=auto|local|s3` and `KEEN_ARTIFACT_LOCAL_DIR` (default `/app/data/artifacts`). Auto selects disk with no S3 settings and S3 with complete `KEEN_S3_*` settings. A partial S3 configuration fails evidence writes rather than silently changing storage.
- The Docker `artifacts` named volume is persistent across rebuilds. Back it up with the database; `docker compose down -v` deletes it. To use a host directory instead, bind mount it at `/app/data/artifacts` in both the API and worker and make it writable by UID 10001. Existing `s3://` evidence remains readable if S3 credentials stay configured, even when writes use disk.
- Authentication:
  - Bootstrap admin (first-run only): `KEEN_BOOTSTRAP_ADMIN_USERNAME`, `KEEN_BOOTSTRAP_ADMIN_PASSWORD`
  - Session cookies: `KEEN_SESSION_*`, `KEEN_COOKIE_*`
- Mapping rules: `KEEN_RULES_PATH` (default `./config/rules.yml`)
- Default framework slug for API/UI filtering: `KEEN_DEFAULT_FRAMEWORK` (default `ISO27001:2022`)
- Framework seed files directory: `KEEN_FRAMEWORKS_DIR` (default `./frameworks`)
- Loki queries: `KEEN_LOKI_QUERIES_PATH` (default `./config/loki.yml`)
- CloudWatch Logs queries: `KEEN_CLOUDWATCH_LOGS_CONFIG_PATH` (default `./config/cloudwatch_logs.yml`)
- Webhook policies: `KEEN_WEBHOOKS_PATH` (default `./config/webhooks.yml`)
- Aggregate UI cache: `KEEN_AGGREGATE_CACHE_TTL_SECONDS` (default `60`; set `0` to disable). This uses Valkey/Redis for expensive dashboard/source/facet counts while keeping exact count semantics.
- Optional IP/email masking in event data: `KEEN_EVENT_DATA_MASKING=false|samples|true` (default `false`). `samples` masks only event/audit PDF and ZIP exports; `true` masks at ingestion time before storage.

### Outbound question notifications (webhooks)

When a user asks a question against an event (auditor Q&A), Keen can optionally send a
webhook notification to external systems (in addition to the in-product WebSocket bell).

Configure one or more destinations via environment variables (comma/space/newline separated):

- Slack incoming webhooks: `KEEN_QUESTION_WEBHOOK_SLACK_URLS`
- Microsoft Teams incoming webhooks: `KEEN_QUESTION_WEBHOOK_TEAMS_URLS`
- Google Chat incoming webhooks: `KEEN_QUESTION_WEBHOOK_GOOGLE_CHAT_URLS`
- Generic webhooks (for Node-RED, etc.): `KEEN_QUESTION_WEBHOOK_GENERIC_URLS`

Optional helpers:

- `KEEN_PUBLIC_BASE_URL` to generate absolute links in messages (e.g. `https://keen.example.com`)
- `KEEN_QUESTION_WEBHOOK_GENERIC_HEADERS_JSON` to add headers to generic webhooks
- `KEEN_QUESTION_WEBHOOK_SIGNING_SECRET` (HMAC-SHA256) + `KEEN_QUESTION_WEBHOOK_SIGNATURE_HEADER`

Generic webhook payloads are sent as JSON with `type: keen.question.created` and include the event,
thread, post, mapped controls, and UI links.

Reply notifications (new posts on an existing question thread) use `type: keen.question.replied`.
Configure separate destinations via `KEEN_QUESTION_REPLY_WEBHOOK_*_URLS`, or (by default) Keen will
reuse the question-created destinations when the reply URL lists are empty. To disable this fallback,
set `KEEN_QUESTION_REPLY_WEBHOOK_USE_QUESTION_URLS_IF_EMPTY=false`.

### Event incident creation webhook

When `KEEN_INCIDENT_WEBHOOK_URL` is set, individual event pages show a **Create Incident** button
to admins and to users/groups granted `incident.create`. Submitting the modal sends a canonical JSON
payload to the configured webhook and, after a 2xx response, records a local incident marker on the
event and a semantic `incident_created` entry in the Keen audit trail.

Optional helpers:

- `KEEN_PUBLIC_BASE_URL` to generate absolute event URLs (e.g. `https://keen.example.com`)
- `KEEN_INCIDENT_WEBHOOK_HEADERS_JSON` to add headers such as bearer tokens
- `KEEN_INCIDENT_WEBHOOK_SIGNING_SECRET` (HMAC-SHA256) + `KEEN_INCIDENT_WEBHOOK_SIGNATURE_HEADER`
- `KEEN_INCIDENT_WEBHOOK_TIMEOUT_SECONDS` to tune the outbound POST timeout

The payload uses `type: keen.incident.created` and includes `incident.title`, `incident.text`,
the Keen event UUID, and `event.url`/`links.event` for the originating event page.

### AWS CloudWatch Logs connector

When `KEEN_CLOUDWATCH_LOGS_ENABLED=true`, Keen polls CloudWatch Logs using the AWS SDK (`boto3`) and stores a raw message artifact per matched log event.

The connector uses `filter_log_events` under the hood. Configure one or more queries/streams in `./config/cloudwatch_logs.yml`:

```yaml
defaults:
  lookback_minutes: 15
  overlap_seconds: 2
  limit: 10000
  max_pages: 50

queries:
  - name: cloudtrail
    region: ap-southeast-2
    log_group: /aws/cloudtrail/your-account
    filter_pattern: ""
    # Optional:
    # log_stream_prefix: "2026/01/"
    # log_stream_names: []
    event:
      system: aws
      action: cloudwatch_log_event
      outcome: info
      severity: 3
```

Authentication/permissions come from the standard AWS credential chain (env vars, shared config, instance profile, etc.).

### RSS / Atom connector

When `KEEN_RSS_ENABLED=true`, Keen polls any RSS/Atom feed(s) defined in
`./config/rss.yml` and stores a JSON artifact per feed entry.

This is intended as a "catch-all" connector for vendor advisories, internal
news feeds, status pages, etc.

### GitHub connector

When `KEEN_GITHUB_ENABLED=true`, Keen reads `./config/github.yml` (mounted into the container at `/app/config/github.yml`) and polls GitHub's Events API.

The connector supports:
- org events (`/orgs/{org}/events`, or `/users/{username}/events/orgs/{org}` when `GITHUB_USERNAME` is set)
- per-repo events (`/repos/{owner}/{repo}/events`), optionally enumerating all repos in an org

See `./config/github.yml` for an annotated example.

### BookStack connector

When `KEEN_BOOKSTACK_ENABLED=true`, Keen uses the BookStack API to poll for
page updates (ideal when your ISMS lives in BookStack as a book).

Required env vars:
- `KEEN_BOOKSTACK_BASE_URL` (e.g. `https://wiki.example.com`)
- `BOOKSTACK_TOKEN_ID`
- `BOOKSTACK_TOKEN_SECRET`

Configuration lives in `./config/bookstack.yml`.

Key options:
- `book.slug` or `book.id` to restrict ingestion to a single BookStack book
- `books` (list of slugs/ids) to restrict ingestion to multiple books (ignored if `book:` is set)
- `capture.preview_chars` (default 500) to store only a short preview snippet
  plus URLs (no full page content)
- `page_mappings` to map specific pages to framework refs (set `framework:` per mapping or rely on default)
  - Match by `id`, exact `slug`, `slug_regex`, `title`, or `title_regex`
  - If you track multiple books and want to match by `slug` without using numeric IDs, add
    `match.book_slug` (or `match.book: {slug: ...}`) to disambiguate slugs across books


## Evidence definitions in the admin interface

Admin → **Evidence definitions** manages collection, matching and mappings in one
place. Pick a source adapter and a collection item such as a Loki query, Jenkins
job or BookStack page. Describe what its evidence demonstrates, optionally narrow
it by event fields, and select controls or clauses in any number of frameworks.
A definition may also apply to all events from a source; this is useful for
rules that match by action or outcome across multiple collection items. One event
can support several frameworks and controls.

On `alembic upgrade head`, the database migration imports the existing YAML
presets or current Postgres overrides and converts all rules into this format.
Existing framework targets, enabled state, IDs and confidence values are retained.
Rules are linked to a collection item when its existing conditions identify a
single item; broader matches appear as “All events from this source.” BookStack
page mappings become editable evidence definitions; their page selectors continue
to seed page collection. A fresh install receives the same conversion during its
first migration. From then on Postgres is authoritative; the YAML files are
bootstrap inputs for this migration. Keep a database backup before upgrading.
Previously collected evidence and its mappings are preserved. Rule edits can
apply to historical evidence through a resumable background job.

Jenkins **kind** sets the action on evidence emitted by a job, such as `build`
or `deployment`. Its **label** sets the event system. The wizard explains the
match fields and lets administrators preview against recent events. Select
**Predefine** to create definitions before the first event arrives. BookStack's
page picker reads names and structure through the server-side API client; its
credentials stay in the container environment and are never sent to the browser.
Shared adapter settings are in a separate advanced panel. Docker environment
variables still provide connection credentials and enable adapters.

## Multi-framework mapping and filtering

Keen supports mapping the same event to refs in multiple frameworks.

- UI pages accept `?framework=<slug>` and the navbar includes a framework switcher.
- Users can set a personal **default framework** in Account → Preferences; when no `?framework=` is present, Keen uses that value on sign-in.
- API endpoints for controls/events/stats/graph accept `framework` query params.
- Rules support per-framework targets (see comments at the top of `config/rules.yml`).
- Seed files can be imported per framework via Admin → Import controls (for example `frameworks/iso27001_2022_minimal.yml` or `frameworks/uk_dvstf_10_minimal.yml`).

## Webhook ingestion example

Configure `webhooks.yml`, then send:
```bash
curl -X POST http://localhost:8181/v1/webhooks/something/diff   -H 'Content-Type: application/json'   -H 'X-My-Secret: changeme-secret'   -d '{"example":"payload"}'
```

## Notes on framework control text
Many framework control texts are copyrighted or licensed. Keen ships **minimal seed registries** with refs only and expects you to import your organisation's authoritative control library (refs, titles, scope, rationale) per framework slug.

---
MIT License (see LICENSE).

## Manual diary entry

```bash
# Log in and store the session cookie
curl -c cookies.txt -b cookies.txt \
  -H 'Content-Type: application/json' \
  -X POST http://localhost:8181/v1/auth/login \
  -d '{"username":"admin","password":"changeme-admin"}'

# Then create the diary entry (admin role required)
curl -b cookies.txt -X POST http://localhost:8181/v1/admin/diary \
  -H 'Content-Type: application/json' \
  -d '{"summary":"DR test performed","details":{"result":"pass"}}'
```

## Optional S3 storage

To use an S3-compatible provider, configure `KEEN_S3_ENDPOINT_URL`, `KEEN_S3_ACCESS_KEY`, `KEEN_S3_SECRET_KEY`, and `KEEN_S3_BUCKET` in `.env`. Set `KEEN_ARTIFACT_STORAGE_BACKEND=s3` explicitly or leave it at `auto`. To resume writing evidence to disk, set the backend to `local`; retain the S3 settings if old S3 evidence still needs to be downloaded.
