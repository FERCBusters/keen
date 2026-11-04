# Security policy and threat model

## Reporting a vulnerability

Please report suspected vulnerabilities through this project's GitHub repository. Prefer **Security → Advisories → Report a vulnerability** for a private report. If private reporting is unavailable, open an issue asking for a private reporting channel and keep exploit details, credentials and sensitive data out of the public issue.

Every report must include a reproducible proof of concept demonstrating the issue. Include:

- The Keen version or commit, deployment configuration relevant to the issue, and affected component.
- The attacker's required access, permissions and other prerequisites.
- Minimal reproduction steps or a script, expected and actual results, and evidence of the security impact.
- Synthetic test data and redacted logs or screenshots where useful.

Test on an installation you own or have explicit permission to assess. Use the smallest demonstration that establishes the issue, preserve other people's data, and avoid disruptive testing against public or hosted instances.

Our goal is to provide a preliminary response within **72 hours**. This is an acknowledgement and initial assessment target; investigation and remediation time depends on the report. Keen offers **no monetary bounties**. Authors of validated reports will be credited, using their preferred name or handle, unless they request anonymity. We coordinate disclosure with reporters after assessing impact and remediation.

## Scope and trust boundaries

### What Keen protects

Keen stores operational evidence, artifacts, assurance mappings, audit discussions, risk and ISMS records, user accounts, integration credentials and agent identities. These records can contain sensitive organisational or personal information. Security objectives include controlling who can read or change them, keeping collection credentials within their intended connection, preserving evidence identity, and maintaining service availability.

A Keen installation is an organisational trust boundary. Users inside it receive access through roles, groups and permissions. Source connections distinguish systems and credentials within that installation. They do not create independent tenants or separate encryption domains. Hosting mutually distrustful organisations requires isolated installations, databases, storage, broker namespaces and secrets.

### Actors

The threat model includes an unauthenticated Internet client, a malicious website visited by a signed-in user, a user acting beyond their assigned permissions, a compromised enrolled agent or webhook credential, and hostile content returned by a configured evidence source. A valid source credential establishes who submitted evidence; reviewers still assess the truth and relevance of its contents.

Application administrators are trusted to configure sources, permissions, mapping rules and evidence retention. Deployment operators control the executable code, database, broker, storage and encryption keys. Compromise of these privileges can compromise the installation. An administrator's intended ability to delete data or grant permissions is part of that role; escaping the role's intended authority remains a security issue.

Upstream identity providers and authorised integration servers are trusted for their specific functions. A compromised provider can assert identities; a compromised collection server can return fabricated data and observe credentials issued for that server. Network controls and narrowly scoped upstream credentials reduce the resulting exposure.

### Hostile evidence sources

A configured evidence source can turn hostile: a compromised upstream, a third-party feed, or anyone who can write into a log Keen collects. Keen contains what such a source can do to its own collection: its requests, parsing and runs end within time and size limits, one record or rule cannot stop a run, and other sources, the API and the workers carry on.

Within those limits a hostile source can still send false, misleading or incomplete evidence, fail on every run, or use its whole budget each time. Reviewers judge the truth and provenance of what it sent, a failing source reports a bounded, generic error in its run results, and administrators pause or remove a source that misbehaves.

### Post-quantum cryptography

A future cryptographically relevant quantum computer matters chiefly for data recorded now and attacked later. Keen Agent traffic is protected against that "harvest now, decrypt later" risk: each request's key exchange is hybrid, ML-KEM-768 (FIPS 203) with X25519, so it stays confidential unless both are broken, and request bodies and responses use AES-256-GCM. Authentication is classical: Keen's identity key, agent keys and the request, batch and evidence signatures are Ed25519, which such a computer could forge. A forgery only helps an attacker who is active at the time, and recorded traffic cannot be impersonated retrospectively; stored evidence also rests on Keen's own receipt records, so passing off forged evidence as historical still needs write access to Keen.

Other Keen traffic (browsers, integrations, webhooks and bearer-token OTLP collectors) relies on TLS alone and is post-quantum only where both ends negotiate a hybrid key exchange, as Go 1.24 and later clients (including Keen Agent) and servers built on TLS libraries such as OpenSSL 3.5 and later do by default. Secrets stored with `KEEN_INTEGRATION_SECRET_KEY` and `KEEN_MFA_ENCRYPTION_KEY` use symmetric cryptography, which quantum attacks weaken but do not break.

## Authentication

### Sessions

Browser sessions use random, server-generated identifiers stored in Valkey. The browser receives an HttpOnly session cookie. Where cookies are Secure and no cookie domain is set, the session cookie and the pending second-factor cookie take the `__Host-` prefix, which a browser accepts only from the installation's own host over HTTPS, so a site on a sibling subdomain can neither set nor shadow them.

Successful authentication issues a fresh identifier, and CSRF-protected POST logout deletes the current server-side session. When the session store cannot confirm the deletion, sign-out returns a retryable error and leaves the browser's cookies in place for the retry. GET logout links open the account page without changing authentication state.

Permission snapshots are checked against a database-backed authorization version, and inactive accounts are rejected. Group membership and group permission changes are serialised, so each invalidates the snapshots of the members it affects.

Deactivating an account advances its database-backed session version, invalidating previously issued sessions across authentication methods even after reactivation. SSO session issuance reads the account's active state and session version together under the account's row lock, so a deactivation during SSO sign-in issues no session; local and LDAP sessions carry the session version read when the password was checked, so one issued during a deactivation is refused when it is first used. In-flight requests can finish; subsequent authentication checks reject the old sessions.

Session expiry is rolling, and every session also ends a fixed time after sign-in, 30 days by default, however recently it was used and whatever its authentication method; operators configure both durations.

### Passwords and sign-in limits

Local passwords are stored as salted PBKDF2-HMAC-SHA256 hashes. Password login attempts are rate limited by client address and by username; a blank username is refused before any work. Usernames are compared for this limit after compatibility normalisation, case folding, removal of invisible characters and collapsing of spaces, and each password tried against an LDAP directory entry also counts against that entry, so spellings the directory treats as one name share one allowance; a spent entry allowance is answered as a wrong password.

Every per-address limit, for sign-in, password changes, second-factor codes, SSO starts, webhooks, agent credential failures, agent enrolment and the request audit trail, counts an IPv6 /64 network as one address.

Each sign-in attempt within the rate limits is audited with its method and outcome, including a right password whose second factor cannot start; a refused attempt names the account only when its username matches one. Current-password confirmation for password changes is limited per address and per account. Authentication rate limits fail closed when their backing service is unavailable. Local password and MFA changes invalidate local/LDAP sessions through the account's security version.

### Second factors

Local and LDAP login support TOTP, WebAuthn security keys/passkeys and recovery codes. A pending MFA ceremony has its own short-lived credential and grants access only to that ceremony. Wrong TOTP and recovery codes are counted for the account across sign-ins: the fifth within an hour ends the ceremony, the account then accepts no code for an hour, and its owner is emailed; the owner is also emailed whenever a recovery code is used. A failed security-key assertion consumes its own challenge and does not count towards that limit, and a security key still completes sign-in while codes are refused.

Account security changes require reauthentication; an existing factor must be verified before managing enabled MFA. TOTP secrets are encrypted, recovery codes are hashed and single-use, and WebAuthn checks the expected origin, relying-party ID and challenge. Keen uses WebAuthn as a second factor alongside a password; hardware authenticators may rely on user presence without requiring a PIN or biometric verification.

Keen's local MFA policy applies to local and LDAP authentication. Configure equivalent MFA at the identity provider when using SSO, or at the authenticating proxy when using trusted remote-user authentication. Those mechanisms constitute separate authentication authorities.

### Single sign-on and account linking

OIDC validates signatures using the configured asymmetric algorithm policy and checks identity-token claims, issuer, audience and nonce. Its configured client ID is the only trusted audience; tokens naming additional audiences are rejected. Identity-provider HTTP responses are limited to 1 MiB and a 20-second request budget, with redirects and compression disabled. Login state is expiring, single-use and bound to the initiating browser; exchanges use PKCE.

Each provider's redirect URI is configured explicitly and must be on `KEEN_PUBLIC_BASE_URL`; Keen never derives a callback address from the request. GitHub sign-in uses its configured OAuth endpoints.

Automatic linking to an existing account requires both Keen mailbox verification and provider verification of the email address. A provider's allowed email domains apply to every sign-in, including identities linked earlier: the provider's current verified email must be in one of them. Users manage linking addresses through a recently authenticated session and a mailbox proof. A request to verify an address gets the same answer whether or not another account uses it, and only a free address is sent a link, through the security-email outbox.

### Trusted remote-user authentication

Trusted remote-user authentication is an explicit deployment option. The authenticating proxy must remove client-supplied identity headers, inject its own verified identity, and be the sole route to the API. Keen accepts a single identity header only from a direct peer in `KEEN_SECURITY_TRUSTED_PROXY_CIDRS`; HTTP and WebSocket authentication use the same peer check. Where the supplied nginx is that peer, trusting it for client addresses also trusts the identity headers it forwards: nginx blanks the default `REMOTE_USER` header, and an installation that names another header (`KEEN_REMOTE_USER_HEADER_NAME`) relies on its authenticating proxy removing client-supplied copies of that header on every route, since nginx passes the header on. The Compose default also trusts the network gateway, through which every process on the Docker host connects, so an installation using remote-user authentication sets `KEEN_SECURITY_TRUSTED_PROXY_CIDRS` to the keen-ui address alone.

The supplied server disables Uvicorn proxy-header rewriting so this remains the actual socket peer; custom ASGI launches must also disable that rewriting. Native OIDC takes precedence over remote-user headers.

### Client addresses

Client addresses used in audit records, sign-in rate limits and agent enrolment network restrictions follow `X-Forwarded-For`, every field line in order, back from the socket peer only while each hop is in `KEEN_SECURITY_TRUSTED_PROXY_CIDRS`; entries beyond the first untrusted or unparseable hop cannot change the recorded address. The supplied Compose file pins its network and, while the setting is unset or empty there, trusts the keen-ui container and the network gateway, where connections to the host's published port arrive; anything running on the Docker host can therefore supply forwarded addresses. Deployments without Compose set it to their own proxies. While the API's own setting is empty, every client behind a proxy shares the proxy's address for these purposes and for per-address rate limits, so one client's failed sign-ins can block everyone's; the API logs a warning, once per process, when a peer on a private address sends `X-Forwarded-For`.

## Authorisation

### Enforcement

The application applies authentication centrally. Public HTTP exceptions are enumerated by method and complete route pattern. The API documentation and OpenAPI schema require a signed-in session. Agent and webhook endpoints validate their own credentials; MFA endpoints validate their pending ceremony.

Protected writes have a default administrator gate with explicit permission-controlled or self-service exceptions. The audit trail (request records and the entity changelog, which spans every register's change history) is for administrators only, as is the rest of the administration namespace; no permission grants it. Routes also enforce the permissions and object ownership appropriate to their data. UI visibility is a convenience; authorization is enforced by the backend.

### Audits and People records

Audit managers create and edit audits and scheduled templates they created. The separate `audits.admin` permission permits editing other users' audits and deleting audits or scheduled templates. Whoever samples evidence into an audit is an auditor, so `audits.manage` and `audits.admin` each include `events.read` and `isms.read`, resolved with the account's other permissions and its permission snapshot; sampling any other record, such as a risk, PESTLE(E) item, interested party or story, still requires read access to it.

Scoping ISMS documents into an audit or scheduled template requires ISMS read access, checked before any document is looked up, and scope coverage lists ISMS documents and their counts only for ISMS readers. An audit's readers see its samples, including a sampled event's summary fields, each sample's title and supported controls, and the controls of the events a sampled story holds when it is read, and its scoped documents and SWOT items, without read access to those records.

An account named as an audit's attendee, directly or through its linked People record, reads that audit and finds it among its audits. A People record attends audits and sits quizzes through whichever account is linked to it, so the audit's attendee list and person pickers, and a quiz's audience picker, show that account to the people adding the person. Whoever moves a People record's account moves what the record carries, so changing the account linked to someone named as an audit attendee, or deleting that person, requires `audits.admin`, as the same change for someone who sits quizzes requires `quiz.manage`; every access rule that reads a People record's account is listed with the permission that guards moving it.

### Evidence, controls and aggregates

Event records and the aggregates derived from them (statistics, volume charts and source graphs) require `events.read`; control lists omit evidence counts for other accounts, and effectiveness-measure forms list the evidence sources that events name only to `events.read` holders, showing other ISMS readers the sources measures already name.

A control's requirement standing counts its linked policies and its measures' latest results only for accounts with ISMS read access, and evidence only for those with `events.read`. Only administrators choose a control's or clause's owners, from active accounts, and each change enters the control's or clause's changelog; owners' usernames are shown to everyone who can open the control or clause, and an account's My controls lists only what it owns itself.

### Diary link entries

Saving an ISMS record, a risk or an interested party with framework links records an automatic diary entry containing the record type, its linked control and clause references and a record-UUID link, within the authority of the already authorised save. A Risk Ledger import records the same entry for each risk it links to a control, as a Keen record outside the importing connection's scope, so the entry carries no connection identity. Entries about risks are visible to users who can read or manage risks, entries about interested parties to users who can read or manage interested parties or risks, and entries about other ISMS records to users with ISMS read access; administrators keep diary editing rights, and the entries never enter evidence retention purges.

Statistics (event totals, controls with evidence, and per-control evidence counts and latest-evidence dates) can count entries a reader cannot open, so any `events.read` user can infer that a record was linked to a control, and roughly when. Event and evidence lists, exports, facets, source counters, graphs and volume charts include only entries the reader can open, and the same rules govern each entry's artifacts and questions. Event lists and facets, source lists, graphs and a control's evidence summary are cached per account for the aggregate cache lifetime, 60 seconds by default, so a permission change reaches them within that time.

### Mapping rules

Mapping-rule insights are for administrators only; they count rule matches and source events without event content, are cached for the same lifetime for each saved version of the rules, and on PostgreSQL each of their queries stops after 15 seconds.

Suggested mapping rules are for administrators only. A background job reads at most the latest 1,000 events of each source from the last 30 days, never diary entries; one run proceeds at a time, and on PostgreSQL each of its queries stops after 30 seconds. A suggestion stores the source and activity names, the proposed rule, its candidate controls with the catalogue words and concepts that matched, and the identifiers of up to five sample events. It stores no event summaries or payloads, which are read when an administrator lists suggestions, so retention removes them with their events. Nothing is mapped until an administrator creates the rule, which is validated and saved like any other rule change.

An upgrade adds Keen's built-in mapping rules for its own cloud, workstation and server evidence once, beside the installation's rules and as a recorded revision of them with no account named; administrators edit, disable or delete them like any other rule, and they map only evidence collected afterwards unless an administrator applies them to earlier evidence.

### Risks and organisation roles

Membership of an organisation role that owns risks grants read access to those risks, so changing that role's members or deleting it requires `risk.manage`. Choosing a role as a risk's owner, or as a quiz's audience, gives the role's current members that access, whoever set its membership; review a role's members when choosing it.

### Stories

Stories are containers for events, risks, ISMS records and notes: `stories.read` views them and comments, `stories.manage` creates stories and edits or deletes its holder's own, and `stories.admin` edits or deletes any story. Adding an item requires read access to the referenced record, and each item is shown only to readers who can read that record themselves; other items appear as placeholders without the record's identifier, label or note, so a story's title, summary, notes and shape are visible to every story reader while its restricted content is not.

### Quizzes

Quizzes keep each person's answers with the people who run them. `quiz.manage` writes, schedules and grades quizzes, assigns people, accounts and organisation roles, and sees each person's answers and scores; account provisioning creates a uniquely linked People record, and assigning an older account without one creates it in the same transaction. `quiz.read` sees quizzes, their questions without answer keys or explanations and with options in alphabetical order, the audience size, and results only for rounds that have closed with every response graded and at least three completed, without names, answers or the options people chose.

Readers who know who sat a small round can still narrow down its members' scores from its average, and a grade changed after a round closes shows as a change in its recorded average. A recorded round stays closed, so no one can compare its average before and after a latecomer.

A person sits only rounds they take part in, as the People record linked to their account, and answers go in only from the account that started the attempt. The people in an organisation role a quiz is assigned to sit its rounds, so changing that role's members, moving a person into or out of it, changing the account of someone in it and deleting it need `quiz.manage`, as does moving the account of a People record that is assigned a quiz or has quiz rounds. An attempt in progress carries no answer key.

A person sees their own score only when the quiz shows scores, and reviews their answers beside the right ones only as the quiz allows, while they have no other round of the quiz left to sit and until a later round opens; once a review closes, the attempt returns no questions or answers. Written answers are graded without names, and a quiz manager cannot grade their own answers. Saving a page or handing in share-locks the round while closing a round row-locks it, so a round's recorded average covers exactly the attempts handed in before it closed; that average reaches the quiz's effectiveness measure, and through it the controls the quiz tests, only once the round closes under the conditions above.

### Notifications, mail and meetings

Quiz invitations and reminders, and reminders to audit attendees, go only to people whose account is active, carry a title, dates and a link, and go to each recipient at most once: each is claimed before sending so overlapping workers never send twice, and none holds a database transaction open while the mail server answers. Requests queue quiz invitations for the worker. Email subjects keep to one line. Every stored email address, whether typed into an account, a People record or an audit attendee or read from an identity provider or directory, is exactly one mailbox, never a list, display name or comment, and each message names its one recipient as the SMTP envelope recipient.

Each account has an inbox of notifications: an audit it attends has started, it was asked to sit a quiz round, minutes were added for a meeting it attended, or the latest result of an effectiveness measure it owns fell outside the threshold. Measure notifications are shown only to accounts with ISMS read access. A notification goes to the attendee's or participant's own account, or the account linked to their People record, never to the account that made the change, and once per record. It stores only the kind, the record's identifier and who acted; titles and links are read when the inbox is shown, and a notification is shown only while its feature is on and the account can open the record.

Each account chooses whether notifications appear in Keen, arrive by email, or both. An email is queued only when mail is configured, the account has an address, and it can open the record when the notification is made; it carries the record's title, its date, who acted and a link, in the same background outbox as account security emails, which keeps no message text once a message is sent and deletes sent and failed messages after 30 days. With email only, the notification is kept as read. An account can read and mark only its own notifications. Read notifications are deleted after 90 days and unread ones after a year.

An account recorded on an ISMS meeting, as an attendee or with apologies, directly or through its linked People record, can read that meeting without `isms.read`, as listed under its meetings; linked ISMS documents then appear by title only. Without `isms.read`, a request for any other meeting, or for one that does not exist, is refused alike, and the meeting's change history and the meetings list stay with ISMS readers.

### Workstation and cloud posture

Workstation agent assignments are administrator-managed. An agent key supplies evidence only for its authenticated identity; payload fields cannot select the associated Person. Posture summaries require both ISMS read access (including its risk-permission equivalents) and `events.read`, and the People and Evidence features must be enabled.

A bounded latest-evidence index keeps up to 128 provider/scope streams per agent, references the retained events with cascading deletion, and uses event observation time with a 24-hour freshness window. Revoked or expired credentials cannot contribute completed targets, and only envelopes that name one of Keen's catalogues reach the summary. Changing an assignment clears its current index and its posture risk links, and excludes reports observed before that assignment, while preserving raw evidence; the risks stay in the register, and a risk recorded for the previous holder no longer marks the device's checks as accepted.

Device reports are signed assertions by the key holder; a signature establishes which enrolled key produced a report and that it is unchanged, and a compromised device can still sign false reports. Registered collector names identify devices; operators issue a separate credential per device. Account-to-Person provisioning reuses only the unique account ID association and never claims an existing unlinked Person by name or email.

Recording a risk from a posture check additionally requires `risk.manage` and the Risk management features; the workstation must belong to the Person named in the request, and the risk, its workstation asset and its posture link are written in one transaction. A recorded risk can mark a check as accepted only while its treatment is Accept, it is not closed and its review date has not passed; acceptance never changes the stored evidence. Risk titles on posture results are shown only to risk readers.

Person and Vendor detail reads retain ISMS read authority and feature gates. Cloud posture on a Vendor page requires ISMS read access and `events.read` with the Vendors and Evidence features enabled; it shows only providers linked to that Vendor. Recording a risk from a cloud check additionally requires `risk.manage` and the Risk management features, and the provider, scope, check and listed subject must match the latest stored result for that Vendor's provider. Requesting a refresh, from the Vendor page or from Admin's ingest runners, requires an administrator session with CSRF and Origin checks; Keen queues an expiring refresh and activity read per enabled, unpaused provider and accepts one request per provider every two minutes from either place.

Named quiz assignments and results on a Person page additionally require `quiz.manage`; `quiz.read` alone remains a cohort-results permission. The summary excludes questions, answers and grading material.

### Installation feature availability

Administrator-only feature settings supplement existing permissions. Disabled features reject their operational API routes, including for administrators. Settings use revision-checked database writes; guarded requests check the authoritative revision before reusing a cached configuration. Feature caches contain no user authority or credentials.

Existing records, historical audit material and retention protections remain intact. Availability settings are not per-user confidentiality boundaries; shared references can remain visible through other enabled features. Authentication, account security and administration remain available. Requests and jobs already admitted can finish.

## Browser boundaries

### CSRF, Origin and WebSockets

Cookie-authenticated writes require a matching CSRF cookie/header token and reject any browser Origin other than `KEEN_PUBLIC_BASE_URL`; the request's Host and forwarded headers play no part in that check. A signed-in browser without the CSRF cookie, such as one authenticated by the trusted proxy, receives a fresh random token with its next authenticated response. MFA ceremonies require `KEEN_PUBLIC_BASE_URL` as their Origin, and its host is the WebAuthn relying-party ID.

Notification WebSockets require an allowed Origin and an authenticated identity; connections are limited in each API process (`KEEN_API_WORKERS` sets how many run) and authorization is rechecked at intervals of up to 30 seconds. Notification connections release database sessions between checks. A change or revocation can take up to that interval to close an existing notification connection. Notification connections and sends are bounded.

### Rendering, links and navigation

Plain text is escaped before HTML insertion. Rich text is reduced to a formatting allowlist on the server and sanitized when rendered. Every rich-text editor starts through one shared helper. The vendored editor parses the content it is given unsafely, turning escaped markup back into elements, and does the same to its own top-level text after every edit. It therefore always starts empty and receives saved content only through the shared sanitiser, pasted HTML is sanitised before its own paste handling sees it, and the helper keeps all top-level text inside paragraphs as content is loaded and before the editor reads each edit. Links and post-login destinations are validated separately from HTML escaping. Same-origin link paths exclude backslashes and control characters, which browsers would otherwise resolve to another host.

A path the URL parser writes out again, such as a post-login destination or a link carrying the selected framework, is checked again, since resolved dot segments (`/.//host`) can leave it beginning with `//`; SSO sign-in destinations and saved-search URLs also exclude dot segments, including percent-encoded ones. Imported upstream guidance links must be absolute HTTP(S) URLs.

The supplied nginx policy restricts scripts to the installation's own origin and blocks inline scripts, string evaluation and embedded objects. Inline styles remain allowed for the UI.

### Request paths through the proxy

The supplied nginx redirects a request whose path begins with `//` to `/` before choosing a location, since nginx merges such a path into one it serves while the browser keeps `//host` as the page's path. A request whose path begins with `//api/` gets 404, so an API client whose base URL ends in `/` sees an error and keeps its request method. A front proxy that rewrites the path hands nginx the merged path instead, so every Keen page outside Help replaces itself with `/` when its path begins with `//`, and the front proxy forwards the request URI unchanged.

Pages build API paths from record identifiers and query values and send them with the page's session and CSRF token, so the supplied nginx forwards an API request's target exactly as the browser sent it, less the `/api` prefix, and refuses one that only normalises to `/api/`; the API refuses a target with a `.` or `..` segment, or with an encoded slash or backslash outside a route's `:path` parameter, so an encoded `../` inside an identifier cannot reach another route. The browser resolves a `.` or `..` segment, encoded or not, before a request leaves, so the UI's API helpers refuse a path containing one. A front proxy must likewise forward API targets without decoding them or resolving their dot segments.

### Downloads, exports and caching

CSV exports neutralise formula-like text before it reaches spreadsheet cells. Download filenames are encoded and stripped of header control characters. Downloads are served as attachments with `nosniff`, and keep their recorded media type only when it is one a browser displays or saves without interpreting it (PDF, PNG, JPEG, GIF and WebP images, office documents, plain text, CSV and JSON); any other type, such as script, style sheet or markup, is served as `application/octet-stream`, so an uploaded file cannot act as same-origin script or style whatever its uploader declared.

PDF previews are rendered in a resource-limited child process and cached in memory with per-process byte and entry limits. Masked text downloads have a 20 MiB input limit. Application responses use a no-store cache policy.

## Inbound machine interfaces

### Webhooks

Inbound webhooks require configured secrets; missing secret configuration fails closed. A generic evidence webhook's secret travels with each delivery, so whoever obtains a delivery can also send new ones; these webhooks reject identical deliveries within the replay window, and a delivery that fails to persist releases its claim so the sender's retry is accepted.

Deliveries whose secret verifies draw on an allowance of 100 requests a minute from each client address for each provider defined in the `webhooks.yml` Keen ships, and 100 shared by every other provider name; verified effectiveness-metric submissions have a separate allowance of the same size. Refused deliveries and submissions draw on their own allowance of 100 a minute for each client address, so a sender without a secret that shares an address with a genuine one, as customers of a hosted Git service do, cannot spend the genuine sender's allowance. Nothing reveals whether a connection exists or ingestion is paused before the secret or signature verifies.

Storing a delivery costs processor time in proportion to its size, so each provider on a webhook connection accepts at most 32 MiB of deliveries a minute, from any number of addresses, counted once its secret is verified; a holder of one provider's secret spends only that provider's allowance on that connection.

### Effectiveness-metric submissions

Effectiveness-metric submissions are signed instead. Each measure has its own secret, generated by an ISMS manager, shown once and stored encrypted with `KEEN_INTEGRATION_SECRET_KEY`; the sender signs the timestamp and the exact body with HMAC-SHA256, so a captured submission reveals no secret. A submission's body is limited to 256 KiB as it arrives.

Keen accepts a signature within five minutes of its timestamp and only once, answering 409 to a repeat, and refuses an unknown measure, a measure without a secret and a wrong signature alike before it parses the payload or reveals a pause. A holder of one measure's secret can submit results for that measure only.

### Keen Agent identities and enrolment

Keen Agent identities authenticate with Ed25519 keys generated on the device; Keen stores only public keys. A single-use enrolment code (24-hour lifetime) or an enrollment profile's high-entropy bootstrap key binds a key once the client proves possession of it with a signature made for that attempt: each proof names its creation time and a nonce, is accepted only within five minutes and only once, and is refused while Valkey is unavailable, so a recorded enrolment request cannot be replayed to obtain the grant. Keen retains only hashes of codes and bootstrap keys.

Profiles support expiry, enrollment limits and network restrictions, and profile revocation revokes its agents and their keys. An agent's and a profile's enrolment allowances are spent only by requests that hold that agent's code or that profile's key, so wrong credentials from someone who knows an identifier cannot hold up an enrolment. Possession of a profile's bootstrap key permits enrollment within that profile's restrictions, under a name no active agent outside the profile holds, compared without regard to case; machines within one profile may share a name, and a retry with the saved nonce returns the agent it enrolled.

Profile enrolments and administrator registrations of one name are serialised. A profile agent can still take a free name that an administrator registers later, or one a pattern-based mapping rule matches; scope device rules to exact names and review profile enrolments. Distribute bootstrap keys through a protected secret channel.

A compromised agent key permits actions scoped to that agent, namely evidence submission, heartbeats and renewal, until revocation. Identities registered for generic OTLP collectors use hashed bearer tokens instead and cannot sign.

### Keen Agent requests

Each request carries an RFC 9421 signature over its method, operation, body digest and content type, valid for five minutes and accepted once: nonces are recorded in Valkey, and requests are refused while Valkey is unavailable. A signature for one operation is refused by the others. Renewal binds a new key in a request signed by the current key and retires the old one immediately; for 24 hours the retired key can repeat only the same renewal.

Signed requests are rate-limited per identity before their nonces are stored. An agent's requests never wait for one another: one that arrives while another of the same agent holds the agent's row is refused at once (429) for the agent to retry, agents of one enrolment profile share its row while their requests run, and a request waits at most five seconds for that row while an administrative change or an enrolment holds it. Enrolment takes no row lock until its credential matches the record it names, so a request without the credential cannot hold up that agent or profile.

### Keen Agent encryption

Keen Agent traffic is encrypted end to end to Keen, so TLS termination, CDNs, load balancers, inspecting middleboxes and a party that obtains a valid certificate for Keen's hostname see only ciphertext. Keen holds an Ed25519 identity key whose 128-bit fingerprint ends every enrolment code and bootstrap key; agents pin it before sending anything and verify Keen's encryption key against it through the unauthenticated `/v1/agents/server-key` document, which carries only public keys. Every keen-agent request, the enrolment credential included, is HPKE-sealed (MLKEM768-X25519, a hybrid post-quantum KEM; HKDF-SHA256; AES-256-GCM) with `info` binding the operation, Keen key and sender key; the request signature covers the sealed body and the named encryption key.

Keen refuses plaintext from key identities (415), opens a request only after its signature, digest, time window and nonce check, and answers 421 for a key it no longer holds. Each sealed request carries a one-time response key, and Keen seals success responses with it under AES-256-GCM bound to the operation and status; agents act only on responses that open, so a party without Keen's keys cannot acknowledge evidence out of an agent's queue or alter a grant.

The encryption key rotates every 30 days; agents may use one for 37 days and Keen erases its private half an hour after that (each API process also drops an expired key from memory when it next opens one; restart the API and worker after replacing the identity key), bounding what a later compromise of Keen's stored keys can open of recorded traffic. Erasure applies to the live database: PostgreSQL backups and archived WAL keep the encrypted private halves for as long as they are retained, so that bound holds only for backups kept about 37 days or less, or kept apart from `KEEN_INTEGRATION_SECRET_KEY`. Private keys are stored encrypted with `KEEN_INTEGRATION_SECRET_KEY`; whoever holds that key and the database, or controls the API process, can read agent traffic it receives.

Replacing the identity key (`scripts/agent_server_key.py --reset --confirm-fingerprint=<fingerprint>`) invalidates every issued code and bootstrap key and revokes every agent key, so each agent must enrol again. An enrolment that was waiting when the reset committed is refused, since it checks the pinned fingerprint again once it holds the locks the reset takes. Bearer-token OTLP collectors remain TLS-only.

### Evidence envelopes and catalogues

Evidence envelopes must be in RFC 8785 canonical form; duplicate member names, non-integers, integers beyond ±(2^53 − 1) and lone surrogates are refused, so the stored bytes are the hashed bytes. Batch manifests signed by a known key must verify, or the whole request is rejected, as is a batch signed by an enrolled agent's key and sent by any other identity; unsigned batches and batches signed by unknown keys are accepted and labelled. Event pages show unsigned and unknown-key batches as such, and list every change Keen made before storage beside the signature result.

For catalogues Keen holds, envelopes may carry only declared fields of their declared types and none of the type's prohibited fields. Keen grants agents its latest catalogue when they enrol or renew and in its sealed replies to their health reports, and still accepts envelopes that follow its earlier catalogue; an agent sends an event type only from a catalogue Keen granted it. The host events of Keen's 2026.11 catalogue name the program and the kind of fault, failure, task or protection, never the name looked up, a time server, the message text or an address; account changes carry the account only as a pseudonymous subject.

Programs on a host can write log messages under any name, so a recognised message is only as trustworthy as the host's log; the agent takes completed units, such as the daily apt upgrade job, only from journal records that journald attributes to systemd itself (process 1), and commands a root shell logs only from records it attributes to user 0 and the syslog socket, never from a syslog file, where the sender writes its own IDs. A logged command's event names its executable only when that is a program installed in the system directories; the command line itself reaches Keen only where a source's evidence mode includes lines.

Booleans, and integers and timestamps the catalogue declares, are kept whatever their names; undeclared integers under sensitive names and all text are redacted with the usual rules, and every change is listed in the evidence's custody record with the original hash. The identity's evidence mode limit is enforced on receipt from what each envelope contains (an included source line, a whole-line event), whatever mode the envelope declares. Only key-enrolled workstation identities feed posture.

The public subject pseudonym key is encrypted with `KEEN_INTEGRATION_SECRET_KEY` and sent only in enrolment and renewal responses to identities configured for public subjects; anyone holding it can test guessed account identifiers against public pseudonyms, while pairwise pseudonyms use keys that never leave their device.

## Outbound connections and collection

### TLS

HTTPS connections validate the certificate chain and server hostname. Keen has no supported option to disable HTTPS certificate verification. Install a suitable trusted CA for private certificates. TLS failure must be resolved through the trust configuration or upstream certificate.

Ingestion CIDR allowlists never disable TLS. Trusted-proxy CIDRs govern inbound proxy identity and do not affect outgoing encryption. Internal database, broker and application hops use their deployment-configured transports; they are not covered by a claim about upstream TLS.

### Destinations and request time

Built-in HTTP collectors validate destination addresses and pin connections to validated addresses while preserving TLS hostname verification. Redirects and ambient proxies are disabled in that transport, and each request has one 120-second wall-clock budget from connecting to its last body byte: every TLS handshake, read and write waits at most for what remains, so an upstream that sends its headers or body a byte at a time cannot extend it. Custom API requests apply the same bound to their 20-second timeout.

Every request on those paths, and every identity-provider request, has one deadline from connecting to its last byte, response headers included. Cloud SDK, LDAP and SMTP connections use their libraries' transports and time out each socket operation instead: collection and mail sending run in worker tasks whose limits bound them as a whole, and LDAP sign-in waits on a directory the operator runs.

Loopback, link-local and other prohibited address classes are rejected, including IPv4-mapped equivalents. `KEEN_INGESTION_ALLOWED_CIDRS` restricts permitted private destinations; an empty setting permits private networks apart from prohibited addresses.

Custom API collectors additionally require an operator-managed host and port allowlist (`host` permits HTTPS port 443; `host:port` permits that explicit port) and explicit private-network CIDRs. Their definitions are declarative; they do not execute supplied Python, shell commands or templates. AWS and Google collectors use their respective SDK transports.

### Credentials stay with their connection

Upstream credentials and cached authentication state belong to the selected connection. GitHub audit-log collection follows upstream pagination links only within the configured API origin and base path, refusing dot segments whether or not they are percent-encoded, so its token stays with that API.

Saved credentials are write-only: changing a connection's endpoint, origin, authentication mode or credential header requires its credentials to be supplied again. Custom API connections apply the same rule to any change to the base URL's scheme, host, port or path, to the credential header or query parameter name and to the OAuth token path. Custom API request, detail and OAuth token paths cannot contain `.` or `..` segments, percent-encoded, behind encoded slashes or backslashes, or with `;` parameters, and a detail request whose substituted record value forms one is refused, so the credential stays under the base path. Next-page links may lead anywhere on the connection's origin.

### Outbound notifications

Outbound notification webhooks use the built-in validated, pinned HTTPS transport and its private-network restrictions. Question notifications for Slack, Google Chat and Teams carry question text, usernames and event summaries as text: Slack's markup characters are escaped, and the link and mention brackets the other services read become lookalike characters, so untrusted text cannot form labelled links or mentions; a bare URL in that text can still appear as a link, as each service shows URLs typed into ordinary messages. With a signing secret, each delivery carries `X-Keen-Timestamp` and `X-Keen-Signature: v1=<hex>`, an HMAC-SHA256 over the timestamp and exact body in the signed-submission scheme, so a receiver that applies a five-minute window and refuses repeated signatures can reject replays. Links in outbound messages and emails take their host from `KEEN_PUBLIC_BASE_URL`, which every Keen process requires at start-up, so request headers cannot point a posted link at another site.

Operator-configured SSO, SMTP and storage endpoints have separate transport paths and trust requirements. SMTP authentication requires STARTTLS or implicit TLS. Apply network egress policy to constrain the destinations available to the whole deployment.

### Secrets and redaction

Credentials saved for integrations are encrypted with `KEEN_INTEGRATION_SECRET_KEY`. TOTP uses its separate MFA encryption key. These keys live outside the database and must be protected and backed up. Encryption protects a database copy without its keys; a running application with the keys can decrypt the credentials it needs.

The shared ingestion path redacts recognised secret keys in structured payloads and text artifacts, and applies secret redaction to event summary fields. Effectiveness-metric submissions receive the same redaction, and optional event-data masking, in their stored text fields and payload copy. The audit trail omits authorisation codes and state from sign-in callbacks. URL userinfo and recognised secret query/fragment parameters are removed from displayed URLs.

Administrator listings of collection inputs, including deployment-managed connection inputs, withhold values under credential names, qualified names such as `X-Api-Key`, `Private-Token`, `Ocp-Apim-Subscription-Key` and `X-Functions-Key` included, and every RSS feed's custom headers and credentials; collection uses the stored values. Collector diagnostics use bounded, generic messages; SQLAlchemy error output omits bound parameter values, and an agent batch that fails to persist is logged with its digest, the sending identity and the error class alone.

Redaction is best effort: arbitrary prose, unknown key names, secrets embedded in URL paths and binary artifacts may retain sensitive information. Configure additional sensitive keys where needed and avoid placing secrets in event content, labels, URLs or definition fields that appear in evidence and exports.

### Provider diagnostics and logging

Provider diagnostics are treated as untrusted data. Built-in HTTP collection suppresses HTTPX request logging, and Google Workspace suppresses the SDK HTTP/discovery loggers that emit response bodies and request URLs. Forgejo/Gitea status diagnostics include only the numeric status code. Other library wire/debug logging must remain disabled: it can expose headers, tokens and response bodies. Recheck emitting logger names and actual dependency behaviour during upgrades; parent-logger filters do not filter descendant records.

These controls do not prevent an upstream from placing credentials it knows into ordinary evidence under unrecognised keys. Sensitive-key redaction is not a general secret-content detector. Deployment operators can access process memory, environment secrets and encryption keys; restrict diagnostic tooling, core dumps and access to logs accordingly.

### Worker resource separation

Background HTTP collection uses the `ingest-http` queue and a dedicated worker. Scheduled built-in collectors, named HTTP connections, custom API runs and Admin Ingest runners use this queue; Admin runners enqueue expiring jobs after enablement/pause checks and retain administrator/CSRF enforcement. Internal jobs retain `celery`; IMAP retains `imap`. Queue routing separates capacity only: every queue shares the same broker authorisation and tenancy, and workers share the deployment's database/storage credentials.

Existing queued jobs retain their original queue and must be drained during an upgrade. Interactive catalogue lookups and inbound agent/webhook requests remain API operations.

The supplied HTTP worker defaults to one process, prefetch multiplier one, recycling after 100 tasks, 768 MiB memory, one CPU and 128 PIDs. Each source type's scheduled run ends within its own budget, set below the interval between its runs and adjustable with `KEEN_<SOURCE>_RUN_SECONDS` for a source whose legitimate runs take longer, such as Loki during a large log spike. `KEEN_COLLECTION_RUN_MAX_SECONDS`, 55 minutes by default, caps every budget and bounds a manual run of one connection. Collectors store and commit each input, page, chunk or day as they go, so a run stopped at its budget keeps what it collected and the next run continues from its cursor.

A scheduled collection that waits more than ten minutes for the HTTP worker expires, and the source's next scheduled run collects from its cursor, so no backlog builds up behind a slow or hostile source. Risk Ledger's budget sits above the longest per-connection run it accepts, 30 minutes. Cloud posture tasks keep soft and hard limits of 25 and 30 minutes, custom API runs 140 and 160 seconds, and every other worker task ends within 55 minutes; long internal work, such as a rule backfill, runs as a series of short tasks.

Google Workspace and AWS SDK requests keep per-socket timeouts, which these task limits bound. Runtime bounds in transports remain necessary; container limits do not provide transaction recovery or reliable health reporting by themselves.

The Python API, workers and beat drop all Linux capabilities and set `no-new-privileges`, retaining the non-root image user and default container seccomp policy. No added capability is needed for ordinary TCP clients or the API's port 8080. Writable data volumes remain necessary. Database/cache/nginx images have different startup ownership needs and do not inherit this capability policy. Hosted tenant provisioning applies the same worker separation and includes the HTTP worker in monitoring.

### CloudWatch and Google Workspace

CloudWatch rejects a non-HTTPS SDK service endpoint, including environment/shared-configuration endpoint overrides, before issuing collection requests. CloudWatch retains the SDK's certificate-chain and hostname verification, including when a custom CA bundle is configured. AWS credential discovery can use the platform's link-local instance/container metadata service; this is a separate operator-controlled bootstrap trust boundary.

Google Workspace validates the service-account token URI before constructing credentials, including file and base64 inputs. Google token refresh and Reports requests share an explicitly certificate-validating transport with hostname checks, a 30-second socket timeout, redirects disabled and no environment proxy. Plain HTTP requests are rejected before network access.

### Cloud posture

Cloud posture reads each provider with deployment-environment credentials that belong to that provider alone; AWS posture uses its own `AWS_POSTURE_*` names, so its keys are never the ones CloudWatch collection reads implicitly. It rejects non-HTTPS SDK endpoints, applies 10-second connect and 30-second read timeouts with bounded retries, assumes each account's role with an external ID for 15 minutes, and confirms the account through STS before reading. Reads are bounded: up to 100 accounts, 40 regions, 50 pages per EC2 or RDS listing, a 16 MiB credential report, 5,000 Security Hub findings and 1,000 activity entries per six-hour window; a listing that would be incomplete makes the check Not established.

Microsoft posture uses the built-in HTTP client: each session is bound to one origin, a paging link to another origin is refused before the token is sent, a throttled request is retried once after at most ten seconds, the client credentials' tokens last for one read, and Azure must confirm that each subscription belongs to the configured tenant. Google posture uses a certificate-validating HTTPS-only transport with a 30-second timeout and redirects and environment proxies disabled; a service-account key over 64 KiB, or one whose token endpoint is not HTTPS, is refused unread, each client holds only the scopes its read needs, and a Workspace user listing that spans customers is refused.

Akamai Cloud (Linode), DigitalOcean and Scaleway posture also use the built-in HTTP client, each bound to its provider's API origin with its token or secret key in a request header; listings are requested by page number or page token and links in responses are never followed, a listing longer than its page limit makes its check Not established, and per-resource reads (security group rules, policies, database and cluster access lists) are capped at 200 each. DigitalOcean's custom scopes and Scaleway's read-only permission sets keep those credentials read-only; Linode's read-only token scopes do the same, though its user and sign-in listings need a token created by an unrestricted user. Scaleway activity drops request bodies and telephone numbers before an entry is stored.

Failures are stored as fixed reason codes, and exception text is never logged or shown. Collection runs before any database write, so no transaction waits on a provider. The latest results, including identity names and email addresses, are kept in Keen's own table and removed after 30 days without a refresh; check results and activity entries are ordinary events subject to redaction, masking and retention. Each scope a provider reports, up to 100 a run, is an asset in the Cloud services category, named from the provider's own label and kept until an administrator removes it; Keen never changes an existing asset, so a provider cannot rename or move one an administrator has edited, and the asset names it adds are text the asset register shows as text.

### IMAP mail collection

#### Connections and transport

IMAP is disabled by default and unavailable in demo mode. Each named connection selects one account and folder; passwords use the existing encrypted credential store or approved file-managed secret references. A host, port or username change requires saved credentials to be supplied again. The credential destination check uses the same parsed host/port and exact quoted username as collection; username whitespace and trailing slashes remain significant.

The transport uses implicit TLS with certificate-chain and original-hostname validation, pins a validated resolved address, rejects loopback/link-local/multicast/reserved destinations and requires explicit `KEEN_INGESTION_ALLOWED_CIDRS` for private addresses. There is no plaintext, STARTTLS fallback, redirect/referral, proxy or compression mode.

#### Worker isolation

The supplied Compose `imap` profile runs a dedicated, single-concurrency queue worker with container CPU, memory and PID limits. Scheduled connection order rotates each five-minute period so the same later connections do not repeatedly lose their jobs to expiry under sustained load. Each connection has a server-validated interval of 5–1,440 minutes in five-minute steps, defaulting to five minutes.

Poll admission time is persisted under the existing connection run lock before network work; scheduled and manual runs, including failed attempts, respect it. Failure backoff can extend the wait. The dispatcher checks every five minutes, so actual polling is rounded to a subsequent dispatch and depends on queue capacity. The administrator-only, CSRF-protected Ingest runners action queues an expiring dispatcher; HTTP requests perform no IMAP network work. Runtime enablement, demo restrictions and pause state are checked before dispatch and again by collection.

Each poll performs network work in a short-lived subprocess with a minimal environment (Python encoding, executable search path and sensitive-key redaction settings), closed inherited file descriptors, a 256 MiB address-space limit, eight CPU seconds, 32 file descriptors and disabled core dumps. Its initial alarm expires after 50 seconds; collection resets the alarm to a 45-second wall-clock budget, followed by a five-second serialisation/output budget. The parent kills and reaps it after 55 seconds. Socket operations also have timeouts within the collection deadline. The independent alarm bounds slow-drip responses that keep buffered reads active. Celery adds 65/75-second soft/hard task limits.

These boundaries isolate blocked DNS, TLS, server responses and MIME processing from ordinary ingestion capacity. They are resource controls only; a compromised Python runtime keeps the worker's operating-system privileges.

#### Response limits

Responses are limited to 4 MiB and 2,048 lines per poll, 32 KiB per line and at most 1 MiB per literal; server-declared literals are checked before reading. The literal allowance is zero during greeting, authentication and metadata commands; each explicit BODY.PEEK request permits exactly one literal, including an empty one, and the allowance is cleared on success or failure. Unsolicited and extra literals are rejected before their payload is read.

Each poll searches at most 100 UID positions and imports at most ten messages. Collection also measures each item's escaped JSON representation against a 5 MiB output budget, reserving envelope space and returning a resumable batch when necessary. The final serialisation and parent process retain independent size checks.

Selected headers have a 16 KiB wire limit and 200-line limit; MIME metadata has nesting, token and part-count limits. Partial `BODY.PEEK` requests select one inline plaintext body, or an HTML body when plaintext is absent. Attachment/named parts and attached-message subtrees are excluded. Multipart/related traverses only its identified root body, excluding sibling resources; an unresolved root is omitted. Malformed MIME parameters and dispositions fail closed.

FETCH validation binds UID, body section, zero offset and declared size to the same literal; metadata UIDs must be top-level fields. Contradictory UIDVALIDITY or UIDNEXT response values are rejected; identical repeats are permitted. HTML remains a string in a JSON artifact; it is never parsed, rendered or followed.

#### Bodies and redaction

Body collection defaults off, with a 4,000-character default and 65,536-character maximum when enabled. Transfer decoding uses fixed encodings and a small charset allowlist. Headers-only polling does not request or parse BODYSTRUCTURE.

Unsupported body charsets and invalid or unsupported transfer encodings omit the body, retain headers with a fixed omission reason and allow the cursor to advance. No raw undecoded body is retained. UID/section/size, transport and MIME-structure failures still stop the poll. Oversized headers or malformed/over-complex MIME structure with body collection enabled can block the current polling window and need operator investigation.

Sensitive-key redaction runs before character truncation and again through shared persistence; existing privacy masking applies. Indexed event actor/system labels are capped to their database column lengths after redaction and masking, while the bounded mail headers remain in the payload and artifact. Unterminated quoted values for recognised sensitive keys are redacted through the end of the available input, including whitespace and a trailing escape. Recognised redaction keys are best effort, particularly for incomplete wire data and arbitrary prose.

#### Mailbox state and failures

`EXAMINE` and `BODY.PEEK` preserve server-side read flags. Collection never writes flags, expunges or deletes mail. The first poll establishes the current UID baseline, avoiding an automatic historical import. Cursors, deduplication and artifacts are scoped to the connection, endpoint/account/folder and UIDVALIDITY. A changed UIDVALIDITY pauses collection until an administrator creates a reviewed replacement connection.

Failures preserve progress and impose exponential backoff. A hostile server can misrepresent mail, attachment metadata, sizes or UIDs; evidence provenance remains subject to review. Operators control mailbox access, evidence retention and disk capacity; per-poll limits do not cap lifetime storage.

#### Future IMAP work

Future IMAP work must retain actual-transport tests for TLS chain/hostname/expiry and credential ordering; DNS rebinding and private-address restrictions; literal limits before allocation; slow/dripping responses, process termination and queue isolation; MIME depth/part/decoding bounds and poison-message recovery; unsupported-charset omission before redaction; runner authorisation/CSRF and dispatch-time pause checks; attachment exclusion; HTML-as-text behaviour; custom redaction and truncation inside quoted secrets; related MIME root selection and malformed dispositions; same-response UID/section/offset/size binding, contradictory mailbox metadata and single-request literal allowances; server header projection into PostgreSQL column bounds after masking; escaped-Unicode output budgets and resumable batches; exact account identity in credential-change guards; read-only/PEEK commands; cursor reset, replay, concurrent polling and storage failures.

OAuth, custom trust stores, historical imports, additional encodings or mailbox mutations require separate security review. Maintain Python/OpenSSL and exercise the supplied queue/container limits under PostgreSQL/Valkey integration tests before deployment.

## Availability and resource bounds

### Requests

Request bodies are bounded before parsing, including chunked bodies: to 40 MiB in general, and to 100 MiB for ISMS document files and audit final reports, which are hashed and stored from the request's temporary file in bounded chunks. The application parses at most 2 MiB of JSON from a request, whatever content type it declares, or 40 MiB from an administration request under `/v1/admin/`; webhooks and agents read their bodies under their own limits. A request's body, up to its route's limit, is read before its credentials are checked, so nginx's `client_max_body_size` and connection limits also bound what unauthenticated clients can send.

The supplied nginx reads a request's whole header within its 60-second default and buffers the body before passing it on, and the API reads any body within 30 seconds, so a client that sends its headers or body a byte at a time holds no API worker.

JSON is decoded in a worker thread; Python's decoder holds the interpreter lock while it runs, so a large document can still pause the process briefly. Multipart and URL-encoded forms, whether a handler or a declared file parameter reads them, are spooled to a temporary file and parsed in a worker thread. Each API process serves every request from one event loop, so the authentication and audit-trail middleware, webhook and metric deliveries, question posts, diary entries, imports and sign-in callbacks do their database, Valkey, storage and broker work in the thread pool; a slow query or a large delivery then holds up only its own request, and a contract test refuses a coroutine that does such work itself.

JSON request bodies must be standard JSON nested no deeper than 64 levels; non-finite numbers are rejected. Outside the administration namespace a JSON body holds at most 20,000 values, since FastAPI checks a body against its route's model on the event loop at a cost for each value and for each error it finds; the costliest accepted body takes a small fraction of a second. PostgreSQL cannot store NUL characters, so request paths and query strings containing them are rejected, while JSON request text, question form text and collected evidence store them as U+FFFD replacement characters; one such record cannot halt a collection run. Validation errors name the invalid fields without echoing submitted values.

### Collected data

Collected structures are walked at most 64 levels deep: a JSON log line nested more deeply is stored as text, a deeper JSON artifact is redacted and masked as text, and the deeper parts of other collected payloads are stored as `[nested too deeply]`, so no record exhausts the interpreter's recursion limit or stops its collection run. Collected labels longer than their event columns are clipped after redaction and masking, so no collected value is refused by the database and none can halt a collection run. A custom API record whose values its definition cannot use (a missing or empty identifier or summary, an unreadable or future timestamp, a link that is not HTTP or HTTPS, an out-of-range value, or a detail path its values would steer) is left out and counted in its run's result, and the cursor moves only past stored records, so the rest of the run is kept and a future-dated record is read again once its time arrives. Feed dates longer than 64 characters are not parsed, since the date parser's time grows with the square of its input, and custom API `Link` headers are read in time linear in their length.

RSS, Atom and Git activity feeds are refused when their document type declaration has an internal subset, where entities and default attribute values are declared, so a parsed feed holds no more text than its response; a declaration naming an external DTD is accepted and that DTD is never fetched. A parsed tree costs many times its text whatever the document's shape, so feed documents over 4 MiB are refused before parsing; the costliest accepted document, and the costliest JSON within the collection response limit, parse within the HTTP worker's memory limit with room for its baseline, and tests measure both against the limit Compose sets.

GitHub, Gitea and Forgejo activity listings and BookStack page listings keep their pages until they have them all, and read them within one allowance of 16 MiB of upstream data, the collection response limit, whose costliest kept pages tests measure against the same limit; a listing that would keep more is refused with an error for that input, and a listing read inside another draws on what that one has left. When Gitea or Forgejo activity since the cursor exceeds the allowance, the collector reads it a day at a time instead, storing and committing each day with the cursor before the next, and fails only at a day that alone exceeds it. GitLab stores and commits project activity a page at a time. Repository and project discovery keeps only the names and identifiers it uses, a page holding more items than requested is refused, and Gitea and Forgejo activity paging stops at the first page older than the cursor. A BookStack input that names several books holds every book's listing for the run, each read within its own allowance, and cloud posture collectors keep the inventory a provider's management API returns, within their page limits, without this allowance.

### Regular expressions

Every regular expression the API runs compiles in `security/regex.py`. A pattern written in Keen's code passes a shape check as it compiles, at import for a fixed pattern, so the API does not start with one that fails: a greedy or lazy repeat of a group, capture or alternatives holds state for at most 1,024 repetitions in all, since Python's engine keeps state for each one, and an unbounded repeat inside another repeat is possessive unless the outer one is. Matching memory therefore does not grow with the text, and nested repeats cannot backtrack exponentially; the request and collection size limits bound the remaining polynomial time. Redacting and masking megabytes of quoted evidence takes a few copies of the text, which tests measure at 4 MiB. Field patterns that request schemas declare meet the same check.

Rule matching limits patterns to 512 characters, reads at most the first 4,096 characters of each value, and limits each match to 25 ms of processor time and the compiled-pattern cache to 256 entries. Within that window an ordinary pattern uses a small fraction of its time, so evidence of any length is stored and mapped. A pattern that still exhausts its time on an event is the rule's own: that rule does not match the event, the event is stored, every other rule still applies, and the API log names the rule without any event content. A rule's exact conditions on connection, collector and source are checked before its patterns, so its patterns never read events from other sources.

A match keeps Python's interpreter lock while it runs, so other threads' Python work is not charged to its time limit, and a match ended by other threads' native work before using 25 ms of its own thread's time is tried again, up to three attempts in all. Unsafe repetition, backreferences and expanded mode (the `x` flag, under which Python reads some quantifiers as text) are rejected. These limits also apply to file-managed mapping patterns and BookStack configurable selectors. Fixed BookStack HTML-preview patterns use 25 ms substitution timeouts and a 65,536-character preview input cap; a timeout omits the optional preview. Per-match limits do not establish a total CPU budget across many events and rules.

Event-filter regular expressions match in PostgreSQL. They meet the same pattern checks, and back-references (a backslash followed by a digit) and lookahead or lookbehind are also refused; on PostgreSQL, every event list, facet, export, volume chart and source graph that applies them stops each statement after 5 seconds.

### Queries and exports

Event volume charts reject windows needing more than 5,000 buckets before querying. An event export reads at most 50,000 events in pages ordered by time and identifier and stops after 60 seconds: a count that outlasts that time is an error response, and an export stopped after its response has started ends with an `Export incomplete` line, or closing members in JSON.

### Audit trail

The request audit trail omits reads of the public status, sign-in methods and agent server-key documents, records requests made without a signed-in account or a verified machine credential, whatever their status, refusals and credential-free successes such as signing out alike, at most 60 times a minute from each client address, counting an IPv6 /64 network as one address (none while Valkey is unavailable), and deletes records older than `KEEN_AUDIT_LOG_RETENTION_DAYS`, 400 days by default (0 keeps them); a client using many IPv4 addresses or IPv6 networks can add that many records from each. Requests whose agent key, webhook secret, measure signature or enrolment credential verified are each recorded, within that credential's own allowances.

### Limits of these controls

These controls address individual-request resource abuse; deployment-level connection limits, quotas, worker capacity, monitoring and network rate limiting are needed for sustained or distributed traffic. Expensive reports, broad collection jobs and large authorised datasets consume resources. PDF resource limits reduce parser exposure; maintain the native parser and isolate the application processes. Keen does not claim complete protection against denial of service, dependency defects or every memory/resource leak.

A Keen installation serves one organisation, and its signed-in accounts are that organisation's staff, contractors or trusted external auditors. The bounds above keep any one request from monopolising an API process, but a signed-in account can still load the API by sending many requests, as a machine credential can within its allowances. Keen does not rate-limit ordinary authenticated use; sustained load from a signed-in account is an operational matter for administrators, who can deactivate the account.

## Evidence storage and integrity

Artifacts have recorded SHA-256 checksums and backend-specific storage controls. Each attempt to store an object writes under its own key, and local and hosted storage refuse to replace an existing object. Objects written by a transaction that does not commit are queued for the retention worker, which waits an hour and then deletes an object only when no record references it. An object can remain unreferenced until an operator removes it when its process is killed after storing it, or when the database is unavailable as its transaction ends.

Files attached to questions stay with their question threads; event views and evidence exports present only collected evidence artifacts. Checksums support integrity verification. Immutability and retention guarantees depend on storage permissions, versioning and retention configuration. Someone controlling both records and storage can alter the evidence and its recorded checksum.

## Images and build supply chain

The Dockerfiles, the Compose file and the CI and release workflows pin third-party images by multi-platform index digest, including the BuildKit and emulator images that build releases. A moved or replaced upstream tag therefore changes what Keen builds, tests or deploys only when a commit changes the digest.

A scheduled workflow proposes those commits as pull requests through a GitHub App after checking that each new index matches its digest and serves the release platforms; it never approves or merges them. It cannot judge whether upstream content is trustworthy: a maintainer checks the version change and upstream release notes before merging, and a branch rule requiring an approving review keeps that decision with a person. The workflow parses registry responses before the App token exists and accepts only well-formed digests, versions and dates from them. CI for an update branch runs the proposed CI images before review, with the secrets available to CI.

Workflows pin third-party Actions by commit. Python and npm dependencies follow their lock files. Distribution packages installed during image builds come from the distribution's signed repositories at build time and follow those repositories' updates. The Compose file names Keen's own images by release tag; operators can pin them by digest through `KEEN_API_IMAGE` and `KEEN_UI_IMAGE`.

## Deployment responsibilities

Use HTTPS for browser, agent and credential-bearing traffic; configure `KEEN_PUBLIC_BASE_URL`, Secure cookies and trusted proxies correctly. The supplied settings enable Secure cookies, which only a plain-HTTP local test should turn off. Production cookies must be Secure and scoped to the installation's host. Keep unrelated or untrusted applications off the same origin, and avoid sharing session cookies across sibling domains.

The provided Compose port bindings are local to the host, and Compose refuses to start until `POSTGRES_PASSWORD` and `KEEN_REDIS_PASSWORD` are set. Keep PostgreSQL, Valkey, worker control interfaces and artifact storage inaccessible to untrusted clients. Use separate strong secrets, restrict upstream account permissions, preserve encryption keys securely, and test backup restoration.

Keep Keen, locked dependencies, container images, the TLS proxy and native tools maintained. Disable unused source types and authentication methods, restrict private-network egress, and monitor authentication failures, ingestion errors, disk use and resource pressure. Validate configuration changes with the documented test workflow and operational checks appropriate to the installation.

### Diagnostic tracing

`KEEN_SQL_TRACE` and `tools/keen-perf-trace.sh` are diagnostics. While tracing, PostgreSQL logs every statement with its values, and statement comments and the service logs carry each request's method, path (with numeric and UUID segments replaced) and referring page path, without query strings. Trace only where that data may appear in the PostgreSQL and container logs, and delete captures once they have served their purpose.

## Required security review checklist

Reviewers check these boundaries after every round of changes and before a release. A green suite establishes only the behaviours it exercises. Record each applicable check's evidence, untested prerequisites and remaining limitations in the review or delivery; do not mark an untested boundary as verified. Use synthetic credentials, local network fixtures and disposable databases. Review the final formatted tree and exercise the assembled application, including middleware, as well as injected administrator fixtures.

- **Timing and account-discovery oracles:** compare existing, absent, inactive and externally authenticated accounts on login, recovery, linking and reauthentication paths. Check response bodies, status codes, lengths and expensive operations as well as timing. Require comparable password-derivation work on rejected local-login paths, constant-time secret comparison and bounded inputs. Rate limiting supplements these controls. Test the work performed deterministically; use repeated, interleaved measurements when claiming timing resistance. Network-backed identity providers have separate timing behaviour.
- **TLS identity and credential ordering:** verify trusted matching certificates succeed and trusted wrong-name, untrusted and expired certificates fail for every transport, including LDAPS and StartTLS. Prove that credentials are never bound or sent before verification. Inspect the complete library verification path: a disabled SSLContext hostname flag can coexist with explicit library hostname validation. Keep dependency-version regression tests and test custom CA stores.
- **Inbound ingestion credentials:** exercise every provider and connection type with absent, empty, incorrect and correct secrets, missing configuration, duplicate credential headers and legacy environment settings. No operator switch may turn a credential-bearing ingestion endpoint into anonymous ingestion. Preserve signature, replay, connection isolation, body-limit and rate-limit checks.
- **Validation and failure propagation:** send wrong magic bytes, misleading MIME types, malformed uploads and hostile filenames through the HTTP boundary. Confirm a rejected attachment rejects the operation without committing its question or post. Exercise read, storage and database failures; broad exception handlers must not turn them into partial success. Review orphaned-object cleanup separately from database rollback. Do not interpolate untrusted filenames or upstream diagnostics into logs.
- **Authorization inventory:** compare every mutation's method and full route shape with middleware policy and adapter permissions. Exercise permitted non-admin users, users without the permission, other owners, inactive accounts and unauthenticated callers. Check adjacent paths, extra segments, wrong methods and encoded separators. Permission-owned exceptions remain authenticated and CSRF protected; new routes do not inherit public status.
- **Logout and CSRF:** GET and other safe methods must not revoke sessions or mutate account state. Test intentional logout, cross-site GET/POST, missing and wrong CSRF tokens, expired sessions, pending MFA and provider logout. Check Origin against the configured public origin, including proxy deployments. Review cookie scope, sibling-domain cookie injection, duplicate cookies and whether double-submit tokens require session binding under the deployment's threat model. Never assume SameSite alone provides CSRF protection.
- **Proxy identities:** require an explicitly trusted direct socket peer and exactly one identity header. Test untrusted peers, duplicate headers, client-supplied forwarding chains, absent headers and malformed addresses. Enforce the same boundary in HTTP and WebSockets. Upstream proxies must strip client identity headers; forwarded client addresses do not establish proxy trust.
- **Resources, storage and previews:** bound cache bytes and entry counts, masking buffers, downloads, uploads, parser work, decompression, render concurrency and response bodies. Test real byte counts independently of metadata. Close streams on success, rejection and cancellation. Match media types exactly after parsing parameters. Validate object keys before backend selection, and exercise both local and S3 storage. Keep storage locations and credentials out of presentation APIs. Check cache eviction and response delivery together for races.
- **Administrative lockout and concurrency:** evaluate effective administrators after proposed group-role, membership, user-role and active-state changes. Cover multiple inherited administrators in one group, replacement with inactive or explicitly normal users, other surviving administrator groups, and concurrent operations. Verify transaction rollback and session/authorisation invalidation. A sequential lockout guard does not establish a concurrency guarantee.
- **Outbound destination authority:** review scheme, hostname, port, resolved addresses, redirects, DNS changes, proxy environment, timeouts and response limits independently. Distinguish app-managed integration destinations from operator-managed notification, SSO and storage destinations. Document any separate trust boundary and test its restrictions; a hostname allowlist alone does not constrain ports. Keep certificate checks enabled for all HTTPS paths and never log secret-bearing URLs.
- **Sessions, MFA and linking:** exercise fixation, replay, concurrent refresh/revocation, stale permission snapshots, account deactivation/reactivation and pending-ceremony confusion. Check OIDC issuer, audience, nonce, algorithm, PKCE, single-use browser binding and verified-email linking. Test factor reauthentication and recovery-code reuse through the assembled app.
- **Personal results and small groups:** check that each person's answers, scores and grades reach only that person, as the record's settings allow, and the people who manage the record; that aggregate views wait for their minimum group and leak no names or choices; and that reviews of answers close before the questions recur. Exercise concurrent submissions against closing and grading.
- **Class sweeps:** for each confirmed issue, record the property it violates, the search that found every path sharing it, and the contract or budget test that enforces it across the codebase. A fix at the reported site alone leaves the class open.
- **Untrusted data and isolation:** exercise SQL/configuration injection, stored and reflected XSS, unsafe links, CSV formulas, regex resource exhaustion and malicious upstream evidence. Trace credentials, caches, cursors, artifacts and redaction through two connections of the same provider. Check audit output, errors, logs and exports for leaks and preserve evidence identity and ownership.

The full validation workflow uses PostgreSQL and Valkey. SQLite, mocked transports and DOM tests are useful focused checks with a narrower reach: PostgreSQL locking/migrations, real-browser behaviour and resistance to sustained distributed attacks lie outside them. Treat missing coverage as an explicit review limitation that needs follow-up.

## Required review procedure

The checklist above is an inventory of security concerns. Reviewers also follow this procedure to establish which application surfaces and behaviours have been assessed. Apply it to the affected workflows after a change and to the complete installation before a release. A review of individual components does not establish the security of their interactions.

### Establish the review scope and attack surface

Record the exact source revision, final working-tree changes, dependency versions, build output and deployment configuration under review. Identify the supported authentication methods, storage backends, source types and optional features that are enabled or assessed. Do not use results from a different revision or configuration as verification of the delivered application.

Build an inventory from the actual code, build and deployment configuration. Include:

- Browser pages, forms, navigation, static assets, direct and dynamic module dependencies, and native browser actions.
- Reverse-proxy locations and rewrites, authentication subrequests, public asset exceptions, caching and response-header inheritance, and the externally reachable API paths.
- API reads and writes, public protocol exceptions, WebSockets, previews, downloads, exports and alternate representations of the same records.
- Workers, scheduled jobs, collectors, outbound notifications, queues, retries, administrative scripts and migrations.
- Databases, session stores, credential stores, artifact backends, caches, temporary files, logging and diagnostic outputs.

For each entry, identify its owner, entry points, authentication authority, required permissions or credentials, data handled, dependencies and applicable security properties. Reconcile the inventory with registered routes, forms and imports, worker entry points, source registrations and proxy rules. Explain exclusions. A surface absent from the review record is unreviewed; absence from the existing test suite does not make it out of scope.

### Trace complete workflows and sensitive data

For each sensitive workflow, trace an ordinary permitted operation and a realistic unauthorised operation from the external entry point to every durable write and externally visible result. Include the actual browser, proxy, API, service, database, session store, worker and storage boundaries that participate. Mark where identity is established, permissions are checked, untrusted data is validated, secrets are redacted, resource budgets are enforced and transactions commit.

Trace passwords, factor proofs, session identifiers, one-time links, upstream credentials and sensitive evidence through request URLs and bodies, redirects, browser history, referrers, proxy logs, application logs, exceptions, queues, caches, database records, artifacts and exports. Use synthetic recognisable values to detect unexpected retention or disclosure. A shared redaction helper protects only the paths that call it; inspect direct storage writes, bulk operations, snapshots and alternate export paths separately.

Treat reads and transformations as security boundaries as well as mutations. Search results, summaries, counts, relationship APIs, previews and exports must not disclose records that their callers cannot read. Sampling, linking, copying and sharing require separate checks for access to the source and authority over the destination. Record any intentional disclosure through authorised sharing and how that authority is established and revoked.

### Define and exercise authorisation rules

Maintain a review matrix for each resource and operation. `tools/security_e2e/access_policy.py` records the permission rule for every served route; the backend tests fail when a route has no rule, and the security suite checks a deployment against the rules. Distinguish read, create, edit, delete, share, sample, export, administer and ownership transfer where supported. Include anonymous callers, permitted non-administrators, users without the required permission, owners, other users, delegated administrators, inactive users, pending authentication ceremonies and machine identities as applicable.

Define permission implications explicitly. Read access does not establish write access; permission to edit one resource does not establish permission to read or share another. Application-wide administrators, delegated administrators and resource owners have distinct authority. Establish trusted ownership from server-side records, including records created by workers, and reject attempts to choose identity, ownership or permissions through client data.

Exercise these rules through assembled entry points with real authentication and permission resolution. Also check secondary routes and representations of the same object. A hidden button, an injected administrator fixture or a service test alone cannot verify the externally enforced authorisation boundary.

### Exercise failures, defaults and state transitions

Test what happens when the application code or a dependency that normally enforces a property is unavailable. Relevant cases include failed or delayed scripts and imports, missing assets, unavailable authentication or rate-limit stores, database errors, interrupted uploads, storage failures, malformed upstream responses, expired credentials, retries, cancellation and process restarts. Inspect native browser, framework, library, proxy and SDK defaults wherever application code normally overrides them.

For credential-bearing forms, verify their initial HTML, native method and destination, serialised fields, implicit Enter-key submission, and behaviour before handlers attach or when scripts fail. Credentials and factor proofs must not enter URLs or unsafe fallback requests. Verify that unauthenticated workflows can load their complete required asset graph with the expected status codes, media types and effective security headers.

Test state transitions as well as steady states: sign-in and sign-out, pending-to-authenticated ceremonies, permission changes, account deactivation and reactivation, credential rotation and revocation, ownership changes, source removal, audit completion and reopening, retention and deletion. Exercise duplicate delivery, replay, out-of-order operations and concurrent changes where they can affect authority, confidentiality, integrity or availability. Distinguish operations that are intentionally allowed to finish in flight from subsequent operations that must be rejected.

For rejected operations, check the absence of unintended side effects as well as the response. Examine database rows, stored objects, queues, notifications, cached authority and cleanup work. A rollback of one system does not prove rollback of another.

### Verify the enforcing deployment surface

Use a disposable installation and synthetic data. Verify each security property at the surface that enforces it:

| Property | Required evidence |
| --- | --- |
| Browser form behaviour, module loading, navigation, cookies and CSP | Real-browser checks through the supplied UI build and nginx configuration, including failed-script cases and unauthenticated access |
| Proxy path handling, static asset access and effective response headers | Requests through the actual proxy configuration, including rewrites, error responses and nested locations |
| API authentication, authorisation, CSRF and body limits | Requests through the assembled application with authentication, middleware and permission resolution active |
| Locking, concurrent revocation, migrations and transaction guarantees | Disposable PostgreSQL with the supported migrations and representative concurrent operations |
| Session-store atomicity, expiry and service failures | Valkey with representative configuration and controlled failure cases |
| Destination restrictions, TLS verification and credential ordering | Local network and certificate fixtures exercising the actual transport and supported dependency versions |
| Artifact persistence, downloads, immutability and cleanup | Representative local and S3 storage paths, including actual-byte checks and controlled failures |

`tools/security_e2e` exercises the browser, proxy, API and PostgreSQL concurrency rows above against a running installation, using synthetic accounts and data; see its README before use. Static inspection, SQLite, DOM tests and mocks provide useful evidence within their limits. Identify those limits explicitly. An inventory or static configuration assertion does not replace a browser or proxy test; a mocked transport does not establish its library's real network behaviour. Do not infer that an untested deployment surface is safe because a substitute test passed.

### Record evidence and change impact

For each applicable checklist item and workflow, retain a review record containing:

- The surface and security property being assessed, attacker authority and prerequisites, expected permitted behaviour and expected rejection or containment.
- The exact revision, configuration and dependencies, inspection or test performed, reproducible test reference and observed result.
- The evidence level: static inspection, isolated or mocked test, assembled application, or representative deployment integration.
- Status: **verified at the stated evidence level**, **failed**, **unverified**, or **not applicable with justification**.
- Untested prerequisites, supported configurations still requiring assessment, known limitations and required follow-up.

Test totals and coverage percentages supplement this record; they do not establish that every entry point, failure mode or security property has been tested. Do not describe a review as complete when applicable required evidence is missing.

For each change, identify the workflows and security properties it can affect. Trace callers and consumers of shared helpers, middleware, dependencies, proxy rules, schemas, build tooling and vendored assets. Include generated output and indirect consumers such as module imports, exports and worker tasks. A small shared change can affect a workflow whose own source files were not edited. Reassess the relevant boundaries on the final formatted and built tree.

### Retain regression coverage and enforce release checks

A confirmed issue requires a reproducible regression demonstrating the realistic prerequisite, the previously unsafe behaviour and the expected rejection or containment. Verify permitted behaviour as well. Run the test against the vulnerable version where practical to establish that it detects the defect. Inspect neighbouring entry points for the same underlying failure and add coverage for materially distinct paths.

Keep regressions in the ordinary test workflow. Where safe and practical, deliberately remove or bypass a guard in an isolated copy to check that its regression fails. A test that remains green without the guard does not demonstrate that boundary. Keep browser/proxy, PostgreSQL and Valkey integration checks in the release validation workflow so that every release runs them.

Before a release, reconcile the complete attack-surface inventory, authorisation matrix and evidence record with the final build and supported deployment configuration. An applicable critical security property without its required evidence remains an unmet release check. Failed checks require resolution or a documented change to supported scope that removes the affected exposure. A skipped test, unavailable environment or broad disclaimer does not satisfy the check. Record the release decision and its supporting evidence outside this policy document.

## Security claims and review records

The security-model sections describe current supported behaviour and realistic risks. Each security claim must identify its enforcing code or configuration and supporting test or other verification evidence in the review record. Where a supported configuration lacks evidence, record the gap and assess whether the claim needs qualification. Review requirements describe obligations; their presence does not assert that the required validation has been performed.

Keep findings, remediation history, per-review results, exceptions and release decisions in the corresponding review or delivery records. Do not turn this document into a cumulative list of fixes or describe an intended future control as current protection.
