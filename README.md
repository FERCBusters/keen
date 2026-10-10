# KEEN — Key Evidence ENgine

KEEN brings operational evidence, assurance frameworks, risk management and ISMS records into one workspace. It collects activity from your systems, maps evidence to controls, and helps people review that evidence during audits.

## What you can do

- **Collect evidence:** ingest logs, development activity, service updates and API records, receive webhooks, and record manual diary evidence with attachments.
- **Map across frameworks:** define rules that associate events with controls in multiple frameworks, preview their matches, and apply saved rules to past evidence.
- **Manage assurance:** set framework scope, maintain a Statement of Applicability, review related controls, and inspect the evidence behind coverage counts.
- **Manage risks and ISMS records:** maintain risk registers and treatments, use the reusable risk library and KEEN Mitigator, and link policies, assets, objectives, interested parties, measurements and meetings.
- **Conduct audits:** plan and schedule audits, sample evidence, record findings, discuss evidence through questions, and retain reports and document revisions.

The catalogue includes 94 frameworks, including 88 from Open Security Architecture. Administrators choose which frameworks to enable and can edit their controls and definitions. Evidence coverage indicates that evidence is present; reviewers assess its relevance, quality and sufficiency.

## Run with Docker Compose

You need Docker Engine and the Docker Compose plugin. Run these commands from this directory.

```sh
cp .env.example .env
chmod 600 .env
```

Before starting, edit `.env`:

- Set a strong, unique `KEEN_BOOTSTRAP_ADMIN_PASSWORD`. The bootstrap account is created only when the user database is empty.
- Replace `POSTGRES_PASSWORD` and `KEEN_REDIS_PASSWORD` with separate random passwords. The supplied database and Redis URLs interpolate these values; URL-encode reserved characters if you write connection URLs yourself.
- Set `KEEN_PUBLIC_BASE_URL` to the browser-facing origin. For an HTTPS deployment, set `KEEN_COOKIE_SECURE=true` and configure a TLS reverse proxy to the UI service.
- Set `KEEN_INTEGRATION_SECRET_KEY` to a persistent Fernet key before saving source credentials. API and worker services must use the same key. Generate one with `openssl rand -base64 32 | tr '+/' '-_'` and keep it in your secrets manager and protected backups.
- Enable the source types you need with their `KEEN_*_ENABLED` settings.

Start the services:

```sh
docker compose pull
docker compose up -d
```

The API runs database migrations during startup. The worker and scheduler wait for it to become healthy.

Open <http://localhost:8081> for local access, or your configured HTTPS address, and sign in with the bootstrap account. The Compose configuration binds published ports to loopback. Route browser and agent traffic through the UI reverse proxy; the browser-facing API prefix is `/api/v1/`.

Check startup with:

```sh
docker compose ps
docker compose logs --tail=100 keen-api keen-worker
```

For a local source build:

```sh
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

## Connect your sources

Open **Admin → Sources & Evidence Mapping**:

1. **Define a source.** Choose an enabled source type, give the connection a name, and enter its endpoint and credentials.
2. **Add inputs.** Select what to collect, such as a GitHub organisation, Forgejo user, Loki query or Jenkins job. Each RSS source contains its feed URL and optional credentials.
3. **Collect evidence.** Run collection and inspect the resulting events.
4. **Add mapping rules.** Select the source connection and input, choose matching conditions, and select the controls the evidence supports.

Supported sources include GitHub, GitLab, Forgejo, Gitea, Jenkins, Loki, CloudWatch Logs, Google Workspace, Redmine, Taiga, BookStack, Risk Ledger, RSS/Atom and incoming webhooks. The custom API ingester builder supports declarative HTTP requests, field extraction, pagination and scheduling.

You can configure several named connections of the same type. KEEN records the connection identity on collected events. Credentials entered in the UI are encrypted in PostgreSQL using `KEEN_INTEGRATION_SECRET_KEY`. Environment credentials and file-managed connections are also supported; see `.env.example` and the **Help** administrator handbook for their configuration.

Built-in HTTP collectors require HTTPS, restrict authenticated Git feeds to their connection’s server, and limit individual responses to 16 MiB. They request uncompressed responses and ignore ambient proxy settings. Set `KEEN_INGESTION_ALLOWED_CIDRS` to restrict access to private networks; an empty value permits private networks except prohibited addresses such as loopback and link-local.

Custom API destinations require `KEEN_INTEGRATION_ALLOWED_HOSTS`; approved private destinations also require `KEEN_INTEGRATION_PRIVATE_CIDRS`. Configure upstream credentials with the access needed for collection.

## KEEN-agent

[KEEN-agent](https://github.com/FERCBusters/keen-agent) collects evidence from hosts and sends it over authenticated HTTPS. Create an individual agent credential for a persistent host, or an enrollment profile for a fleet. A profile has a shared enrollment key; each enrolled agent receives its own expiring credential. Revoking a profile revokes its associated agent credentials.

The agent setup and enrollment instructions are in **Help**. Install and configure the agent from its own repository.

## Access and authentication

KEEN supports local accounts, LDAP, OpenID Connect, Google and GitHub sign-in, user/group permissions, and local second-factor authentication with TOTP, security keys/passkeys and recovery codes. Administrators assign access to evidence, risks, ISMS records, audits and administration.

Set `KEEN_PUBLIC_BASE_URL` before configuring authentication. TOTP requires a separate persistent `KEEN_MFA_ENCRYPTION_KEY`. Follow the authentication chapters in **Help** for callback URLs, account linking and MFA policy.

## Storage and operation

PostgreSQL holds application records. Valkey provides sessions, task queues, rate limits and caches. Celery workers collect evidence and run background jobs; Celery beat schedules them. The UI is served by nginx, which proxies API requests.

Evidence artefacts use the persistent `artifacts` volume by default, with SHA-256 checksums recorded in KEEN. S3-compatible storage is available through `KEEN_ARTIFACT_STORAGE_BACKEND` and the `KEEN_S3_*` settings. Storage-level retention and write protection depend on the selected backend and its configuration.

Back up PostgreSQL, artefacts and encryption keys together, and test restoration. Preserve S3 credentials when older evidence still resides there. `docker compose down -v` deletes the Compose-managed data volumes.

Use KEEN's retention controls to manage evidence lifetimes. Configure source schedules, review failed ingestion runs, and keep deployment images and dependencies maintained. Detailed setup, backup and administration guidance lives in the application’s **Help** section and the [portal help](https://keen.cloud/help/).

## Development and tests

- `services/api/`: FastAPI application, Celery tasks, Alembic baseline and backend tests.
- `services/ui/`: browser UI, nginx configuration and frontend tests.
- `config/`: source and mapping configuration examples.
- `frameworks/`: framework source data and attribution.

The CI workflow in `.github/workflows/tests.yml` installs locked dependencies and runs `bash tests.sh` against disposable PostgreSQL and Valkey services. Set `KEEN_TEST_DATABASE_URL` to a dedicated test database and `KEEN_REDIS_URL` to the test broker before running it. The suite changes database contents. See [TESTING.md](TESTING.md) for the development test environment.

## Security

See [SECURITY.md](SECURITY.md) for the threat model, deployment responsibilities and vulnerability reporting policy. Contributor and coding-agent guidance is in [AGENTS.md](AGENTS.md).

## Licence and catalogue attribution

KEEN software is licensed under [Apache License 2.0](LICENSE).

The KEEN Assurance Framework incorporates Vanta's Apache-2.0-licensed control set. OSA-derived catalogue data and adaptations are distributed under CC BY-SA 4.0. See [NOTICE](NOTICE) and the bundled licence files for attribution and applicable terms. Consult the underlying standards for authoritative requirements.
