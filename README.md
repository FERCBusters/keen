# Keen - Key Evidence ENgine

[![Backend coverage](https://github.com/FERCBusters/keen/raw/badges/coverage-api.svg)](https://github.com/FERCBusters/keen/actions/workflows/tests.yml?query=branch%3Amain)
[![Frontend coverage](https://github.com/FERCBusters/keen/raw/badges/coverage-ui.svg)](https://github.com/FERCBusters/keen/actions/workflows/tests.yml?query=branch%3Amain)
[![mypy](https://github.com/FERCBusters/keen/raw/badges/mypy.svg)](https://github.com/FERCBusters/keen/actions/workflows/tests.yml?query=branch%3Amain)
[![ruff](https://github.com/FERCBusters/keen/raw/badges/ruff.svg)](https://github.com/FERCBusters/keen/actions/workflows/tests.yml?query=branch%3Amain)

KEEN is an open-source, **evidence-first** ISMS and compliance platform. It continuously collects **real operational activity** from your infrastructure, maps it to compliance controls using transparent rules, and makes that evidence available for review and audit. Instead of relying solely on policies, questionnaires and declarations of compliance, KEEN helps organisations demonstrate **what** actually happened, **when** it happened and **how it relates** to their obligations.

Keen is for organisations that want to **operate an ISMS day-to-day**, grounded in **verifiable activity** from their own infrastructure, and **retain control** over how evidence is collected, mapped and scrutinised.

The goal of Keen is to be a totally open source, free compliance management platform that puts organisations and auditors on the same page (literally), with an 'evidence first' approach that avoids box-ticking, and which doesn't cost tens of thousands of dollars from walled garden platforms.

Compliance and security should be in reach of anyone, even those without big budgets.

## Features

- **Collect evidence:** ingest logs, development activity, service updates and API records, receive webhooks, and record manual diary evidence with attachments.
- **Map across frameworks:** define rules that associate events with controls in over 90 compliance frameworks, all of which are editable. You can choose want controls are in scope for or even add your own frameworks.
- **Watch each source's rhythm:** a source page compares recent activity with the source's own history, flags a source that has gone quiet, and lists quiet periods, daily spikes and repeated summaries with their unmapped counts.
- **Manage assurance:** set framework scope, maintain a Statement of Applicability, review related controls, and inspect the evidence behind coverage counts. Each control's Requirements tab brings together its linked policies and processes, its evidence, and its effectiveness measures with their latest results. Each control and clause can name the owners who answer for it, and each owner finds them under My controls.
- **Plan SWOT responses and objectives:** rate strengths, weaknesses, opportunities and threats, organise them in hierarchies, link organisational records and framework requirements, and visualise it in a variety of ways.
- **Manage risks and ISMS records:** maintain risk registers and treatments, access control and asset matrices, policies and procedures, objectives, measurements and minutes of meetings.
- **Conduct audits:** plan and schedule audits, sample evidence, record findings, discuss evidence through questions, and retain reports and document revisions.
- **Conduct training and awareness:** define quizzes of multiple-choice and written questions on your policies, assign them to people, accounts or organisation roles, and run them once or in scheduled rounds with invitations and reminders. Questions and options are shuffled for every sitting, written answers are graded blind. Each round's average is recorded on the quiz's effectiveness measure for the controls it tests.
- **Keep people informed:** each person's notifications tell them when an audit they attend starts, when they are asked to take a quiz, when minutes are added for a meeting they attended, and when a measure they own falls outside its threshold, and link straight to the record.
- **Connect AI apps:** when an administrator enables it, people connect AI apps such as Claude to Keen over the Model Context Protocol (MCP). Each connection reads only the records its person approves, within their own permissions, and its access renews hourly until they revoke it. With changes also enabled, a connection can help onboard an organisation: add policies and processes, objectives, assets, people, vendors / 3rd parties and other ISMS records, import risks and SWOT items from a sheet, and, for administrators, add evidence mapping rules. It never deletes anything.
- **Add context to events through stories:** gather the events, risks, records and notes of one incident into a story with a timeline and a drag-and-drop flow chart, add notes and associate assets/policies. Stories, like everything else, can be sampled into audits.

## Try out a free demo

Visit https://keen.cloud and try a 30-day free demo, seeded with pretend data. This is the quickest way to get a feel for the application's features and experience layer. Only an e-mail address is required, purely to link it to the demo instance's authentication layer. We never use your e-mail address to send you any unsolicited mail.

## Installation

You need Docker Engine and the Docker Compose plugin. Run these commands from this directory.

```sh
cp .env.example .env
chmod 600 .env
```

Before starting, edit `.env` with at least the following:

- Set a strong, unique `KEEN_BOOTSTRAP_ADMIN_PASSWORD` of at least 15 characters, for example from `openssl rand -base64 24`; an empty installation does not start without one, and Keen refuses a placeholder or a commonly used password. The bootstrap account is created only when the user database is empty. After confirming you can sign in, remove both bootstrap variables and recreate the containers; the account and its current password remain in the database.
- Set `POSTGRES_PASSWORD` and `KEEN_REDIS_PASSWORD` to separate random passwords, for example from `openssl rand -hex 24`; Compose refuses to start while either is empty. The supplied database and Redis URLs interpolate these values; URL-encode reserved characters if you write connection URLs yourself.
- Set `KEEN_PUBLIC_BASE_URL` to the browser-facing origin, without a path. Keen does not start without it. For an HTTPS deployment, set `KEEN_COOKIE_SECURE=true` and configure a TLS reverse proxy to the UI service.
- Leave `KEEN_SECURITY_TRUSTED_PROXY_CIDRS` empty to trust the supplied proxy hops: Compose pins its network (172.30.65.0/24 unless you set `KEEN_NETWORK_SUBNET`, `KEEN_NETWORK_GATEWAY` and `KEEN_UI_ADDRESS`) so Keen can follow `X-Forwarded-For` through the keen-ui container and the network gateway to each client's address. Outside Compose, set it to your own proxies' addresses; otherwise every client shares the proxy's address and one client's failed sign-ins can block everyone's. A TLS proxy in front of Keen must append the connecting client's address to `X-Forwarded-For`, or overwrite the header: one that passes a client's own header through lets the client choose the address Keen records.
- Set `KEEN_INTEGRATION_SECRET_KEY` to a persistent Fernet key before saving source credentials. API and worker services must use the same key. Generate one with `openssl rand -base64 32 | tr '+/' '-_'` and keep it in your secrets manager and protected backups.
- Enable the source types you need with their `KEEN_*_ENABLED` settings.

Start the services:

```sh
docker compose pull
docker compose up -d
```

The API runs database migrations during startup. Workers and the scheduler wait for it to become healthy.

- `keen-worker` consumes `celery` for internal jobs, including notifications, caches, mapping backfills and scheduling.
- `keen-http-worker` consumes `ingest-http` for built-in HTTP polling, named-connection collection and custom API ingester runs.
- The optional `keen-imap-worker` consumes `imap`.

Both ordinary workers default to one child process. The HTTP worker has a 768 MiB memory limit, one CPU, a 128-PID limit, prefetch multiplier one and recycling after 100 tasks. Tune aggregate worker memory and concurrency to host capacity.

The Python API, workers and scheduler run as the image's non-root user with all Linux capabilities dropped and `no-new-privileges`; outbound TCP does not require added capabilities.

Open <http://localhost:8081> for local access, or your configured HTTPS address, and sign in with the bootstrap account. The Compose configuration binds published ports to loopback. Route browser and agent traffic through the UI reverse proxy; the browser-facing API prefix is `/api/v1/`.

Check startup with:

```sh
docker compose ps
docker compose logs --tail=100 keen-api keen-worker keen-http-worker
```

For a local source build:

```sh
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

## Next steps

### Connect your sources

Open **Admin → Sources & Evidence Mapping**:

1. **Define a source.** Choose an enabled source type, give the connection a name, and enter its endpoint and credentials.
2. **Add inputs.** Select what to collect, such as a GitHub organisation, Forgejo user, Loki query or Jenkins job. Each RSS source contains its feed URL and optional credentials.
3. **Collect evidence.** Run collection and inspect the resulting events.
4. **Add mapping rules.** Select the source connection and input, choose matching conditions, and select the controls the evidence supports. Built-in rules already map cloud posture checks, Keen Agent workstation observations and the server events the agent recognises to the Keen Assurance Framework; edit or disable them like any other rule. Keen also suggests rules for recent unmapped events, with the controls their wording matches; review them under **Suggested rules** and create each with one click.

Supported sources include GitHub, GitLab, Forgejo, Gitea, Jenkins, Loki, Elasticsearch, CloudWatch Logs, Google Workspace, Redmine, Taiga, BookStack, Risk Ledger, IMAP mail, RSS/Atom and incoming webhooks. The custom API ingester builder supports declarative HTTP requests, field extraction, pagination and scheduling. There is also a separate 'Cloud posture' system that supports reading your security posture from AWS, Microsoft Azure/Entra, Google Cloud, Akamai (Linode), DigitalOcean and Scaleway. These records get summarised under ISMS → People/Vendors → Vendors.

### AI agent access (MCP)

Set `KEEN_MCP_ENABLED=true` to let people connect AI apps such as Claude, Claude Code and Cursor to Keen through the Model Context Protocol. Keen must be served over HTTPS. Each person adds the server address shown under **Account → Security** (`https://<your Keen>/api/v1/mcp`) to their AI app, which opens Keen to approve the connection. Hosted assistants register only the OAuth callbacks listed in `KEEN_MCP_REDIRECT_URIS`; local apps use loopback callbacks. The tools are read-only unless you also set `KEEN_MCP_WRITE_ENABLED=true`, which lets people approve change scopes for adding and updating records within their own permissions; nothing is deleted over MCP. Help, under **Local accounts, MFA and security email**, describes the access model.

### Keen Agent

[Keen Agent](https://github.com/FERCBusters/keen-agent) sends signed evidence such as logs and other security events from servers (with support for common fleet enrollments to aid auto-connection from autoscale clusters). From the journal or syslog alone it recognises sign-ins, sudo and su, commands a root shell logs, account changes, refused access, kernel and service faults, clock synchronisation, name-resolution problems, PHP and Drupal errors, fail2ban bans, the daily apt upgrade job and certbot's renewals completing, and AppArmor loading at boot, without sending the log lines themselves. Everything it sends is encrypted end to end to your Keen instance, which each agent pins at enrolment, so reverse proxies, CDNs, inspecting middleboxes or an impostor holding a valid certificate for your Keen's address see only ciphertext and cannot acknowledge evidence away.

Keen Agent also supports being run on Linux, Mac and Windows workstations in the background, wherein it sends attestations as to correct security posture (workstation antivirus, disk encryption, screen locks, automatic updates, and so on). This works over over OTLP/HTTP JSON formats. Keen verifies every request, batch signature, envelope and chain position, checks typed fields against the event catalogue, records any redaction in a custody record and keeps each envelope's provenance.

A check that needs attention can be recorded as a risk against the workstation's asset. An accepted risk marks the check as accepted until its review date, and a closed risk marks it as treated until it fails in a way the risk predates.

See more about Keen Agent and how to install it on workstations at https://keen.cloud/agent


### Access and authentication

Keen supports local accounts, LDAP, OpenID Connect, Google and GitHub sign-in.

It has its own RBAC with user/group permissions, as well as local second-factor authentication with TOTP, security keys/passkeys and recovery codes.

TOTP requires a separate persistent `KEEN_MFA_ENCRYPTION_KEY`. Follow the authentication chapters in **Help** for callback URLs, account linking and MFA policy.

## Further Documentation

Detailed setup, backup and administration guidance lives in the application's **Help** section. Contextual help is offered on each page, as well as complete documentation located at `/help`, which is also available at our [portal site](https://keen.cloud/help/).

## Development and tests

- `services/api/`: FastAPI application, Celery tasks, Alembic baseline and backend tests.
- `services/ui/`: browser UI, nginx configuration and frontend tests.
- `config/`: source and mapping configuration examples.
- `frameworks/`: framework source data and attribution.

The CI workflow in `.github/workflows/tests.yml` installs locked dependencies and runs `bash tests.sh` against disposable PostgreSQL and Valkey services. Set `KEEN_TEST_DATABASE_URL` to a dedicated test database and `KEEN_REDIS_URL` to the test broker before running it. The suite changes database contents. See [TESTING.md](TESTING.md) for the development test environment.

## Security

See [SECURITY.md](SECURITY.md) for the threat model, deployment responsibilities and vulnerability reporting policy. Contributor and coding-agent guidance is in [AGENTS.md](AGENTS.md).

## Licence and catalogue attribution

Keen software is licensed under [Apache License 2.0](LICENSE).

The Keen Assurance Framework incorporates Vanta's Apache-2.0-licensed control set. OSA-derived catalogue data and adaptations are distributed under CC BY-SA 4.0. See [NOTICE](NOTICE) and the bundled licence files for attribution and applicable terms. Consult the underlying standards for authoritative requirements.

## Professional Support

Keen is completely open source and always will be.

However, getting it set up, maintained or extended is not something everyone wants to or is capable of doing.

Professional support for installation, ISMS migration/guidance, maintenance/upgrades, troubleshooting and custom feature development is available from [mig5 system administration](https://mig5.net/keen/), Keen's original inventor and principal developer. Please feel free to get in touch to discussion your needs.

Contributions to [sponsor](https://github.com/sponsors/mig5) ongoing development of Keen is also warmly appreciated.
