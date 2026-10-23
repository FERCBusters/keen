# Keen security end-to-end suite

This suite tests a running Keen installation through its real nginx front end,
API, sessions, database and browser UI. It signs in as an administrator,
creates synthetic accounts with single permissions, seeds records whose text
fields carry hostile markup, and then checks that the deployment enforces the
security model in SECURITY.md:

| Module | Checks |
| --- | --- |
| `test_01_surface.py` | Every API operation in the OpenAPI schema needs a session unless it is a reviewed public route; API documentation needs a session; pages need sign-in; administrator pages are hidden; response headers, CSP and caching; encoded and dotted path variants; unusual methods; oversized bodies; server version disclosure |
| `test_02_sessions_csrf.py` | Session cookie attributes, fresh session IDs, failed sign-in parity, logout revocation, CSRF token and Origin enforcement, cross-site sign-in, form posts without JavaScript, WebSocket Origin and session checks |
| `test_03_authorisation.py` | A read matrix and a write-denial matrix across roles, aggregate evidence withholding, self-escalation attempts, the risk-owner role boundary, audit ownership and question permissions |
| `test_04_browser_rendering.py` | Seeded hostile text on detail and list pages, as an administrator and as read-only roles, in Chromium with the production CSP: injected elements, script execution, unsafe links and CSP violations. Diary links are tested one by one |
| `test_05_redirects.py` | Post-sign-in destinations, SSO start and MFA pages with hostile `next` values, API redirect locations |
| `test_06_fuzz.py` | Query and path parameter fuzzing from the OpenAPI schema, malformed and deeply nested JSON, wrong JSON types on create operations, chart window budgets. A server error or a slow response is a finding |
| `test_07_uploads_downloads.py` | Hostile uploads (HTML, SVG, spoofed images, header-injection and traversal names) come back as sanitised attachments; question attachments are validated and kept out of evidence; control characters in form text |
| `test_08_concurrency.py` | Simultaneous requests against single-use TOTP codes, MFA completion and enrolment limits; concurrent sign-ins and per-session logout |
| `test_09_access_policy.py` | Every API operation has a rule in `access_policy.py`; one probe account, whose permissions change between groups of requests, checks that each rule's permitted sets can read and that the largest refused sets cannot read or write. An account without permissions must see no seeded record anywhere |

A finding is reported with the route, role and status only. Response bodies,
cookies and credentials are not printed.

## Adding or changing an API route

Give every new route a rule in `access_policy.py` (public, signed in,
administrator, or the permissions that grant it). The backend test
`services/api/tests/test_access_policy_inventory.py` fails until you do, and
`test_09_access_policy.py` then checks the deployment enforces it. Record
ownership or object-visibility rules in `OBJECT_RULES` and test them in the
backend or in `test_03_authorisation.py`.

## Use an installation you control

Run the suite only against a Keen installation you operate, preferably a
disposable copy with synthetic data. It creates users, records and an agent
enrolment profile, uploads files, and sends malformed input at a sustained rate.
It never needs production credentials.

Two interlocks prevent accidental targets:

- `KEEN_E2E_CONFIRM_HOST` must repeat the hostname in `KEEN_E2E_BASE_URL`.
- Without `KEEN_E2E_ALLOW_WRITES=1` the suite only reads; most modules skip.

## Staging instance

`tools/staging` provides a disposable staging host for this suite: a golden
dataset with realistic records and stored files, a one-command restore, and a
runner that restores the host, runs the suite, saves the service logs and tears
everything down. See its README.

## Install

Python 3.11 or later and a Chromium build are required.

```sh
cd tools/security_e2e
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium   # or set KEEN_E2E_BROWSER_EXECUTABLE
```

## Configure

Credentials come from environment variables only, so they do not appear in
process listings or reports.

| Variable | Purpose |
| --- | --- |
| `KEEN_E2E_BASE_URL` | Origin of the installation, for example `https://keen-test.example.org` |
| `KEEN_E2E_CONFIRM_HOST` | The same hostname, as confirmation |
| `KEEN_E2E_ADMIN_USERNAME`, `KEEN_E2E_ADMIN_PASSWORD` | A local administrator account created for testing |
| `KEEN_E2E_ADMIN_TOTP_SECRET` | The administrator's authenticator secret, when MFA is required |
| `KEEN_E2E_ALLOW_WRITES=1` | Create synthetic users, records, uploads and an enrolment profile |
| `KEEN_E2E_ALLOW_EVIDENCE_WRITES=1` | Also create a synthetic diary event for question, link and event-page checks. Evidence has no delete route, so this event remains, labelled with the run ID |
| `KEEN_E2E_CA_FILE` | CA bundle for a private certificate authority; hostname verification stays on |
| `KEEN_E2E_BROWSER_TRUST_CERT` | PEM certificate for a lab installation: the browser accepts this one key and still rejects other certificate errors |
| `KEEN_E2E_BROWSER_EXECUTABLE` | Chromium executable, when not using Playwright's download |
| `KEEN_E2E_REQUEST_DELAY` | Seconds between API requests per connection (default 0.05) |
| `KEEN_E2E_FUZZ_CASES`, `KEEN_E2E_SEED` | Fuzzing budget (default 400) and random seed (default 1) |
| `KEEN_E2E_SLOW_SECONDS` | Response time reported as a finding (default 15) |

## Run

```sh
export KEEN_E2E_BASE_URL=https://keen-test.example.org
export KEEN_E2E_CONFIRM_HOST=keen-test.example.org
export KEEN_E2E_ADMIN_USERNAME=e2e-admin
read -rs KEEN_E2E_ADMIN_PASSWORD && export KEEN_E2E_ADMIN_PASSWORD
export KEEN_E2E_ALLOW_WRITES=1 KEEN_E2E_ALLOW_EVIDENCE_WRITES=1
.venv/bin/python -m pytest
```

Run one module with `.venv/bin/python -m pytest test_06_fuzz.py`, or vary the
fuzzing with `KEEN_E2E_SEED=7 KEEN_E2E_FUZZ_CASES=3000`. A full run takes
several minutes; most of that is browser rendering and sign-in pacing.

Keen limits sign-in attempts per client address (30 per five minutes by
default). The suite waits out the limit when it reaches it, which can add
several minutes. On a disposable installation, raising
`KEEN_LOGIN_RATE_LIMIT_PER_IP` for the test client shortens runs.

## Clean-up

At the end of a run the suite deletes the users and records it created, newest
first, and revokes its enrolment profile. Some traces remain by design: audit
trail entries, entity change logs, the revoked profile and any agent it
enrolled, and the synthetic diary event when evidence writes are enabled. Each
carries the run ID (`e2e` followed by eight hexadecimal characters). Anything the
suite could not remove is printed at the end of the run.

## Reading results

- A failing check names the route, role, payload index or page involved.
  Reproduce it with the same seed and module.
- `seeded_text_not_shown` means a page did not display the seeded record, so its
  rendering was not tested; check the page or the seed data.
- OIDC checks skip when OIDC is not configured.

Passing this suite does not prove the absence of vulnerabilities. It
complements the backend contract tests, the frontend DOM tests, manual review
and penetration testing. `tools/security_fuzz` remains available for offline
helper fuzzing and small manifest-driven probes against a loopback API.
