"""Fictional cloud posture for demonstration environments.

Demo mode never calls a vendor. Each provider answers with the same small,
fictional organisation: a tidy production scope and a staging scope that
needs some attention. Activity entries are generated per hour, with stable
IDs, so repeated reads record each one once.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.datetime_utils import utc_now_naive
from app.ingest.cloud import provider as cloud_provider
from app.ingest.cloud.base import Activity, Check, Identity, ScopeResult

DAY = timedelta(days=1)


def _ago(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


def _scope(
    key, label, checks: dict, identities, provider_checks, kind=""
) -> ScopeResult:
    rows = []
    for check, (status, detail, *subjects) in checks.items():
        rows.append(
            Check(
                check,
                provider_checks[check],
                status,
                detail,
                list(subjects[0]) if subjects else [],
                reason="not_licensed" if status == "unknown" else "",
            )
        )
    return ScopeResult(key, label, rows, identities, kind=kind)


def _aws(now: datetime) -> list[ScopeResult]:
    checks = cloud_provider("aws").checks
    people = [
        Identity("root", kind="root", admin=True, mfa=True, last_used=_ago(now, 120)),
        Identity(
            "alex.morgan@example.invalid",
            "alex.morgan@example.invalid",
            admin=True,
            mfa=True,
            last_used=_ago(now, 1),
            detail="Console",
        ),
        Identity(
            "sam.lee@example.invalid",
            "sam.lee@example.invalid",
            mfa=True,
            last_used=_ago(now, 3),
            detail="Console",
        ),
        Identity(
            "deploy-pipeline",
            kind="service",
            last_used=_ago(now, 0),
            detail="1 access key",
        ),
    ]
    staging = [
        Identity("root", kind="root", admin=True, mfa=False, last_used=_ago(now, 400)),
        Identity(
            "jordan.patel@example.invalid",
            "jordan.patel@example.invalid",
            admin=True,
            mfa=False,
            last_used=_ago(now, 2),
            detail="Console and 1 access key",
        ),
        Identity(
            "contractor-2023",
            mfa=False,
            last_used=_ago(now, 210),
            detail="Console",
        ),
        Identity(
            "reporting-export",
            kind="service",
            last_used=_ago(now, 9),
            detail="1 access key",
        ),
    ]
    return [
        _scope(
            "111122223333",
            "Production",
            {
                "identity.mfa": ("met", "MFA on for 2 of 2 console users"),
                "identity.admin_mfa": ("met", "MFA on for 1 of 1 administrator"),
                "identity.admins": (
                    "info",
                    "1 user and 1 role hold administrator rights",
                    ["alex.morgan@example.invalid", "role/BreakGlass"],
                ),
                "identity.inactive": ("met", "0 of 3 users unused for 45 days or more"),
                "credentials.key_age": (
                    "met",
                    "0 of 1 active access key older than 90 days",
                ),
                "aws.root": ("met", "Root user has MFA and no access keys"),
                "aws.password_policy": (
                    "met",
                    "Minimum length 14, last 24 passwords cannot be reused",
                ),
                "logging.audit_trail": (
                    "met",
                    "A multi-region trail is logging (organisation trail, log file "
                    "validation on)",
                ),
                "storage.public_access": (
                    "met",
                    "All four settings are on for the account",
                ),
                "network.admin_ports": (
                    "met",
                    "0 of 6 security groups in use admit SSH or RDP from anywhere",
                ),
                "network.databases": (
                    "met",
                    "0 of 2 RDS instances accept connections from anywhere",
                ),
                "findings": ("attention", "0 critical and 1 high findings"),
            },
            people,
            checks,
        ),
        _scope(
            "444455556666",
            "Staging",
            {
                "identity.mfa": (
                    "attention",
                    "MFA off for 2 of 2 console users",
                    ["contractor-2023", "jordan.patel@example.invalid"],
                ),
                "identity.admin_mfa": (
                    "attention",
                    "MFA off for 1 of 1 administrator",
                    ["jordan.patel@example.invalid"],
                ),
                "identity.admins": (
                    "info",
                    "1 user and 0 roles hold administrator rights",
                    ["jordan.patel@example.invalid"],
                ),
                "identity.inactive": (
                    "attention",
                    "1 of 3 users unused for 45 days or more",
                    ["contractor-2023"],
                ),
                "credentials.key_age": (
                    "attention",
                    "1 of 2 active access keys older than 90 days",
                    ["reporting-export"],
                ),
                "aws.root": ("attention", "Root user: MFA is off"),
                "aws.password_policy": ("attention", "No password policy is set"),
                "logging.audit_trail": (
                    "met",
                    "A multi-region trail is logging (organisation trail)",
                ),
                "storage.public_access": ("attention", "Off: restrict public buckets"),
                "network.admin_ports": (
                    "attention",
                    "1 of 4 security groups in use admit SSH or RDP from anywhere",
                    ["ap-southeast-2/sg-0bastion"],
                ),
                "network.databases": (
                    "attention",
                    "1 of 2 RDS instances accept connections from anywhere",
                    ["ap-southeast-2/reporting"],
                ),
                "findings": ("attention", "1 critical and 2 high findings"),
            },
            staging,
            checks,
        ),
    ]


ALEX = "alex.morgan@example.invalid"
SAM = "sam.lee@example.invalid"
JORDAN = "jordan.patel@example.invalid"
CONTRACTOR = "chris.contractor@partner.example.invalid"


def _azure(now: datetime) -> list[ScopeResult]:
    checks = cloud_provider("azure").checks
    people = [
        Identity(ALEX, ALEX, admin=True, mfa=True, last_used=_ago(now, 0)),
        Identity(SAM, SAM, mfa=True, last_used=_ago(now, 2)),
        Identity(JORDAN, JORDAN, mfa=False, last_used=_ago(now, 1)),
        Identity(
            CONTRACTOR, CONTRACTOR, kind="guest", mfa=None, last_used=_ago(now, 80)
        ),
    ]
    tenant = _scope(
        "7f3c2a1e-4b5d-4c6e-8f70-91a2b3c4d5e6",
        "Entra tenant",
        {
            "identity.mfa": ("attention", "MFA off for 1 of 3 users", [JORDAN]),
            "identity.admin_mfa": ("met", "MFA on for 1 of 1 administrator"),
            "identity.mfa_enforced": (
                "attention",
                "MFA is required by 1 Conditional Access policy; 1 user excluded from all of them",
                ["breakglass@example.invalid"],
            ),
            "identity.admins": (
                "info",
                "2 users, groups or apps hold privileged Entra roles",
                [ALEX, "Deployment pipeline"],
            ),
            "identity.guests": ("info", "1 guest, 0 yet to accept their invitation"),
            "identity.inactive": (
                "attention",
                "1 of 4 users unused for 45 days or more",
                [CONTRACTOR],
            ),
            "credentials.key_age": (
                "attention",
                "1 of 3 live secrets and certificates older than 90 days; 1 expire within 30 days",
                ["Payroll export"],
            ),
        },
        people,
        checks,
        kind="tenant",
    )

    def subscription(key, label, tidy):
        return _scope(
            key,
            label,
            {
                "identity.admins": (
                    "info",
                    "2 principals hold Owner or User Access Administrator",
                    [ALEX, "Platform team"],
                ),
                "logging.audit_trail": (
                    ("met", "The activity log is exported to 1 destination")
                    if tidy
                    else ("attention", "The activity log is not exported")
                ),
                "storage.public_access": (
                    ("met", "0 of 4 storage accounts allow public blob access")
                    if tidy
                    else (
                        "attention",
                        "1 of 3 storage accounts allow public blob access",
                        ["devassets"],
                    )
                ),
                "network.admin_ports": (
                    (
                        "met",
                        "0 of 5 network security groups in use admit SSH or RDP from anywhere",
                    )
                    if tidy
                    else (
                        "attention",
                        "1 of 2 network security groups in use admit SSH or RDP from anywhere",
                        ["nsg-jumpbox"],
                    )
                ),
                "network.databases": (
                    "met",
                    "0 of 2 database servers admit every address",
                ),
                "findings": (
                    (
                        "met",
                        "0 high, 4 medium and 9 low recommendations; secure score 81%",
                    )
                    if tidy
                    else (
                        "attention",
                        "2 high, 6 medium and 11 low recommendations; secure score 58%",
                    )
                ),
            },
            [],
            checks,
            kind="subscription",
        )

    return [
        tenant,
        subscription("1a2b3c4d-1111-4222-8333-444455556666", "Production", True),
        subscription("5e6f7a8b-2222-4333-8444-555566667777", "Development", False),
    ]


def _google_cloud(now: datetime) -> list[ScopeResult]:
    checks = cloud_provider("google_cloud").checks
    people = [
        Identity(ALEX, ALEX, admin=True, detail="Owner, Organization Administrator"),
        Identity(
            "platform-admins@example.invalid", kind="group", admin=True, detail="Owner"
        ),
        Identity(
            "terraform@acme-ops.iam.gserviceaccount.com",
            kind="service",
            admin=True,
            detail="Owner",
        ),
    ]
    return [
        _scope(
            "123456789012",
            "example.invalid",
            {
                "identity.admins": (
                    "info",
                    "3 users, groups or service accounts hold Owner or Organization Administrator",
                    [p.name for p in people],
                ),
                "credentials.key_age": (
                    "attention",
                    "1 service account with keys older than 90 days, of 3 live keys",
                    ["reporting@acme-data.iam.gserviceaccount.com"],
                ),
                "google_cloud.key_creation": (
                    "attention",
                    "Service account keys can be created",
                ),
                "google_cloud.domain_sharing": (
                    "met",
                    "IAM members are restricted to 1 allowed customer or domain",
                ),
                "storage.public_access": (
                    "met",
                    "Public access prevention is enforced",
                ),
                "logging.audit_trail": (
                    "attention",
                    "Data Access audit logs are not on for all services",
                ),
                "network.admin_ports": (
                    "attention",
                    "1 of 3 VPC networks let SSH or RDP in from anywhere",
                    ["legacy-vpc"],
                ),
                "network.databases": (
                    "met",
                    "0 of 2 Cloud SQL instances authorise every address",
                ),
                "findings": ("attention", "0 critical and 2 high findings"),
            },
            people,
            checks,
            kind="organisation",
        )
    ]


def _google_workspace(now: datetime) -> list[ScopeResult]:
    checks = cloud_provider("google_workspace").checks
    people = [
        Identity(
            ALEX,
            ALEX,
            admin=True,
            mfa=True,
            last_used=_ago(now, 0),
            detail="Super administrator",
        ),
        Identity(SAM, SAM, mfa=True, last_used=_ago(now, 1)),
        Identity(JORDAN, JORDAN, mfa=False, last_used=_ago(now, 3)),
        Identity(
            "former.staff@example.invalid",
            "former.staff@example.invalid",
            mfa=False,
            last_used=_ago(now, 120),
        ),
    ]
    return [
        _scope(
            "C01demo23",
            "example.invalid",
            {
                "identity.mfa": (
                    "attention",
                    "MFA off for 2 of 4 users",
                    [JORDAN, "former.staff@example.invalid"],
                ),
                "identity.mfa_enforced": (
                    "attention",
                    "Not enforced for 2 of 4 users",
                    [JORDAN, "former.staff@example.invalid"],
                ),
                "identity.admin_mfa": ("met", "MFA on for 1 of 1 administrator"),
                "identity.admins": (
                    "info",
                    "1 super or delegated administrator",
                    [ALEX],
                ),
                "identity.inactive": (
                    "attention",
                    "1 of 4 users unused for 45 days or more",
                    ["former.staff@example.invalid"],
                ),
            },
            people,
            checks,
            kind="customer",
        )
    ]


def _linode(now: datetime) -> list[ScopeResult]:
    checks = cloud_provider("linode").checks
    people = [
        Identity(
            "alexmorgan",
            ALEX,
            admin=True,
            mfa=True,
            last_used=_ago(now, 1),
            detail="Unrestricted",
        ),
        Identity("samlee", SAM, mfa=True, last_used=_ago(now, 6), detail="Restricted"),
        Identity(
            "ci-deploy",
            "",
            admin=True,
            mfa=False,
            last_used=_ago(now, 70),
            detail="Unrestricted",
        ),
    ]
    return [
        _scope(
            "A1B2C3D4-E5F6-4789-9ABC-DEF012345678",
            "Example Ltd",
            {
                "identity.mfa": (
                    "attention",
                    "MFA off for 1 of 3 users",
                    ["ci-deploy"],
                ),
                "identity.admin_mfa": (
                    "attention",
                    "MFA off for 1 of 2 unrestricted users",
                    ["ci-deploy"],
                ),
                "identity.admins": (
                    "info",
                    "2 unrestricted users of 3",
                    ["alexmorgan", "ci-deploy"],
                ),
                "identity.inactive": (
                    "attention",
                    "1 of 3 users unused for 45 days or more",
                    ["ci-deploy"],
                ),
                "network.admin_ports": (
                    "met",
                    "0 of 2 enabled firewalls let SSH or RDP in from anywhere",
                ),
                "network.firewall_coverage": (
                    "attention",
                    "1 of 4 Linodes have no enabled Cloud Firewall",
                    ["build-runner"],
                ),
                "network.databases": (
                    "met",
                    "0 of 1 managed database allow every address",
                ),
                "resilience.backups": (
                    "attention",
                    "1 of 4 Linodes without backups",
                    ["build-runner"],
                ),
                "kubernetes.control_plane": (
                    "met",
                    "0 of 1 cluster lack high availability or an access list",
                ),
            },
            people,
            checks,
        )
    ]


def _digitalocean(now: datetime) -> list[ScopeResult]:
    checks = cloud_provider("digitalocean").checks
    return [
        _scope(
            "5f0c1d2e-3a4b-4c5d-8e6f-708192a3b4c5",
            "Example Ltd",
            {
                "network.admin_ports": (
                    "attention",
                    "1 of 2 firewalls let SSH or RDP in from anywhere",
                    ["legacy-ssh"],
                ),
                "network.firewall_coverage": (
                    "met",
                    "0 of 3 Droplets have no Cloud Firewall",
                ),
                "network.databases": (
                    "met",
                    "0 of 1 managed database accept connections from anywhere",
                ),
                "resilience.backups": (
                    "attention",
                    "1 of 3 Droplets without backups",
                    ["staging-web"],
                ),
                "kubernetes.control_plane": (
                    "attention",
                    "1 of 1 cluster lack high availability or automatic upgrades",
                    ["apps-ams3: no high availability"],
                ),
                "findings": (
                    "attention",
                    "0 critical and 1 high findings in the latest scan",
                ),
            },
            [],
            checks,
            kind="team",
        )
    ]


def _scaleway(now: datetime) -> list[ScopeResult]:
    checks = cloud_provider("scaleway").checks
    people = [
        Identity(
            ALEX, ALEX, admin=True, mfa=True, last_used=_ago(now, 0), detail="Owner"
        ),
        Identity(
            SAM,
            SAM,
            admin=True,
            mfa=True,
            last_used=_ago(now, 2),
            detail="IAM manager",
        ),
        Identity(JORDAN, JORDAN, mfa=False, last_used=_ago(now, 9)),
    ]
    return [
        _scope(
            "6a7b8c9d-0e1f-4a2b-9c3d-4e5f6a7b8c9d",
            "example-ltd",
            {
                "identity.mfa": ("attention", "MFA off for 1 of 3 members", [JORDAN]),
                "identity.admin_mfa": ("met", "MFA on for 2 of 2 administrators"),
                "identity.admins": (
                    "info",
                    "2 users and 1 group or application hold the owner role, "
                    "OrganizationManager or IAMManager",
                    [ALEX, SAM, "application/terraform"],
                ),
                "identity.inactive": ("met", "0 of 3 users unused for 45 days or more"),
                "credentials.key_age": (
                    "attention",
                    "1 user or application hold API keys older than 90 days, of 4 "
                    "live keys",
                    ["application/terraform"],
                ),
                "network.admin_ports": (
                    "met",
                    "0 of 3 security groups in use let SSH or RDP in from anywhere",
                ),
                "network.databases": (
                    "attention",
                    "1 of 2 Database Instances accept connections from anywhere",
                    ["fr-par/analytics"],
                ),
                "resilience.backups": (
                    "met",
                    "0 of 2 Database Instances without automatic backups",
                ),
                "kubernetes.control_plane": (
                    "met",
                    "0 of 1 cluster lack automatic upgrades or an access list",
                ),
            },
            people,
            checks,
            kind="organisation",
        )
    ]


RESULTS = {
    "aws": _aws,
    "azure": _azure,
    "google_cloud": _google_cloud,
    "google_workspace": _google_workspace,
    "linode": _linode,
    "digitalocean": _digitalocean,
    "scaleway": _scaleway,
}

ACTIVITY = {
    "azure": [
        (
            "7f3c2a1e-4b5d-4c6e-8f70-91a2b3c4d5e6",
            "SignIn",
            ALEX,
            "success",
            {"service": "entra", "mfa_used": True},
        ),
        (
            "7f3c2a1e-4b5d-4c6e-8f70-91a2b3c4d5e6",
            "SignIn",
            JORDAN,
            "failure",
            {"service": "entra", "mfa_used": False},
        ),
        (
            "7f3c2a1e-4b5d-4c6e-8f70-91a2b3c4d5e6",
            "Add member to role",
            ALEX,
            "success",
            {"service": "Core Directory"},
        ),
        (
            "1a2b3c4d-1111-4222-8333-444455556666",
            "Microsoft.Authorization/roleAssignments/write",
            ALEX,
            "success",
            {"service": "Microsoft.Authorization"},
        ),
    ],
    "google_cloud": [
        (
            "123456789012",
            "SetIamPolicy",
            ALEX,
            "success",
            {"service": "cloudresourcemanager.googleapis.com"},
        ),
        (
            "123456789012",
            "CreateServiceAccountKey",
            "terraform@acme-ops.iam.gserviceaccount.com",
            "success",
            {"service": "iam.googleapis.com"},
        ),
    ],
    "aws": [
        (
            "111122223333",
            "ConsoleLogin",
            "arn:aws:iam::111122223333:user/alex.morgan@example.invalid",
            "success",
            {"service": "signin", "mfa_used": True},
        ),
        (
            "444455556666",
            "ConsoleLogin",
            "arn:aws:iam::444455556666:user/jordan.patel@example.invalid",
            "success",
            {"service": "signin", "mfa_used": False},
        ),
        (
            "111122223333",
            "CreateAccessKey",
            "arn:aws:iam::111122223333:user/alex.morgan@example.invalid",
            "success",
            {"service": "iam"},
        ),
        (
            "444455556666",
            "ConsoleLogin",
            "arn:aws:iam::444455556666:user/contractor-2023",
            "failure",
            {"service": "signin", "mfa_used": False},
        ),
        (
            "111122223333",
            "AttachRolePolicy",
            "arn:aws:sts::111122223333:assumed-role/BreakGlass/alex",
            "success",
            {"service": "iam"},
        ),
    ],
    "linode": [
        (
            "A1B2C3D4-E5F6-4789-9ABC-DEF012345678",
            "login",
            "alexmorgan",
            "success",
            {"service": "account"},
        ),
        (
            "A1B2C3D4-E5F6-4789-9ABC-DEF012345678",
            "firewall_update",
            "alexmorgan",
            "success",
            {"service": "account", "resource_type": "firewall"},
        ),
        (
            "A1B2C3D4-E5F6-4789-9ABC-DEF012345678",
            "user_create",
            "alexmorgan",
            "success",
            {"service": "account", "resource_type": "account"},
        ),
    ],
    "digitalocean": [
        (
            "5f0c1d2e-3a4b-4c5d-8e6f-708192a3b4c5",
            "snapshot",
            "",
            "success",
            {"service": "digitalocean", "resource_type": "droplet"},
        ),
        (
            "5f0c1d2e-3a4b-4c5d-8e6f-708192a3b4c5",
            "power_cycle",
            "",
            "success",
            {"service": "digitalocean", "resource_type": "droplet"},
        ),
    ],
    "scaleway": [
        (
            "6a7b8c9d-0e1f-4a2b-9c3d-4e5f6a7b8c9d",
            "SignIn",
            ALEX,
            "success",
            {"method": "password", "mfa": "totp"},
        ),
        (
            "6a7b8c9d-0e1f-4a2b-9c3d-4e5f6a7b8c9d",
            "CreateAPIKey",
            SAM,
            "success",
            {"product": "iam", "service": "scaleway.iam.v1alpha1.Api"},
        ),
        (
            "6a7b8c9d-0e1f-4a2b-9c3d-4e5f6a7b8c9d",
            "SignIn",
            JORDAN,
            "failure",
            {"method": "password", "failure_reason": "invalid_mfa"},
        ),
    ],
}


def results(key: str, now: datetime | None = None) -> list[ScopeResult]:
    build = RESULTS.get(key)
    return build(now or utc_now_naive()) if build else []


def activity(key: str, since: datetime, until: datetime) -> tuple[list[Activity], bool]:
    """One fictional entry per hour in [since, until), with stable IDs."""
    spec = ACTIVITY.get(key) or []
    out = []
    if not spec:
        return out, True
    hour = since.replace(minute=0, second=0, microsecond=0)
    while hour < until and len(out) < 500:
        at = hour + timedelta(minutes=17)
        if since <= at < until:
            stamp = int(hour.timestamp() // 3600)
            scope, action, actor, outcome, detail = spec[stamp % len(spec)]
            short = actor.rsplit("/", 1)[-1] or key
            out.append(
                Activity(
                    id=f"demo-{key}-{stamp}",
                    at=at,
                    scope=scope,
                    action=action,
                    actor=actor,
                    outcome=outcome,
                    summary=f"{action} by {short} ({outcome})",
                    detail=dict(detail),
                    raw={"demo": True, "eventName": action},
                )
            )
        hour += timedelta(hours=1)
    return out, True


def seed(db, days: int = 3) -> int:
    """Store every provider's fictional results and a few days of activity,
    as a demonstration environment's refresh would. Only demo mode seeds."""
    from app.core.config import settings
    from app.db.models import IngestionCursor
    from app.ingest.cloud import KEYS, runner
    from app.ingest.connections import connection_scope, namespace

    if not settings.demo_mode:
        return 0
    for key in KEYS:
        runner.refresh(db, key, force=True)
        provider = cloud_provider(key)
        if provider.activity is None:
            continue
        now = utc_now_naive()
        until = now - provider.activity_lag
        with connection_scope(runner.connection(provider)):
            entries, _ = activity(key, until - timedelta(days=days), until)
            for entry in entries:
                runner.record_activity(db, provider, entry)
            name = namespace("cloud-activity:" + key)
            cursor = db.query(IngestionCursor).filter_by(name=name).one_or_none()
            if cursor is None:
                cursor = IngestionCursor(name=name, meta={})
                db.add(cursor)
            cursor.last_ts, cursor.updated_at = until, now
            row = runner.provider_row(db, provider)
            runner.link_vendor(db, provider, row)
            row.activity_at = now
        db.commit()
    return len(KEYS)
