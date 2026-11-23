from __future__ import annotations

from datetime import datetime

from mosp.config import MospSettings
from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import SettingsConfigDict


class Settings(MospSettings):
    model_config = SettingsConfigDict(env_prefix="KEEN_", case_sensitive=False)

    # KEEN's names for the settings mosp defines (mosp.config.MospSettings).
    session_cookie_name: str = Field(default="keen_session")
    csrf_cookie_name: str = Field(default="keen_csrf")
    smtp_from_name: str = Field(default="Keen")

    timezone: str = Field(default="Etc/UTC")
    # Visible date input format used by MOSP shared date widgets. API values remain YYYY-MM-DD.
    # Allowed: ymd/iso/YYYY-MM-DD or dmy/DD/MM/YYYY.
    ui_date_format: str = Field(default="ymd")

    # Scheduled audit materialisation window.
    # 0 means scheduled audits are created only on their scheduled begin date.
    # N means they may be created up to N calendar days before their begin date.
    scheduled_audit_create_days_ahead: int = Field(default=0)
    # Days before an audit starts, or a quiz round falls due, that Keen emails
    # attendees and unfinished participants a reminder. 0 sends no reminders.
    reminder_days_before: int = Field(default=2)

    # -----------------------------------------------------------------------------
    # Authentication / sessions
    # -----------------------------------------------------------------------------
    # Hosted owner-only v1. This is a security boundary, not UI configuration.
    demo_mode: bool = False
    demo_expires_at: datetime | None = None
    demo_consultation_url: str = "https://mig5.net/"

    def check_application(self) -> None:
        # KEEN's demo and hosted profiles explain a refused configuration
        # better than mosp's own checks, so they run first.
        self.validate_demo()
        self.validate_hosted_security()
        super().check_application()

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

    def validate_hosted_security(self):
        if self.hosted_mode:
            if not self.hosted_owner_subject or not self.oidc_issuer.startswith(
                "https://"
            ):
                raise ValueError(
                    "Hosted Keen requires an exact OIDC issuer and owner subject"
                )
            if (
                not self.oidc_enabled
                or self.local_auth_enabled
                or self.ldap_enabled
                or self.trust_remote_user
                or self.google_sso_enabled
                or self.github_sso_enabled
                or self.oidc_auto_link_existing
                or self.oidc_auto_provision
            ):
                raise ValueError(
                    "Hosted Keen requires OIDC only, with automatic account linking disabled"
                )
            if (
                not self.cookie_secure
                or self.cookie_domain
                or self.oidc_allow_insecure_http
                or not self.session_cookie_name.startswith("__Host-")
            ):
                raise ValueError(
                    "Hosted Keen requires secure __Host- cookies and HTTPS OIDC"
                )
            # The field validator has already reduced the URL to a bare origin.
            if not self.public_base_url.startswith("https://"):
                raise ValueError("Hosted Keen requires an exact public HTTPS origin")
            if (
                not self.s3_bucket
                or self.s3_access_key
                or self.s3_secret_key
                or self.s3_endpoint_url
            ):
                raise ValueError(
                    "Hosted Keen requires its own AWS bucket and no static S3 credentials"
                )
            if self.artifact_storage_backend != "s3" or not self.s3_use_instance_role:
                raise ValueError(
                    "Hosted Keen requires S3 with instance-role credentials"
                )
        return self

    # Optional sign-out address of the trusted proxy that authenticates users.
    # Native OIDC uses KEEN_OIDC_END_SESSION_ENDPOINT and the ID token stored in
    # Keen's own session instead.
    upstream_logout_url: str = Field(default="")

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

    # -----------------------------------------------------------------------------

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
                f"KEEN_S3_COMPATIBILITY_MODE must be one of: {allowed_values}"
            )
        return value

    # Global receiving gate for all webhook connections.
    webhooks_enabled: bool = Field(default=True)

    # AI agent access through the Model Context Protocol. Off unless enabled:
    # the MCP endpoint, its OAuth endpoints and the consent page answer 404, and
    # access tokens issued while it was on stop working.
    mcp_enabled: bool = Field(default=False)
    # Whether connections may also add and update records, within scopes each
    # person approves and never beyond their own permissions. Off unless set,
    # and ignored while MCP is off: consent then offers read scopes only, the
    # change tools are not listed, and grants keep their change scopes unused.
    mcp_write_enabled: bool = Field(default=False)
    # Redirect URIs an MCP client may register, besides loopback http
    # callbacks (http://127.0.0.1, http://[::1] and http://localhost on any
    # port), which local clients such as Claude Code and Claude Desktop use.
    # Comma-separated exact URIs; empty admits local clients only. claude.ai's
    # connectors return to https://claude.ai/api/mcp/auth_callback.
    mcp_redirect_uris: str = Field(default="")

    @field_validator("mcp_redirect_uris")
    @classmethod
    def validate_mcp_redirect_uris(cls, v: str) -> str:
        from app.security.mcp_tokens import configured_redirect_problem

        entries = [entry.strip() for entry in (v or "").split(",") if entry.strip()]
        for entry in entries:
            if problem := configured_redirect_problem(entry):
                raise ValueError(f"KEEN_MCP_REDIRECT_URIS: {problem}")
        return ",".join(entries)

    @model_validator(mode="after")
    def validate_mcp_origin(self):
        from urllib.parse import urlsplit

        # OAuth requires HTTPS for an authorisation server's issuer (RFC 8414),
        # except on this device for local testing.
        if self.mcp_enabled and not (
            self.public_base_url.startswith("https://")
            or urlsplit(self.public_base_url).hostname
            in ("localhost", "127.0.0.1", "::1")
        ):
            raise ValueError(
                "KEEN_MCP_ENABLED requires an https:// KEEN_PUBLIC_BASE_URL"
            )
        return self

    # Loki
    elasticsearch_enabled: bool = Field(default=False)
    elasticsearch_base_url: str = Field(default="")
    elasticsearch_username: str = Field(default="")
    elasticsearch_password: str = Field(default="")
    elasticsearch_api_key: str = Field(default="")
    elasticsearch_config_path: str = Field(default="/app/config/elasticsearch.yml")

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

    # --- Collection run time
    # Each source type's scheduled run ends within its own budget, set below the
    # interval between its runs; a source whose legitimate runs take longer,
    # such as Loki during a large log spike, can be given more. Every budget is
    # capped by collection_run_max_seconds. A Risk Ledger run needs its
    # longest per-connection max_run_seconds (30 minutes) and one request more.
    collection_run_max_seconds: int = Field(default=3300, ge=300, le=86400)
    elasticsearch_run_seconds: int = Field(default=1500, ge=300, le=86400)
    loki_run_seconds: int = Field(default=1500, ge=300, le=86400)
    cloudwatch_logs_run_seconds: int = Field(default=1500, ge=300, le=86400)
    google_workspace_run_seconds: int = Field(default=1500, ge=300, le=86400)
    github_run_seconds: int = Field(default=1200, ge=300, le=86400)
    forgejo_run_seconds: int = Field(default=1200, ge=300, le=86400)
    gitea_run_seconds: int = Field(default=1200, ge=300, le=86400)
    gitlab_run_seconds: int = Field(default=1200, ge=300, le=86400)
    jenkins_run_seconds: int = Field(default=1200, ge=300, le=86400)
    taiga_run_seconds: int = Field(default=900, ge=300, le=86400)
    bookstack_run_seconds: int = Field(default=1800, ge=300, le=86400)
    redmine_run_seconds: int = Field(default=480, ge=300, le=86400)
    op_events_run_seconds: int = Field(default=600, ge=300, le=86400)
    bitwarden_run_seconds: int = Field(default=600, ge=300, le=86400)
    mosyle_run_seconds: int = Field(default=600, ge=300, le=86400)
    cloudflare_run_seconds: int = Field(default=600, ge=300, le=86400)
    riskledger_run_seconds: int = Field(default=2100, ge=1980, le=86400)
    rss_run_seconds: int = Field(default=600, ge=300, le=86400)

    # --- Cloud vendor posture, shown on each cloud Vendor's page
    # Every enabled provider's settings checks run on one schedule and its
    # activity reads on another.
    cloud_posture_poll_seconds: int = Field(default=43200, ge=3600, le=86400)
    cloud_activity_poll_seconds: int = Field(default=1800, ge=300, le=86400)
    # Users unused for, and access keys older than, these many days need
    # attention.
    cloud_inactive_days: int = Field(default=45, ge=1, le=3650)
    cloud_key_age_days: int = Field(default=90, ge=1, le=3650)

    # AWS: one account per role ARN, or the credentials' own account. The
    # _POSTURE_ names never collide with AWS_ACCESS_KEY_ID, which boto3 reads
    # implicitly for CloudWatch. Blank keys use the host's role chain.
    aws_enabled: bool = Field(default=False)
    aws_region: str = Field(default="us-east-1")
    aws_role_arns: str = Field(default="")
    # Regions the network checks read; blank reads every enabled region.
    aws_regions: str = Field(default="")
    aws_access_key_id: str = Field(
        default="", validation_alias="AWS_POSTURE_ACCESS_KEY_ID"
    )
    aws_secret_access_key: str = Field(
        default="", validation_alias="AWS_POSTURE_SECRET_ACCESS_KEY", repr=False
    )
    aws_session_token: str = Field(
        default="", validation_alias="AWS_POSTURE_SESSION_TOKEN", repr=False
    )
    aws_external_id: str = Field(default="", validation_alias="AWS_POSTURE_EXTERNAL_ID")

    # Microsoft Entra ID and Azure: one tenant, read as an app registration.
    # The _POSTURE_ names keep Azure SDKs, which read AZURE_CLIENT_SECRET
    # implicitly, from using them.
    azure_enabled: bool = Field(default=False)
    azure_tenant_id: str = Field(default="", validation_alias="AZURE_POSTURE_TENANT_ID")
    azure_client_id: str = Field(default="", validation_alias="AZURE_POSTURE_CLIENT_ID")
    azure_client_secret: str = Field(
        default="", validation_alias="AZURE_POSTURE_CLIENT_SECRET", repr=False
    )
    # Subscription IDs to read; blank reads every one the app can see.
    azure_subscriptions: str = Field(default="")

    # Google Cloud: one organisation, or one project without an organisation,
    # read as a service account.
    google_cloud_enabled: bool = Field(default=False)
    google_cloud_organization: str = Field(default="")
    google_cloud_project: str = Field(default="")
    # Projects whose Admin Activity logs are read beside the organisation's.
    google_cloud_projects: str = Field(default="")
    google_cloud_sa_keyfile: str = Field(
        default="", validation_alias="GOOGLE_CLOUD_SA_KEYFILE"
    )
    google_cloud_sa_json: str = Field(
        default="", validation_alias="GOOGLE_CLOUD_SA_JSON", repr=False
    )
    google_cloud_sa_json_b64: str = Field(
        default="", validation_alias="GOOGLE_CLOUD_SA_JSON_B64", repr=False
    )

    # Akamai Cloud (Linode): the account a read-only personal access token
    # belongs to.
    linode_enabled: bool = Field(default=False)
    linode_token: str = Field(default="", validation_alias="LINODE_TOKEN", repr=False)

    # DigitalOcean: the team a custom-scoped, read-only token belongs to.
    digitalocean_enabled: bool = Field(default=False)
    digitalocean_token: str = Field(
        default="", validation_alias="DIGITALOCEAN_TOKEN", repr=False
    )

    # Scaleway: one Organization, read with an API key whose policy grants
    # read-only permission sets.
    scaleway_enabled: bool = Field(default=False)
    scaleway_organization_id: str = Field(default="")
    scaleway_secret_key: str = Field(
        default="", validation_alias="SCALEWAY_SECRET_KEY", repr=False
    )
    # Zones whose Instance security groups are read; Managed Databases and
    # Kubernetes are read in their regions. Blank reads every zone in Paris,
    # Amsterdam and Warsaw.
    scaleway_zones: str = Field(default="")

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

    # 1Password Events API (one of 1Password's own servers; bearer token).
    op_events_enabled: bool = False
    op_events_base_url: str = "https://events.1password.com"
    op_events_token: str = ""
    op_events_config_path: str = "/app/config/op_events.yml"
    op_events_poll_seconds: int = Field(default=900, ge=60, le=86400)

    # Bitwarden Public API (organisation API key; cloud or self-hosted).
    bitwarden_enabled: bool = False
    bitwarden_base_url: str = "https://api.bitwarden.com"
    bitwarden_identity_url: str = "https://identity.bitwarden.com"
    bitwarden_client_id: str = ""
    bitwarden_client_secret: str = ""
    bitwarden_config_path: str = "/app/config/bitwarden.yml"
    bitwarden_poll_seconds: int = Field(default=900, ge=60, le=86400)

    # Mosyle Business administrator action logs, read from the Mosyle API as
    # one administrator.
    mosyle_enabled: bool = False
    mosyle_access_token: str = ""
    mosyle_email: str = ""
    mosyle_password: str = ""
    mosyle_config_path: str = "/app/config/mosyle.yml"
    mosyle_poll_seconds: int = Field(default=900, ge=60, le=86400)

    # Cloudflare account audit logs, read with an API token.
    cloudflare_enabled: bool = False
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_config_path: str = "/app/config/cloudflare.yml"
    cloudflare_poll_seconds: int = Field(default=900, ge=60, le=86400)

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

    # --- RSS / Atom (polling); feed requests always verify TLS.
    imap_enabled: bool = Field(default=False)
    rss_enabled: bool = Field(default=False)
    rss_config_path: str = Field(default="/app/config/rss.yml")
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
    # Workspace posture uses the same service account and delegated
    # administrator, with the read-only Directory scope.
    google_workspace_posture_enabled: bool = Field(default=False)

    # Config paths inside container
    enabled_frameworks: str = (
        ""  # Comma-separated organisation catalogue restriction; empty means all.
    )
    rules_path: str = Field(default="/app/config/rules.yml")
    control_links_path: str = Field(default="/app/config/control_links.yml")
    risk_mitigator_rules_path: str = Field(default="/app/config/risk_mitigator.yml")
    default_framework_slug: str = Field(
        default="KEEN-AF:1.0",
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
    # Every provider needs a secret header and the environment variable holding
    # its secret; a provider without them is refused. No setting changes that.

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

    # Optional signing secret for the generic webhook. When set, each delivery
    # carries X-Keen-Timestamp (Unix seconds) and X-Keen-Signature:
    #   v1=<hex HMAC-SHA256 of "<timestamp>.<body>">
    question_webhook_signing_secret: str = Field(default="")

    # HTTP timeout for outbound webhook requests.
    question_webhook_timeout_seconds: float = Field(default=6.0)

    # -----------------------------------------------------------------------------
    # Outbound incident creation webhook
    # -----------------------------------------------------------------------------
    # When set, event pages may show a "Create incident" button to users with
    # incident.create permission. Keen POSTs a canonical JSON payload to this URL
    # and records a local reference on the event after the webhook accepts it.
    incident_webhook_url: str = Field(default="")
    # Optional extra headers (JSON object) added to the incident webhook request.
    # Example: {"Authorization": "Bearer ...", "X-Tenant": "acme"}
    incident_webhook_headers_json: str = Field(default="")
    # Optional signing secret, signing deliveries as question webhooks do.
    incident_webhook_signing_secret: str = Field(default="")
    incident_webhook_timeout_seconds: float = Field(default=6.0)

    # Current-password checks for password changes, per signed-in account.
    password_change_rate_limit_per_account: int = Field(default=15)

    @field_validator("event_data_masking")
    @classmethod
    def validate_event_data_masking(cls, value: str) -> str:
        mode = str(value or "false").strip().lower()
        if mode not in {"false", "true", "samples"}:
            raise ValueError("KEEN_EVENT_DATA_MASKING must be false, true, or samples")
        return mode

    @field_validator("reminder_days_before")
    @classmethod
    def validate_reminder_days_before(cls, value: int) -> int:
        # Runs after pydantic has parsed the value as an integer.
        if value < 0 or value > 90:
            raise ValueError(
                "KEEN_REMINDER_DAYS_BEFORE must be an integer from 0 to 90"
            )
        return value

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
