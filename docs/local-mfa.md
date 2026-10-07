# Local account security

Local login supports a password followed by TOTP, a WebAuthn security key/passkey,
or a single-use recovery code. Account → Security manages authenticators.
SSO and trusted upstream authentication use the provider's MFA policy.

## Configuration

```dotenv
KEEN_MFA_POLICY=optional
KEEN_MFA_ORIGIN=https://keen.example.org
KEEN_MFA_RP_ID=keen.example.org
KEEN_MFA_ENCRYPTION_KEY=<persistent Fernet key>
```

Policies: `optional`, `admins` (effective administrator role), or `all` local
accounts. Required users must enrol before accessing KEEN. Existing unverified
local sessions are denied at their next authentication check.

The origin defaults to `KEEN_PUBLIC_BASE_URL`, and the RP ID defaults to its
hostname. Use the exact HTTPS origin without a path. HTTP localhost is accepted
for development. An explicit RP ID must match the hostname. Configure this before
enforcing MFA. Preserve Origin through the proxy and retain secure cookies.

Generate the encryption key using the built API image:

```sh
docker compose run --rm --no-deps keen-api python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Put the result in your secret configuration, use the same value across API
replicas, and back it up separately from PostgreSQL. TOTP enrolment requires this
key. Replacing it without re-encrypting existing records breaks existing TOTP
verification; restore the original key or perform controlled account recovery.
WebAuthn and recovery codes do not depend on the TOTP key.

## Enrol and recover

Confirm your password under Account → Security. Existing MFA users also verify an
existing factor. Scan the app QR code and confirm its six-digit code, or register
and name a security key/passkey. Up to ten WebAuthn credentials are allowed.

Save the ten recovery codes shown after first enrolment. Each replaces a second
factor once. They are displayed only once and stored only as hashes. Regeneration
invalidates all prior unused codes. A lost-factor recovery normally uses one code
to log in and another to verify factor management. Register a replacement before
removing the lost factor. Required accounts cannot remove their final factor.

A management window lasts five minutes. Password/factor/recovery-set changes
invalidate other local sessions. Continue to KEEN completes the current ceremony
with a fresh session. Password resets preserve enrolled factors.

An operator can reset factors after independently verifying the account owner:

```sh
docker compose exec keen-api python scripts/mfa_admin.py alice --reset --confirm-username alice
```

This removes factors, recovery codes and pending challenges, bumps the local
session version, and writes an operator-reset audit entry. It preserves the
password. Required accounts enrol again at next sign-in. Restrict container and
database access because those privileges can perform recovery.

## Implementation and deployment

Migration `0084_local_mfa` follows evidence retention migration `0083`. Deploy the
schema before restarting application processes. Default policy is optional;
existing users are not enrolled automatically. The demo provisioner and its SSO
configuration are unchanged.

Pending challenges are short-lived database records, independent of normal
sessions. Their cookies are HttpOnly, SameSite=Strict and Secure on HTTPS. Exact
Origin checks protect MFA POSTs. Rate limits fail closed. Per-user row locks
serialize TOTP replay checks, recovery-code consumption and factor changes.
WebAuthn validates challenge, RP ID, origin, user presence, signature and counters;
cross-origin ceremonies and mismatched user handles are rejected. It prefers user
verification while allowing password-plus-touch security keys without a PIN.

Fernet encrypts TOTP secrets, WebAuthn stores public keys, and random 128-bit
recovery codes are SHA-256 hashed. Secrets and submitted codes are excluded from
the MFA audit entries. The shared HTTP/WebSocket identity resolver checks the MFA
version and verification state for local sessions. Already-open WebSocket
connections are not actively disconnected by changing factors; checks apply at
subsequent authentication/handshake boundaries.

Online guidance is in Administering KEEN → Local two-factor authentication and
Using KEEN → Protect your local account. Both documentation copies are generated
from the portal's `docs/help-source` fragments.
