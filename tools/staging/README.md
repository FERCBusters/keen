# Disposable staging for the security suite

These scripts keep a staging Keen installation in a known golden state, run
`tools/security_e2e` against it and then remove everything the run created.

- `keen-staging` runs on the staging host and manages the Docker Compose stack:
  generating its `.env`, building and restoring the golden snapshot, and tearing
  it down.
- `run-security-tests` runs on the machine that runs the suite. It restores the
  host over SSH, runs the suite, saves the service logs and tears the host down.
- `staging.env.example` is the configuration template for the host.
- `docker-compose.staging.yml` pins the Docker network so Keen trusts exactly
  its own proxy hops.
- `golden_extras.py` adds stored evidence files (text, JSON, PDF, HTML and CSV)
  to the golden data.
- `nginx-site.example` and `Caddyfile.example` are TLS fronts for the host.

The golden state is the demonstration ISMS dataset from
`services/api/scripts/seed_demo_data.py`, across all frameworks (ISO 27001,
SOC 2, PCI DSS, the Keen Assurance Framework and Cyber Essentials, with ISO
27001 as the organisation default): organisation roles, assets, risks, PESTLE
items, interested parties, documents, meetings, objectives, audits,
effectiveness measures, stories and evidence events, including the automatic
diary entries for document control links. Real data matters to the suite: list
pages, search, charts and aggregates take code paths that an empty installation
never reaches, and downloads and PDF previews need stored files.

## One-time host setup

On the staging host (Docker with Compose, nginx or Caddy, `curl`, `openssl`):

```sh
git clone <your Keen repository> /opt/keen
cd /opt/keen
tools/staging/keen-staging generate-env https://keen-staging.example.org
sudo install -d -m 700 -o "$USER" /var/lib/keen-staging
tools/staging/keen-staging init-golden
```

`generate-env` writes `/opt/keen/.env` (mode 600) with fresh database, broker,
encryption and webhook secrets and a random password for the suite
administrator, `e2e-admin`. Review it before `init-golden`; anything not marked
with `@...@` in the template can be changed. The file carries
`STAGING_DESTROYABLE=yes`, and every destructive command refuses to run without
it.

`init-golden` deletes any existing staging containers and data, starts an empty
installation (migrations, then the administrator from `.env`), seeds the
demonstration data, adds the stored evidence files and saves the snapshot to
`/var/lib/keen-staging/golden`. Run it again whenever you want the golden state to
reflect a newer Keen; the previous snapshot is kept as `golden.previous`.

For TLS, use `nginx-site.example` if the host already runs nginx, or
`Caddyfile.example` for Caddy. Either front must replace any client-supplied
`X-Forwarded-For` with the connecting address and proxy to `127.0.0.1:8081`;
Keen trusts only the UI container and the Docker gateway behind the front. The
nginx example also hides its version, matches Keen's body limit and passes
WebSocket upgrades, all of which the suite checks.

Restrict the site to the machines that run the suite (`allow`/`deny` in nginx,
`remote_ip` in Caddy). This does not affect the results as long as the suite
runs from an allowed address: the checks act on the socket address, which no
request header can change. Keep the certificate challenge path on port 80 open
if your certificates renew over HTTP. The base Compose file also publishes
PostgreSQL, Valkey and the API on loopback ports 5442, 6399 and 8181, and the UI
on 8081; make sure nothing else on the host uses them.

To test your working tree instead of the published images, set
`KEEN_STAGING_BUILD=1` for `keen-staging`; it adds `docker-compose.build.yml`
and builds before starting.

To add more golden data, put Python scripts in a directory and set
`KEEN_STAGING_HOOKS` to it when running `init-golden`. Each script runs inside
the API container after the built-in seeding, like `golden_extras.py`.

## Test machine setup

Run the suite from a machine other than the staging host, so the
forwarded-address checks see an untrusted client address.

```sh
cd tools/security_e2e
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
mkdir -p ~/.config/keen-staging
cat > ~/.config/keen-staging/config <<'EOF'
KEEN_STAGING_URL=https://keen-staging.example.org
KEEN_STAGING_SSH=deploy@keen-staging.example.org
KEEN_STAGING_REMOTE_DIR=/opt/keen
EOF
```

The SSH account needs to run Docker on the host. The administrator password is
read from the host's `.env` over SSH for each run, so it is not stored on the
test machine; set `KEEN_E2E_ADMIN_PASSWORD` to override that.

## Running

```sh
tools/staging/run-security-tests                     # restore, test, tear down
tools/staging/run-security-tests --keep-on-failure   # keep the instance if anything fails
tools/staging/run-security-tests --keep -- test_06_fuzz.py -x
tools/staging/run-security-tests --no-restore --keep -- -k forwarded
```

Arguments after `--` go to pytest. Each run writes `junit.xml` and
`services.log` (all container logs) to `./staging-results/<time>/`, and reports
how many Python tracebacks the services logged. A traceback with no failing test
still deserves a look: it can come from a background task the suite does not
observe directly.

An interrupted run (Ctrl-C) or a failed restore is cleaned up like any other
run, unless `--keep` or `--keep-on-failure` applies.

## Investigating a failure

After `--keep-on-failure` or `--keep`, the instance stays exactly as the run
left it, including the synthetic accounts and records, which carry the run ID
shown in the report. Sign in at the staging URL as `e2e-admin`, read the logs
with `keen-staging logs` on the host, and rerun individual tests against the same
state:

```sh
tools/staging/run-security-tests --no-restore --keep -- test_03_authorisation.py -k risks
```

When finished, run `keen-staging teardown` on the host, or start the next normal
run, which restores the golden state first.

## What teardown removes

`keen-staging teardown` deletes the project's containers, its Docker network and
every named volume: the PostgreSQL database, Valkey's sessions and rate-limit
counters, stored artifacts and the Celery beat schedule. The golden snapshot and
`.env` stay on the host for the next run. Nothing from a run survives a restore
either, because restore starts from the same full wipe.

## Commands on the host

| Command | Effect |
| --- | --- |
| `keen-staging generate-env URL` | Write `.env` from the template with new secrets |
| `keen-staging init-golden` | Wipe, start empty, seed, and save the golden snapshot |
| `keen-staging snapshot` | Save the current state as the golden snapshot |
| `keen-staging restore` | Wipe, restore the golden snapshot and start Keen |
| `keen-staging teardown` | Wipe everything except the golden snapshot and `.env` |
| `keen-staging status` | Show services, the snapshot manifest and the schema revision |
| `keen-staging logs` | Print all service logs |
| `keen-staging admin-password` | Print the suite administrator's password |

`restore` verifies the snapshot's checksums first. If the checked-out Keen has
newer migrations than the snapshot, they run on start and `restore` says so;
run `init-golden` to bring the snapshot up to date.
