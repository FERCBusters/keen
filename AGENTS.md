# Working on Keen

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
- Update `pyproject.toml` and `poetry.lock` together for Python dependencies. Keep unrelated locked versions unchanged. Preserve pinned third-party Actions, image digests and design-system revisions. Pin a new third-party image with `python3 tools/docker_image_pins.py update`.
- Validate the exact final working tree after formatter or lint fixes. Include a fresh-process application import and the lifespan tests; earlier results from a different tree do not validate the delivered code. Imported fixtures and public re-exports may be used indirectly: mark them explicitly and preserve their contracts.
- Report what passed, what failed and what could not run. Do not equate a green SQLite or mocked UI suite with full PostgreSQL CI, production-browser validation or a penetration test.

## Security re-review whenever completing a round of work

Whenever completing a round of work, work through the required security review checklist in SECURITY.md, record evidence and untested limitations, and re-review the SECURITY.md and consider whether the changes you have implemented weaken in any way the security of Keen. Examples of security issues include but are not limited to:

- account takeover (session fixation/replay/confusion/race condition/concurrent revocation)
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
- insufficient validation of OIDC parameters (e.g issuer, claims, state/nonce and others) as mandated per the OIDC spec
- one-time link/token replay attacks
- lack of credential isolation between common source ingester types where separate inputs are used (e.g when caching them)
- insufficient expiry of stale records
- sensitive data leaking into logs or output, including excessive exception traceback data
- inappropriate trusting of client-side request data
- insufficient redaction of sensitive values where redaction features are expected in Keen
- other cryptographic failures
- data integrity risks and/or other race conditions
- lack of appropriate rate-limiting

Exercise confirmed issues with reproducible negative tests. Recheck the fixes themselves, explain remaining limitations, and distinguish exploitable findings from defense-in-depth improvements. SECURITY.md describes current behavior and realistic risks; keep findings and fix history out of it.

Be as comprehensive in any security review of your completed work as you possibly can, whilst also being realistic about viability of attack vectors.

## Documentation and delivery

- README describes current product capabilities and setup. SECURITY.md describes the current security model, realistic risks and reporting policy; never use it as a list of fixed vulnerabilities.
- Operator and user guidance belongs in Help. Avoid deployment-specific demo assumptions in general guidance.
- Write in present tense, without release-update narratives or developer handover instructions in user-facing documentation. Do not add cumulative Markdown change reports to the repository.
- When delivering an incremental patch, state its base, verify application against that base, and include new files. Use a text patch compatible with `patch -p1`; handle binary changes separately.
- Avoid contrastive-negation language.
- Write in Australian or British English.

## Backend architecture

- The request path is middleware → HTTP adapter → domain operation → persistence or integration. Middleware handles cross-cutting request policy; it is not a separate controller layer. Preserve the central middleware in `main.py` and the exact public-route policy.
- `app/api/routes/` owns HTTP adapters: route declarations, permission dependencies, request parsing, status codes, cookies, headers, redirects, streaming and WebSockets. Use `app.api.routing.Router` so expected domain failures have a consistent HTTP response. Protocol-specific route classes must extend `DomainRoute`. Shared HTTP dependencies belong in `app/api/dependencies/`.
- `app/services/` owns use cases: domain validation, object-level access checks, queries, mutations, audit records and transaction boundaries. Services must not import `app.api`, FastAPI or Starlette, or accept HTTP request/response objects. Pass validated values, the database session and authenticated actor explicitly. `ChangeContext` carries trusted request metadata when needed; adapters supply only the metadata needed, using `app.api.context.change_context` when actor or client metadata is required. Never populate the actor, roles or trusted client address from request payloads.
- Domain errors use `services.errors.DomainError` with a safe public message and optional response headers. Unexpected exceptions remain failures. Avoid catching broad exceptions merely to turn a failed write into success.
- Keep a cohesive feature in one service module. Use a feature package, such as `services/isms/` or `services/audits/`, when operations have distinct responsibilities. Shared schemas belong in `app/schemas.py`; feature-specific schemas may live with their domain. Avoid importing private helpers from HTTP adapters. Extract shared helpers to their actual owner.
- `app/security/` owns authentication, cryptography and reusable security policies; HTTP-aware authentication helpers are called at the adapter boundary. `app/db/` owns models and session setup, `app/storage/` owns artifact storage, and `app/ingest/` and `app/integrations/` own collection and upstream transport. Services may use SQLAlchemy directly; add another abstraction only when it serves a concrete responsibility.
- The domain operation owns its transaction. Shared mutation helpers should not commit independently. Preserve intentional multi-stage security transactions, row-lock ordering and revalidation. Adapters may release a preliminary authentication transaction before reading a bounded body; the operation must reauthenticate under the required locks before writing.
- Preserve synchronous/asynchronous execution, query counts, session lifetimes, pagination and lazy streaming when extracting code. A service call is an ordinary in-process call. Do not add threads, network hops or new sessions to implement a layer boundary. Use the narrow `Upload` adapter for lazy attachment reads and retain existing size limits.
- Test domain behaviour and assembled HTTP/security behaviour. Update mocks to patch the owning module. `test_service_boundaries.py` enforces inward dependencies and keeps ORM queries and mutations out of HTTP adapters; do not add compatibility re-exports to satisfy old test imports.

## Frontend source and domain boundaries

- Keep first-party JavaScript and CSS formatted with `bash format-ui.sh --write`;
  verify with `bash format-ui.sh --check`. Install its pinned tools using
  `npm ci --prefix services/ui/tests`. Minification belongs in generated UI build
  output, never committed source. Do not reformat vendored code.
- Evidence export code belongs in `pages/evidence-export/`; give page-specific
  preferences and state to its factory explicitly. Preserve masking, cleanup,
  filename handling and export cancellation when changing it.
- Keep Forgejo and Gitea collectors separate so their upstream APIs can evolve
  independently. Shared stateless utilities remain appropriate.
