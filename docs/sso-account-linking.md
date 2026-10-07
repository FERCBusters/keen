# SSO account linking and verified email addresses

KEEN identifies linked SSO users by their configured provider issuer and stable
subject identifier. Existing identity links continue working independently of
SMTP and the email verification list.

For a new external identity, the provider's `AUTO_LINK_EXISTING` setting permits
linking only when the provider supplies `email_verified: true` and the complete
email address matches an address verified by KEEN for an active account.
Comparison ignores email case and surrounding whitespace. KEEN preserves plus
suffixes, dots and domains; usernames are never used to establish ownership.
Provider email-domain restrictions still apply. GitHub obtains verification from
its authenticated email API. A provider that omits verification cannot auto-link.
The legacy `*_USERNAME_CLAIMS` settings no longer authorize account linking.

## Verify an address

1. Configure SMTP and an HTTPS `KEEN_PUBLIC_BASE_URL` for this installation.
2. Sign into the existing KEEN account. Add/remove operations require a complete
   sign-in within the last five minutes, including the configured MFA ceremony.
3. Open **Account → Security → SSO email addresses**. Enter your primary email or
   an additional address you own, then select **Send verification link**.
4. While signed into that account, open the email link and select **Verify email
   address**. A link expires after 30 minutes and works once. Requesting another
   replaces the previous link. Password or MFA changes invalidate pending links.
5. Sign in through a configured SSO provider that reports that verified address.

SMTP is needed to prove ownership of addresses. Once ownership has been verified,
SMTP availability does not govern subsequent SSO linking or login. Verification
also requires an HTTPS public base URL; request Host headers never determine the
emailed destination. Links carry their secret in the URL fragment, which is removed
from browser history by the confirmation page. The database stores only its hash.
Confirmation uses an authenticated, CSRF-protected POST and explicit user action.
Email previews and scanners cannot consume a link with an HTTP GET.

An address may be verified for one KEEN account. Another user's primary address
cannot be claimed, and verification rechecks conflicts. Each account may retain up
to ten verified addresses and request five verification emails per hour. Existing
primary emails are not silently marked verified by the migration.

Removing an address prevents future automatic links through it. Existing
issuer/subject associations remain active. Administrators must separately review
and revoke unwanted existing identities and sessions. Changing the contact email
used for security notifications does not add an SSO address.

To onboard a previously unlinked user in an SSO-only installation, arrange an
existing authenticated access method before verification (for example, provision
a local account and temporarily permit local sign-in), or explicitly provision an
administrator-verified issuer/subject association through your controlled
provisioning process. The hosted demo bootstrap already provisions an exact owner
identity and continues to use that identity.

## Browser-bound SSO

Each sign-in transaction has a ten-minute, HttpOnly, host-only browser cookie. In
production it uses the `__Host-` prefix and Secure flag. Its secret and the expected
provider determine the state sent to the identity provider. A callback from another
browser or provider is rejected before token exchange; database consumption is
atomic and single-use. Starting another SSO sign-in in the same browser replaces
the prior transaction. Sign-in attempts begun before this patch must be restarted.
Keep `KEEN_COOKIE_SECURE=true` in production.

## Trusted proxy addresses

`KEEN_SECURITY_TRUSTED_PROXY_CIDRS` governs security notifications, audit records,
semantic event/question audit entries and IP-based rate limiting. KEEN walks
X-Forwarded-For from right to left through configured trusted proxy hops, stopping
at the first untrusted peer. An untrusted direct peer cannot supply its own client
address through forwarding headers. With no configured proxies, the socket peer
is used. Trust only actual proxies; ensure any Uvicorn proxy-header rewriting has
an equally restrictive trust boundary. Behind a proxy, configure these ranges so
clients do not unintentionally share one rate-limit address.

## Evidence discussions

Access to event questions, replies and user-owned question listings requires the
underlying event read permission. Knowing an event or thread UUID does not grant
access after the permission is revoked. Diary visibility checks still apply.
