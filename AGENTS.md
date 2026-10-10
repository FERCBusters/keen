# Working on KEEN

## Scope and structure

- Read the relevant code and tests before editing. Prefer small changes to the layer that owns the behavior, using existing helpers and services.
- Keep HTTP handlers focused on validation, authorization and responses. Put shared security boundaries in `services/api/app/security/`, collection behavior in `ingest/`, and reusable domain operations in `services/`.
- Use the design system and shared UI helpers. Ask for the design system if you do not have that codebase to hand. Keep API authorization authoritative even when the UI hides an action.
- Preserve connection identity through credentials, inputs, cursors, caches, artifacts, events and mapping rules. Connection-local state must live inside `connection_scope`; process-global authentication caches are prohibited.
- Remove code only after checking all callers, configuration references and runtime entry points. Do not introduce compatibility machinery without a concrete supported use case.

## Security invariants

- All HTTP routes enter the main application's authentication, authorization, CSRF, body-limit and response-policy middleware. Public exceptions belong in `security/route_policy.py`, with exact methods and full route patterns and a test explaining the alternative authentication boundary. Never exempt a whole path prefix for convenience.
- New protected routes need explicit permission/ownership checks for their objects and regression tests against the assembled application. A route-only test with an injected administrator cannot establish authorization correctness.
- Preserve fresh session IDs after authentication, atomic session updates, logout revocation, database-backed authorization versions and MFA-version checks. Never turn a pending MFA challenge into an ordinary session or trust client-provided roles or user IDs.
- Treat SSO, local/LDAP MFA and trusted-proxy authentication as distinct authorities. Preserve issuer/audience/nonce/algorithm checks, browser-bound single-use state, PKCE, verified-email linking and factor-management reauthentication.
- Unsafe cookie-authenticated requests require CSRF and Origin checks. WebSockets need their own Origin, authentication, connection-limit and periodic-revalidation checks; HTTP middleware does not protect WebSockets automatically.
- Always validate HTTPS certificate chains and hostnames. Do not add `verify=False`, `CERT_NONE`, disabled hostname checks or a fallback after TLS verification fails. Custom trust stores must retain both validations.
- Use `ingest/http.py` for built-in HTTP collection and `integrations/transport.py` for custom integrations. Preserve DNS/address validation, IP pinning, original-host TLS verification, response limits, redirect restrictions and credential-origin boundaries. Review any additional network path explicitly.
- Never reuse credentials across connections. Store secrets through the encrypted credential layer or approved deployment secret references; exclude them from logs, exceptions, revision histories, artifacts and UI responses.
- Use bound SQL parameters and ORM expressions. Dynamic identifiers/orderings require fixed allowlists. Never evaluate configuration or evidence as Python, JavaScript, shell commands or templates.
- Route administrator-supplied regular expressions through `security/regex.py`, including file-managed configuration. Preserve its runtime timeout, input cap and bounded cache. Edit-time checks alone do not bound execution time.
- Bound uploads, parsing, decompression, pagination and remote response sizes. Apply deadlines to expensive operations. Close network clients and streams on both success and failure; avoid database transactions spanning idle WebSockets or unrelated network waits.
- Treat evidence and stored text as untrusted. Use `textContent` or `esc()` for text, the shared sanitizer for rich HTML, scheme validation for links, and `navigation-security.js` for post-authentication destinations. HTML escaping alone does not make a URL safe.
- Keep scripts in external files and event handlers in JavaScript listeners. Preserve the supplied CSP; do not restore inline scripts, `eval`, `new Function`, or unsafe script policy exceptions to fix a UI regression.
- Serve uploaded active content as attachments. Keep local storage paths within their configured root and preserve download filename/header sanitization. Native preview processing needs resource limits and maintained dependencies.
- Fail closed on authentication or authorization uncertainty. Keep errors useful without disclosing credentials, private keys or upstream response bodies.

## Validation

- Use `.github/workflows/tests.yml` and `tests.sh` as the authoritative test workflow. Run against a disposable PostgreSQL database and Valkey; the suite changes database contents. Never point tests at an operational installation.
- For focused local work, run the affected backend tests and `bash tests-ui.sh`. SQLite runs skip PostgreSQL-specific behavior and cannot establish migration, locking or concurrency correctness.
- For security changes, add a reproducible negative test demonstrating the attack prerequisite and rejection. Test both permitted behavior and the boundary being protected, using synthetic credentials and local fixtures.
- Run the assembled-application contracts in `tests/test_security_design_contracts.py` after route, middleware, session or policy changes. Preserve the local TLS hostname verification regression.
- Test UI rendering and navigation with hostile strings as well as ordinary values. For CSP or vendor changes, verify actual vendor behavior and report any missing browser coverage.
- Update `pyproject.toml` and `poetry.lock` together for Python dependencies. Keep unrelated locked versions unchanged. Preserve pinned third-party Actions and design-system revisions.
- Report what passed, what failed and what could not run. Do not equate a green SQLite or mocked UI suite with full PostgreSQL CI, production-browser validation or a penetration test.

## Security re-review whenever completing a round of work

Whenever completing a round of work, re-review the SECURITY.md and consider whether the changes
you have implemented weaken in any way the security of KEEN. Examples of security issues include
but are not limited to:

- account takeover (session fixation/replay/confusion/race condition)
- privilege escalation or auth bypass
- SQL injection or other injection attacks
- XSS
- SSRF
- CSRF
- trivial DoS or reDoS
- open redirects
- MFA bypass when enabled
- memory leaks
- unbounded requests (size/lack of timeout etc)
- middleware being overlooked or bypassable due to insufficient route restriction/accidental hierarchy 'inclusion'
- lack of validation of HTTPS certs
- undesired following of redirects
- insufficient validation of OIDC parameters as mandated per the OIDC spec
- one-time link/token replay attacks
- lack of credential isolation between common source ingester types where separate inputs are used (e.g when caching them)
- insufficient expiry of stale records
- sensitive data leaking into logs or output, including excessive exception traceback data
- inappropriate trusting of client-side request data
- insufficient redaction of sensitive values where redaction features are expected in KEEN
- other cryptographic failures
- data integrity risks and/or other race conditions

Be as comprehensive in any security review of your completed work as you possibly can, whilst also being
realistic about viability of attack vectors.

## Documentation and delivery

- README describes current product capabilities and setup. SECURITY.md describes the current security model, realistic risks and reporting policy; never use it as a list of fixed vulnerabilities.
- Operator and user guidance belongs in Help. Avoid deployment-specific demo assumptions in general guidance.
- Write in present tense, without release-update narratives or developer handover instructions in user-facing documentation. Do not add cumulative Markdown change reports to the repository.
- When delivering an incremental patch, state its base, verify application against that base, and include new files. Use a text patch compatible with `patch -p1`; handle binary changes separately.
- Avoid contrastive-negation language.
- Write in Australian or British English.
