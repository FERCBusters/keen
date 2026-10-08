from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KEEN_", case_sensitive=False)

    env: str = Field(default="dev")
    timezone: str = Field(default="Etc/UTC")
    # Visible date input format used by MOSP shared date widgets. API values remain YYYY-MM-DD.
    # Allowed: ymd/iso/YYYY-MM-DD or dmy/DD/MM/YYYY.
    ui_date_format: str = Field(default="ymd")

    # Scheduled audit materialisation window.
    # 0 means scheduled audits are created only on their scheduled begin date.
    # N means they may be created up to N calendar days before their begin date.
    scheduled_audit_create_days_ahead: int = Field(default=0)

    # -----------------------------------------------------------------------------
    # Authentication / sessions
    # -----------------------------------------------------------------------------
    # Bootstrap admin user (optional, for first-run only)
    # If set and no users exist, the API will create this initial admin user on startup.
    # REQUIRED in production: must be set via environment variable, no default value.
    bootstrap_admin_username: str = Field(default="")

    @field_validator("bootstrap_admin_username")
    @classmethod
    def validate_bootstrap_admin_username(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "KEEN_BOOTSTRAP_ADMIN_USERNAME must be set when bootstrapping admin user"
            )
        return v.strip()

    bootstrap_admin_password: str = Field(default="")

    @field_validator("bootstrap_admin_password")
    @classmethod
    def validate_bootstrap_admin_password(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "KEEN_BOOTSTRAP_ADMIN_PASSWORD must be set when bootstrapping admin user"
            )
        return v.strip()

    # Hosted owner-only v1. This is a security boundary, not UI configuration.
    demo_mode: bool = False
    demo_expires_at: datetime | None = None
    demo_consultation_url: str = "https://mig5.net/"

    @model_validator(mode="after")
    def validate_demo(self):
        if self.demo_mode:
            if not self.demo_expires_at or not self.demo_expires_at.tzinfo:
                raise ValueError("Demo mode requires a timezone-aware expiry")
            from urllib.parse import urlsplit
            u = urlsplit(self.demo_consultation_url)
            if u.scheme != "https" or not u.hostname or u.username:
                raise ValueError("Demo consultation link must be HTTPS")
        return self

    hosted_mode: bool = Field(default=False)
    hosted_owner_subject: str = Field(default="")

    @model_validator(mode="after")
    def validate_hosted_security(self):
        if self.hosted_mode:
            if not self.hosted_owner_subject or not self.oidc_issuer.startswith("https://"):
                raise ValueError("Hosted KEEN requires an exact OIDC issuer and owner subject")
            if (not self.oidc_enabled or self.local_auth_enabled or self.ldap_enabled or self.trust_remote_user
                    or self.google_sso_enabled or self.github_sso_enabled
                    or self.oidc_auto_link_existing or self.oidc_auto_provision):
                raise ValueError("Hosted KEEN requires OIDC only, with automatic account linking disabled")
            if (not self.cookie_secure or self.cookie_domain or self.oidc_allow_insecure_http
                    or not self.session_cookie_name.startswith("__Host-")):
                raise ValueError("Hosted KEEN requires secure __Host- cookies and HTTPS OIDC")
            from urllib.parse import urlsplit
            public = urlsplit(self.public_base_url)
            if (public.scheme != "https" or not public.hostname or public.username
                    or public.path not in ("", "/") or public.query or public.fragment):
                raise ValueError("Hosted KEEN requires an exact public HTTPS origin")
            if not self.s3_bucket or self.s3_access_key or self.s3_secret_key or self.s3_endpoint_url:
                raise ValueError("Hosted KEEN requires its own AWS bucket and no static S3 credentials")
            if self.artifact_storage_backend != "s3" or not self.s3_use_instance_role:
                raise ValueError("Hosted KEEN requires S3 with instance-role credentials")
        return self

    # Cookie-backed sessions stored in Valkey/Redis
    session_cookie_name: str = Field(default="keen_session")
    session_ttl_seconds: int = Field(default=60 * 60 * 24 * 7)  # 7 days

    # Cookie settings - secure by default for production
    cookie_secure: bool = Field(default=True)
    cookie_samesite: str = Field(default="strict")  # lax|strict|none
    cookie_domain: str = Field(default="")

    # Local username/password login. Disable this when native OIDC is the only
    # allowed interactive login path.
    local_auth_enabled: bool = Field(default=True)
    ldap_enabled: bool = False
    ldap_url: str = "ldaps://localhost:636"
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    # Search below this DN; set it to an OU DN to restrict access to that OU.
    ldap_base_dn: str = ""
    ldap_username_attribute: str = "uid"
    ldap_email_attribute: str = "mail"
    ldap_id_attribute: str = "entryUUID"
    ldap_user_filter: str = "(objectClass=person)"
    ldap_ca_cert_path: str = ""
    ldap_timeout_seconds: int = Field(default=5, ge=1, le=30)
    ldap_auto_provision: bool = True

    mfa_policy: str = Field(default="optional")
    mfa_encryption_key: str = Field(default="", repr=False)
    mfa_origin: str = Field(default="")
    mfa_rp_id: str = Field(default="")

    @field_validator("mfa_policy")
    @classmethod
    def validate_mfa_policy(cls, value):
        if value not in {"optional", "admins", "all"}:
            raise ValueError("MFA policy must be optional, admins or all")
        return value


    # Trusted proxy auth (REMOTE_USER)
    # When enabled, an upstream reverse proxy can authenticate a user and pass
    # their username via a header (default: REMOTE_USER). If present, this
    # identity overrides the cookie-backed session.
    #
    # IMPORTANT: Only enable this if you have a trusted proxy that strips any
    # client-supplied REMOTE_USER header and injects its own. Native OIDC ignores
    # REMOTE_USER even if this is accidentally enabled.
    trust_remote_user: bool = Field(default=False)
    remote_user_header_name: str = Field(default="REMOTE_USER")

    # Optional upstream logout URL (legacy external SSO proxy mode). Native OIDC
    # uses KEEN_OIDC_END_SESSION_ENDPOINT and the ID token stored in KEEN's own
    # session instead.
    upstream_logout_url: str = Field(default="")

    # -----------------------------------------------------------------------------
    # Native generic OpenID Connect relying party
    # -----------------------------------------------------------------------------
    oidc_enabled: bool = Field(default=False)
    oidc_provider_label: str = Field(default="Single sign-on")
    oidc_authorization_endpoint: str = Field(default="")
    oidc_token_endpoint: str = Field(default="")
    oidc_token_endpoint_auth_method: str = Field(default="client_secret_basic")
    oidc_jwks_uri: str = Field(default="")
    oidc_issuer: str = Field(default="")
    oidc_userinfo_endpoint: str = Field(default="")
    oidc_client_id: str = Field(default="")
    oidc_client_secret: str = Field(default="")
    oidc_scopes: str = Field(default="openid email profile")
    oidc_redirect_uri: str = Field(default="")
    oidc_end_session_endpoint: str = Field(default="")
    oidc_post_logout_redirect_uri: str = Field(default="")
    oidc_id_token_leeway_seconds: int = Field(default=60)
    oidc_username_claims: str = Field(default="preferred_username,email")
    oidc_allowed_email_domains: str = Field(default="")
    oidc_auto_link_existing: bool = Field(default=True)
    # Present for deployment parity with Statistics, but intentionally unsupported
    # by KEEN's OIDC resolver because KEEN must not auto-register users.
    oidc_auto_provision: bool = Field(default=False)
    oidc_allowed_algs: str = Field(default="RS256")
    oidc_allow_insecure_http: bool = Field(default=False)

    # Google OpenID Connect
    google_sso_enabled: bool = Field(default=False)
    google_provider_label: str = Field(default="Google")
    google_authorization_endpoint: str = Field(default="https://accounts.google.com/o/oauth2/v2/auth")
    google_token_endpoint: str = Field(default="https://oauth2.googleapis.com/token")
    google_jwks_uri: str = Field(default="https://www.googleapis.com/oauth2/v3/certs")
    google_issuer: str = Field(default="https://accounts.google.com,accounts.google.com")
    google_userinfo_endpoint: str = Field(default="https://openidconnect.googleapis.com/v1/userinfo")
    google_client_id: str = Field(default="")
    google_client_secret: str = Field(default="")
    google_scopes: str = Field(default="openid email profile")
    google_redirect_uri: str = Field(default="")
    google_end_session_endpoint: str = Field(default="")
    google_post_logout_redirect_uri: str = Field(default="")
    google_auto_provision: bool = Field(default=False)
    google_auto_link_existing: bool = Field(default=True)
    google_allowed_email_domains: str = Field(default="")
    google_username_claims: str = Field(default="email")

    # GitHub OAuth
    github_sso_enabled: bool = Field(default=False)
    github_provider_label: str = Field(default="GitHub")
    github_authorization_endpoint: str = Field(default="https://github.com/login/oauth/authorize")
    github_token_endpoint: str = Field(default="https://github.com/login/oauth/access_token")
    github_user_endpoint: str = Field(default="https://api.github.com/user")
    github_emails_endpoint: str = Field(default="https://api.github.com/user/emails")
    github_issuer: str = Field(default="https://github.com/login/oauth")
    github_client_id: str = Field(default="")
    github_client_secret: str = Field(default="")
    github_scopes: str = Field(default="read:user user:email")
    github_redirect_uri: str = Field(default="")
    github_auto_provision: bool = Field(default=False)
    github_auto_link_existing: bool = Field(default=True)
    github_allowed_email_domains: str = Field(default="")

    # CSRF (double-submit cookie)
    # - Token is stored in a non-HttpOnly cookie so the UI can echo it in a header.
    # - For unsafe methods, the API requires the header value to match the cookie.
    csrf_cookie_name: str = Field(default="keen_csrf")
    csrf_header_name: str = Field(default="X-CSRF-Token")
    csrf_cookie_samesite: str = Field(default="strict")  # lax|strict|none

    # -----------------------------------------------------------------------------
    # UI evidence sampling
    # -----------------------------------------------------------------------------
    # Used as a footer when printing "Sample as PDF evidence" from the event view.
    # Example: "Example Organisation, All rights reserved"
    sample_pdf_footer: str = Field(default="")

    # Optional header text shown when printing "Sample as PDF evidence" from the event view.
    # Example: "Organisation ISMS Evidence Pack"
    sample_pdf_header: str = Field(default="")

    # Optional prefix used when naming downloaded evidence files (PDF/ZIP).
    # Example: "Organisation"
    evidence_filename_prefix: str = Field(default="")

    # Controls optional IP/email masking in event data.
    # - false: no IP/email masking (default)
    # - samples: mask only in event/audit PDF and ZIP sample exports
    # - true: mask at event ingestion time before storage
    event_data_masking: str = Field(default="false")

    # Public base URL (optional)
    # Used when generating absolute links in outbound notifications (e.g. webhook
    # messages to Slack/Teams/Google Chat/email).
    # Example: https://keen.example.com
    public_base_url: str = Field(default="")

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, v: str) -> str:
        if not v or not v.strip():
            return ""
        v = v.strip().rstrip("/")
        if not v.startswith("https://") and not v.startswith("http://"):
            raise ValueError("public_base_url must start with http:// or https://")
        return v

    # -----------------------------------------------------------------------------
    # SMTP outbound mail
    # -----------------------------------------------------------------------------
    # Used for scheduled audit attendee notifications.
    security_trusted_proxy_cidrs: str = Field(default="")

    @field_validator("security_trusted_proxy_cidrs")
    @classmethod
    def validate_security_proxies(cls, value):
        import ipaddress
        for entry in value.split(','):
            if entry.strip():
                ipaddress.ip_network(entry.strip())
        return value

    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587)
    smtp_username: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_use_tls: bool = Field(default=True)  # STARTTLS
    smtp_use_ssl: bool = Field(default=False)  # SMTPS (465)
    smtp_timeout_seconds: int = Field(default=20)
    smtp_from_email: str = Field(default="")
    smtp_from_name: str = Field(default="Keen")

    # Database / broker
    database_url: str = Field(default="")
    redis_url: str = Field(default="redis://localhost:6379/0")

    # Short-lived cache for expensive aggregate/count endpoints used by the UI.
    # Set to 0 to disable. The data remains exact; it may simply be up to this
    # many seconds behind newly ingested events.
    aggregate_cache_ttl_seconds: int = Field(default=60)

    # Evidence storage. Existing s3:// objects remain readable after switching
    # the write backend, provided their S3 credentials remain configured.
    artifact_storage_backend: str = Field(default="auto")
    artifact_local_dir: str = Field(default="/app/data/artifacts")

    @field_validator("artifact_storage_backend")
    @classmethod
    def validate_artifact_storage_backend(cls, v: str) -> str:
        value = (v or "").strip().lower()
        if value not in {"auto", "local", "s3"}:
            raise ValueError("KEEN_ARTIFACT_STORAGE_BACKEND must be auto, local or s3")
        return value

    # S3 (MinIO / AWS S3 / etc.), required only for S3 writes/reads.
    s3_use_instance_role: bool = Field(default=False)
    s3_endpoint_url: str = Field(default="")
    s3_access_key: str = Field(default="")
    s3_secret_key: str = Field(default="")
    s3_bucket: str = Field(default="")

    s3_region: str = Field(default="us-east-1")
    s3_use_ssl: bool = Field(default=True)
    # auto: keep boto3 defaults for AWS endpoints, use compatibility mode for
    # S3-compatible/custom endpoints such as Linode Object Storage or MinIO.
    # aws: always use boto3's AWS S3 defaults.
    # s3_compatible: always use compatibility mode.
    s3_compatibility_mode: str = Field(default="auto")

    @field_validator("s3_compatibility_mode")
    @classmethod
    def validate_s3_compatibility_mode(cls, v: str) -> str:
        value = (v or "auto").strip().lower().replace("-", "_")
        allowed = {"auto", "aws", "s3_compatible"}
        if value not in allowed:
            allowed_values = ", ".join(sorted(allowed))
            raise ValueError(
                "KEEN_S3_COMPATIBILITY_MODE must be one of: " f"{allowed_values}"
            )
        return value

    # Loki
    loki_enabled: bool = Field(default=False)
    loki_base_url: str = Field(default="http://localhost:3100")
    loki_username: str = Field(default="")
    loki_password: str = Field(default="")
    loki_header_name: str = Field(default="")
    loki_header_value: str = Field(default="")
    # How far back to look when a Loki query cursor does not exist yet.
    loki_initial_lookback_minutes: int = Field(default=15)
    # Keep each Loki query_range request under the server-side query length cap.
    # 720h = 30d, which is safely below common Loki limits such as 30d1h.
    # Set to 0 to disable chunking.
    loki_max_query_range_hours: int = Field(default=720)
    # What to do when an existing Loki cursor is older than the max range:
    # - chunk: query bounded windows and advance the cursor over time
    # - reset: skip historical backfill and set the cursor to now
    loki_overlong_range_strategy: str = Field(default="chunk")
    # Maximum bounded query_range windows per query per ingestion run. Increase
    # temporarily to catch up faster after an outage.
    loki_catchup_chunks_per_run: int = Field(default=1)

    # --- AWS CloudWatch Logs (polling)
    cloudwatch_logs_enabled: bool = Field(default=False)
    # Optional default region (if blank, boto3 resolves via env/instance profile)
    cloudwatch_logs_region: str = Field(default="")
    cloudwatch_logs_config_path: str = Field(default="/app/config/cloudwatch_logs.yml")

    # GitHub (polling)
    github_enabled: bool = Field(default=False)
    github_base_url: str = Field(default="https://api.github.com")
    github_config_path: str = Field(default="/app/config/github.yml")
    github_token: str = Field(default="", validation_alias="GITHUB_TOKEN")
    # Optional username for Basic Auth (needed for some legacy endpoints like private Atom feeds)
    github_username: str = Field(default="", validation_alias="GITHUB_USERNAME")

    # Git service collection credentials remain in the server environment.
    gitea_enabled: bool = False
    gitea_base_url: str = ""
    gitea_config_path: str = "/app/config/gitea.yml"
    gitea_token: str = Field(default="", validation_alias="GITEA_TOKEN")
    gitea_username: str = Field(default="", validation_alias="GITEA_USERNAME")
    gitea_auth_mode: str = Field(default="token", validation_alias="GITEA_AUTH_MODE")
    gitea_cookie: str = Field(default="", validation_alias="GITEA_COOKIE")
    # Risk Ledger public alpha API (fixed HTTPS origin; one key/organisation).
    riskledger_enabled: bool = False
    riskledger_api_key: str = ""
    riskledger_config_path: str = "/app/config/riskledger.yml"
    riskledger_poll_seconds: int = Field(default=3600, ge=60, le=86400)

    redmine_enabled: bool = False
    redmine_base_url: str = ""
    redmine_api_key: str = ""
    redmine_config_path: str = "/app/config/redmine.yml"
    redmine_poll_seconds: int = Field(default=600, ge=60, le=86400)

    gitlab_enabled: bool = False
    gitlab_base_url: str = "https://gitlab.com"
    gitlab_config_path: str = "/app/config/gitlab.yml"
    gitlab_token: str = Field(default="", validation_alias="GITLAB_TOKEN")

    # Forgejo (polling)
    forgejo_enabled: bool = Field(default=False)
    # Optional default base URL, e.g. https://git.example.com
    forgejo_base_url: str = Field(default="")
    forgejo_config_path: str = Field(default="/app/config/forgejo.yml")
    forgejo_token: str = Field(default="", validation_alias="FORGEJO_TOKEN")
    forgejo_username: str = Field(default="", validation_alias="FORGEJO_USERNAME")
    # One of: token | bearer | basic | query | cookie | none
    forgejo_auth_mode: str = Field(
        default="token", validation_alias="FORGEJO_AUTH_MODE"
    )
    # If forgejo_auth_mode=cookie, set this to a raw Cookie header value
    forgejo_cookie: str = Field(default="", validation_alias="FORGEJO_COOKIE")

    # Jenkins (polling)
    jenkins_enabled: bool = Field(default=False)
    jenkins_base_url: str = Field(default="")
    jenkins_config_path: str = Field(default="/app/config/jenkins.yml")
    jenkins_username: str = Field(default="", validation_alias="JENKINS_USERNAME")
    jenkins_api_token: str = Field(default="", validation_alias="JENKINS_API_TOKEN")

    # Taiga (polling)
    taiga_enabled: bool = Field(default=False)
    taiga_base_url: str = Field(default="")
    taiga_config_path: str = Field(default="/app/config/taiga.yml")
    taiga_token: str = Field(default="", validation_alias="TAIGA_TOKEN")

    # --- BookStack (polling)
    bookstack_enabled: bool = Field(default=False)
    bookstack_base_url: str = Field(default="")
    bookstack_config_path: str = Field(default="/app/config/bookstack.yml")
    bookstack_token_id: str = Field(default="", validation_alias="BOOKSTACK_TOKEN_ID")
    bookstack_token_secret: str = Field(
        default="", validation_alias="BOOKSTACK_TOKEN_SECRET"
    )
    taiga_username: str = Field(default="")
    taiga_password: str = Field(default="")

    # --- RSS / Atom (polling)
    # Note: TLS verification is ALWAYS enforced for RSS feeds (verify=True in all clients).
    # The rss_verify_tls field is retained for backwards compatibility but ignored.
    rss_enabled: bool = Field(default=False)
    rss_config_path: str = Field(default="/app/config/rss.yml")
    rss_verify_tls: bool = Field(default=True)  # Deprecated: always True
    rss_user_agent: str = Field(default="Keen RSS ingester")

    # --- Google Workspace (Admin SDK Reports API)
    google_workspace_enabled: bool = Field(default=False)
    google_workspace_config_path: str = Field(
        default="/app/config/google_workspace.yml"
    )

    # Credentials
    # - Recommended: service account JSON (either raw JSON or base64)
    # - Alternative: a path inside the container
    # Domain-wide delegation is expected; set impersonation to an admin email.
    google_workspace_impersonate: str = Field(
        default="", validation_alias="GOOGLE_WORKSPACE_IMPERSONATE"
    )
    google_workspace_sa_keyfile: str = Field(
        default="", validation_alias="GOOGLE_WORKSPACE_SA_KEYFILE"
    )
    google_workspace_sa_json: str = Field(
        default="", validation_alias="GOOGLE_WORKSPACE_SA_JSON"
    )
    google_workspace_sa_json_b64: str = Field(
        default="", validation_alias="GOOGLE_WORKSPACE_SA_JSON_B64"
    )

    # Config paths inside container
    enabled_frameworks: str = ""  # Comma-separated organisation catalogue restriction; empty means all.
    rules_path: str = Field(default="/app/config/rules.yml")
    control_links_path: str = Field(default="/app/config/control_links.yml")
    risk_mitigator_rules_path: str = Field(default="/app/config/risk_mitigator.yml")
    default_framework_slug: str = Field(
        default="iso_27001_2022",
        validation_alias=AliasChoices(
            "DEFAULT_FRAMEWORK_SLUG",
            "DEFAULT_FRAMEWORK",
            "KEEN_DEFAULT_FRAMEWORK_SLUG",
            "KEEN_DEFAULT_FRAMEWORK",
        ),
    )
    loki_queries_path: str = Field(default="/app/config/loki.yml")

    # -----------------------------------------------------------------------------
    # Webhooks
    # -----------------------------------------------------------------------------
    webhooks_path: str = Field(default="/app/config/webhooks.yml")
    # If true (recommended), webhook providers MUST be configured with a secret
    # header + an environment variable containing the expected secret.
    # When false, providers without a secret configuration are accepted.
    webhooks_require_secret: bool = Field(default=True)

    # -----------------------------------------------------------------------------
    # Ingestion SSRF protection
    # -----------------------------------------------------------------------------
    # Comma-separated list of CIDR blocks allowed for ingestion plugin outbound
    # connections (Jenkins, Loki, Taiga, Bookstack, etc.).
    # Empty means only loopback and link-local are blocked (permissive default).
    # Example: "10.42.0.0/16,172.16.0.0/12"
    ingestion_allowed_cidrs: str = Field(default="")

    # -----------------------------------------------------------------------------
    # Outbound question notifications (webhooks)
    # -----------------------------------------------------------------------------
    # Comma / whitespace separated lists of webhook URLs.
    # When set, Keen will POST a message when a user asks a question on an event.
    question_webhook_slack_urls: str = Field(default="")
    question_webhook_teams_urls: str = Field(default="")
    question_webhook_google_chat_urls: str = Field(default="")
    question_webhook_generic_urls: str = Field(default="")

    # Reply notifications (when someone replies on an existing question thread).
    # If the *_reply_* URL lists are empty and use_question_urls_if_reply_empty=true,
    # Keen will fall back to the corresponding KEEN_QUESTION_WEBHOOK_* URLs.
    question_reply_webhook_slack_urls: str = Field(default="")
    question_reply_webhook_teams_urls: str = Field(default="")
    question_reply_webhook_google_chat_urls: str = Field(default="")
    question_reply_webhook_generic_urls: str = Field(default="")
    question_reply_webhook_use_question_urls_if_empty: bool = Field(default=True)

    # Optional extra headers (JSON object) added to the *generic* webhook.
    # Example: {"Authorization": "Bearer ...", "X-Tenant": "acme"}
    question_webhook_generic_headers_json: str = Field(default="")

    # Optional signing secret for the generic webhook; when set, Keen will add a
    # signature header (default: X-Keen-Signature) with value:
    #   sha256=<hex hmac>
    question_webhook_signing_secret: str = Field(default="")
    question_webhook_signature_header: str = Field(default="X-Keen-Signature")

    # HTTP timeout for outbound webhook requests.
    question_webhook_timeout_seconds: float = Field(default=6.0)

    # -----------------------------------------------------------------------------
    # Outbound incident creation webhook
    # -----------------------------------------------------------------------------
    # When set, event pages may show a "Create Incident" button to users with
    # incident.create permission. Keen POSTs a canonical JSON payload to this URL
    # and records a local reference on the event after the webhook accepts it.
    incident_webhook_url: str = Field(default="")
    # Optional extra headers (JSON object) added to the incident webhook request.
    # Example: {"Authorization": "Bearer ...", "X-Tenant": "acme"}
    incident_webhook_headers_json: str = Field(default="")
    # Optional signing secret. When set, Keen sends an HMAC SHA-256 signature of
    # the canonical JSON body in the configured header.
    incident_webhook_signing_secret: str = Field(default="")
    incident_webhook_signature_header: str = Field(default="X-Keen-Signature")
    incident_webhook_timeout_seconds: float = Field(default=6.0)

    # -----------------------------------------------------------------------------
    # Login rate limiting (Valkey/Redis)
    # -----------------------------------------------------------------------------
    # Simple fixed-window rate limiting applied to /v1/auth/login.
    # These are defense-in-depth; a reverse proxy should still enforce limits.
    login_rate_limit_window_seconds: int = Field(default=300)  # 5 minutes
    login_rate_limit_per_ip: int = Field(default=30)
    login_rate_limit_per_username: int = Field(default=15)

    @field_validator("event_data_masking")
    @classmethod
    def validate_event_data_masking(cls, value: str) -> str:
        mode = str(value or "false").strip().lower()
        if mode not in {"false", "true", "samples"}:
            raise ValueError("KEEN_EVENT_DATA_MASKING must be false, true, or samples")
        return mode

    @field_validator("scheduled_audit_create_days_ahead")
    @classmethod
    def validate_scheduled_audit_create_days_ahead(cls, value: int) -> int:
        try:
            days = int(value or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "KEEN_SCHEDULED_AUDIT_CREATE_DAYS_AHEAD must be a non-negative integer"
            ) from exc
        if days < 0:
            raise ValueError(
                "KEEN_SCHEDULED_AUDIT_CREATE_DAYS_AHEAD must be a non-negative integer"
            )
        return days


settings = Settings()
