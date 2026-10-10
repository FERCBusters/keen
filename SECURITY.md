# Security policy and threat model

## Reporting a vulnerability

Please report suspected vulnerabilities through this project's GitHub repository. Prefer **Security → Advisories → Report a vulnerability** for a private report. If private reporting is unavailable, open an issue asking for a private reporting channel and keep exploit details, credentials and sensitive data out of the public issue.

Every report must include a reproducible proof of concept demonstrating the issue. Include:

- The KEEN version or commit, deployment configuration relevant to the issue, and affected component.
- The attacker's required access, permissions and other prerequisites.
- Minimal reproduction steps or a script, expected and actual results, and evidence of the security impact.
- Synthetic test data and redacted logs or screenshots where useful.

Test on an installation you own or have explicit permission to assess. Use the smallest demonstration that establishes the issue, preserve other people's data, and avoid disruptive testing against public or hosted instances.

Our goal is to provide a preliminary response within **72 hours**. This is an acknowledgement and initial assessment target; investigation and remediation time depends on the report. KEEN offers **no monetary bounties**. Authors of validated reports will be credited, using their preferred name or handle, unless they request anonymity. We coordinate disclosure with reporters after assessing impact and remediation.

## What KEEN protects

KEEN stores operational evidence, artifacts, assurance mappings, audit discussions, risk and ISMS records, user accounts, integration credentials and agent identities. These records can contain sensitive organisational or personal information. Security objectives include controlling who can read or change them, keeping collection credentials within their intended connection, preserving evidence identity, and maintaining service availability.

A KEEN installation is an organisational trust boundary. Users inside it receive access through roles, groups and permissions. Source connections distinguish systems and credentials within that installation. They do not create independent tenants or separate encryption domains. Hosting mutually distrustful organisations requires isolated installations, databases, storage, broker namespaces and secrets.

## Actors and realistic risks

The threat model includes an unauthenticated Internet client, a malicious website visited by a signed-in user, a user acting beyond their assigned permissions, a compromised enrolled agent or webhook credential, and hostile content returned by a configured evidence source. A valid source credential establishes who submitted evidence; reviewers still assess the truth and relevance of its contents.

Application administrators are trusted to configure sources, permissions, mapping rules and evidence retention. Deployment operators control the executable code, database, broker, storage and encryption keys. Compromise of these privileges can compromise the installation. An administrator's intended ability to delete data or grant permissions is part of that role; escaping the role's intended authority remains a security issue.

Upstream identity providers and authorised integration servers are trusted for their specific functions. A compromised provider can assert identities; a compromised collection server can return fabricated data and observe credentials issued for that server. Network controls and narrowly scoped upstream credentials reduce the resulting exposure.

## Authentication, sessions and account linking

Browser sessions use random, server-generated identifiers stored in Valkey. The browser receives an HttpOnly session cookie. Successful authentication issues a fresh identifier, and logout deletes the current server-side session. Permission snapshots are checked against a database-backed authorization version, and inactive accounts are rejected. Session expiry is rolling; operators configure its duration.

Local passwords are stored as salted PBKDF2-HMAC-SHA256 hashes. Password login attempts are rate limited by client address and username. Authentication rate limits fail closed when their backing service is unavailable. Local password and MFA changes invalidate local/LDAP sessions through the account's security version.

Local and LDAP login support TOTP, WebAuthn security keys/passkeys and recovery codes. A pending MFA ceremony has its own short-lived credential and grants access only to that ceremony. Account security changes require reauthentication; an existing factor must be verified before managing enabled MFA. TOTP secrets are encrypted, recovery codes are hashed and single-use, and WebAuthn checks the expected origin, relying-party ID and challenge. KEEN uses WebAuthn as a second factor alongside a password; hardware authenticators may rely on user presence without requiring a PIN or biometric verification.

KEEN's local MFA policy applies to local and LDAP authentication. Configure equivalent MFA at the identity provider when using SSO, or at the authenticating proxy when using trusted remote-user authentication. Those mechanisms constitute separate authentication authorities.

OIDC validates signatures using the configured asymmetric algorithm policy and checks identity-token claims, issuer, audience and nonce. Its configured client ID is the only trusted audience; tokens naming additional audiences are rejected. Identity-provider HTTP responses are limited to 1 MiB and a 20-second request budget, with redirects and compression disabled. Login state is expiring, single-use and bound to the initiating browser; exchanges use PKCE. GitHub sign-in uses its configured OAuth endpoints. Automatic linking to an existing account requires both KEEN mailbox verification and provider verification of the email address. Users manage linking addresses through a recently authenticated session and a mailbox proof.

Trusted remote-user authentication is an explicit deployment option. The authenticating proxy must remove client-supplied identity headers, inject its own verified identity, and be the sole route to the API. Native OIDC takes precedence over remote-user headers.

## Authorization and browser boundaries

The application applies authentication centrally. Public HTTP exceptions are enumerated by method and complete route pattern. Agent and webhook endpoints validate their own credentials; MFA endpoints validate their pending ceremony. Protected writes have a default administrator gate with explicit permission-controlled or self-service exceptions. Routes also enforce the permissions and object ownership appropriate to their data. UI visibility is a convenience; authorization is enforced by the backend.

Cookie-authenticated writes require a matching CSRF cookie/header token and reject an unexpected browser Origin. MFA ceremonies require the configured Origin. Notification WebSockets require an allowed Origin and an authenticated identity; connections are limited and authorization is rechecked at intervals of up to 30 seconds. Notification connections release database sessions between checks. A change or revocation can take up to that interval to close an existing notification connection.

Plain text is escaped before HTML insertion. Rich text is reduced to a formatting allowlist on the server and sanitized when rendered. Links and post-login destinations are validated separately from HTML escaping. The supplied nginx policy restricts scripts to the installation's own origin and blocks inline scripts, string evaluation and embedded objects. Inline styles remain allowed for the UI. Uploaded artifacts are served as downloads; PDF previews are rendered in a resource-limited child process.

Application responses use a no-store cache policy. Production cookies must be Secure and scoped to the installation's host. Keep unrelated or untrusted applications off the same origin, and avoid sharing session cookies across sibling domains.

## Outbound connections and secrets

HTTPS connections validate the certificate chain and server hostname. KEEN has no supported option to disable HTTPS certificate verification. Install a suitable trusted CA for private certificates. TLS failure must be resolved through the trust configuration or upstream certificate.

Built-in HTTP collectors validate destination addresses and pin connections to validated addresses while preserving TLS hostname verification. Redirects and ambient proxies are disabled in that transport. Loopback, link-local and other prohibited address classes are rejected, including IPv4-mapped equivalents. `KEEN_INGESTION_ALLOWED_CIDRS` restricts permitted private destinations; an empty setting permits private networks apart from prohibited addresses. Upstream credentials and cached authentication state belong to the selected connection.

Custom API collectors additionally require an operator-managed host allowlist and explicit private-network CIDRs. Their definitions are declarative; they do not execute supplied Python, shell commands or templates. AWS and Google collectors use their respective SDK transports. Operator-configured SSO, storage and outbound notification endpoints have separate transport paths and trust requirements. Apply network egress policy to constrain the destinations available to the whole deployment.

Credentials saved for integrations are encrypted with `KEEN_INTEGRATION_SECRET_KEY`. TOTP uses its separate MFA encryption key. These keys live outside the database and must be protected and backed up. Encryption protects a database copy without its keys; a running application with the keys can decrypt the credentials it needs. The shared ingestion path redacts recognised secret keys in structured payloads and text artifacts, and applies secret redaction to event summary fields. URL userinfo and recognised secret query/fragment parameters are removed from displayed URLs. Collector diagnostics use bounded, generic messages; SQLAlchemy error output omits bound parameter values. Redaction is best effort: arbitrary prose, unknown key names, secrets embedded in URL paths and binary artifacts may retain sensitive information. Configure additional sensitive keys where needed and avoid placing secrets in event content, labels, URLs or definition fields that appear in evidence and exports.

Agent enrollment profiles use high-entropy shared enrollment keys. KEEN retains credential hashes and issues an individual expiring agent token after enrollment. Profiles support expiry, enrollment limits and network restrictions. Profile revocation also revokes its associated agent credentials. Individual credentials support persistent hosts. Possession of a profile's enrollment key permits enrollment within that profile's restrictions; distribute it through a protected secret channel. A compromised agent credential permits actions scoped to that agent, including evidence submission, until expiry or revocation.

## Availability and evidence storage

Request bodies are bounded before parsing, including chunked bodies. HTTP collection responses have size limits and network timeouts. Rule matching uses bounded inputs, a per-match execution timeout and a bounded compiled-pattern cache. Notification connections and sends are bounded. These controls address individual-request resource abuse; deployment-level connection limits, quotas, worker capacity, monitoring and network rate limiting are needed for sustained or distributed traffic.

Expensive reports, broad collection jobs and large authorised datasets consume resources. PDF resource limits reduce parser exposure; maintain the native parser and isolate the application processes. KEEN does not claim complete protection against denial of service, dependency defects or every memory/resource leak.

Artifacts have recorded SHA-256 checksums and backend-specific storage controls. Checksums support integrity verification. Immutability and retention guarantees depend on storage permissions, versioning and retention configuration. Someone controlling both records and storage can alter the evidence and its recorded checksum. Evidence review should account for source trust and provenance.

## Deployment responsibilities

Use HTTPS for browser, agent and credential-bearing traffic; configure `KEEN_PUBLIC_BASE_URL`, Secure cookies and trusted proxies correctly. The provided Compose port bindings are local to the host. Keep PostgreSQL, Valkey, worker control interfaces and artifact storage inaccessible to untrusted clients. Use separate strong secrets, restrict upstream account permissions, preserve encryption keys securely, and test backup restoration.

Keep KEEN, locked dependencies, container images, the TLS proxy and native tools maintained. Disable unused source types and authentication methods, restrict private-network egress, and monitor authentication failures, ingestion errors, disk use and resource pressure. Validate configuration changes with the documented test workflow and operational checks appropriate to the installation.
