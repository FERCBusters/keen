"""AWS posture: IAM, CloudTrail, S3, network and Security Hub checks for each
configured account, and CloudTrail console sign-ins and IAM changes.

Each scope is one AWS account. KEEN reaches it with the deployment's posture
credentials, either directly or by assuming a read-only role in the account
(``KEEN_AWS_ROLE_ARNS``), and confirms through STS that the credentials answer
for that account before reading anything. Only read actions are used.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from app.core.config import settings
from app.ingest.cloud.base import (
    MAX_ACTIVITY,
    MAX_IDENTITIES,
    MAX_SCOPES,
    Activity,
    Check,
    Identity,
    Provider,
    ScopeResult,
    Unavailable,
    clip,
    guarded,
    inactive_check,
    mfa_check,
    parse_time,
    plural,
    split_list,
    text_field,
    unknown,
)

ROLE_ARN = re.compile(
    r"arn:aws(?:-cn|-us-gov)?:iam::([0-9]{12}):role/(?:[\w+=,.@-]+/)*[\w+=,.@-]{1,64}"
)
REGION = re.compile(r"[a-z]{2}(?:-gov)?-[a-z]+-[0-9]")
EXTERNAL_ID = re.compile(r"[\w+=,.@:/-]{2,1224}")
ADMIN_POLICY = re.compile(
    r"arn:aws(?:-cn|-us-gov)?:iam::aws:policy/AdministratorAccess"
)
EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,}")
DENIED = {
    "AccessDenied",
    "AccessDeniedException",
    "UnauthorizedOperation",
    "UnauthorizedAccess",
}
NOT_CONFIGURED = {
    # Security Hub is not enabled in the region.
    "InvalidAccessException",
    "InvalidClientTokenId",
    "SignatureDoesNotMatch",
    "ExpiredToken",
    "ExpiredTokenException",
    "UnrecognizedClientException",
    "InvalidIdentityToken",
}
CLIENT_CONFIG = Config(
    connect_timeout=10,
    read_timeout=30,
    retries={"max_attempts": 3, "mode": "standard"},
)
REPORT_DEADLINE = 60.0
REPORT_PAUSE = 2.0
# Credential reports list every IAM user; KEEN reads at most this many bytes.
REPORT_LIMIT = 16 * 1024 * 1024
DETAIL_PAGES = 200
NEVER = ("N/A", "no_information", "not_supported", "")
MAX_TRAILS = 50
MAX_REGIONS = 40
NETWORK_PAGES = 50
ADMIN_PORTS = (22, 3389)
EVERYWHERE = ("0.0.0.0/0", "::/0")
FINDINGS_PAGES = 50
# Console sign-ins through the global sign-in endpoint land in these regions.
SIGNIN_REGIONS = ("us-east-1", "us-east-2", "us-west-2", "eu-north-1", "ap-southeast-2")
# LookupEvents answers two requests a second per account and region.
LOOKUP_PAUSE = 0.5
LOOKUP_PAGES = 100
MAX_TRAIL_RECORD = 256 * 1024
MIN_PASSWORD_LENGTH = 14
MIN_REUSE_PREVENTION = 24

CHECKS = {
    "identity.mfa": "MFA for console users",
    "identity.admin_mfa": "MFA for administrators",
    "identity.admins": "Administrators",
    "identity.inactive": "Unused users",
    "credentials.key_age": "Access key age",
    "aws.root": "Root user",
    "aws.password_policy": "Password policy",
    "logging.audit_trail": "CloudTrail logging",
    "storage.public_access": "S3 Block Public Access",
    "network.admin_ports": "SSH and RDP open to the internet",
    "network.databases": "Databases open to the internet",
    "findings": "Security Hub findings",
}
# Every read action KEEN uses; Help sets them out as one IAM policy.
PERMISSIONS = (
    "iam:GetAccountSummary",
    "iam:GenerateCredentialReport",
    "iam:GetCredentialReport",
    "iam:GetAccountAuthorizationDetails",
    "iam:GetAccountPasswordPolicy",
    "iam:ListAccountAliases",
    "cloudtrail:DescribeTrails",
    "cloudtrail:GetTrailStatus",
    "cloudtrail:LookupEvents",
    "s3:GetAccountPublicAccessBlock",
    "ec2:DescribeRegions",
    "ec2:DescribeSecurityGroups",
    "ec2:DescribeNetworkInterfaces",
    "rds:DescribeDBInstances",
    "securityhub:GetFindings",
)
IDENTITY_CHECKS = (
    "identity.mfa",
    "identity.admin_mfa",
    "identity.admins",
    "identity.inactive",
    "credentials.key_age",
    "aws.root",
    "aws.password_policy",
)


# --- transport ------------------------------------------------------------


def _reason(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code") or "")
        if code in DENIED:
            return "permission_denied"
        if code in NOT_CONFIGURED:
            return "not_configured"
        return "unavailable"
    if isinstance(exc, NoCredentialsError):
        return "not_configured"
    return "unavailable"


def call(function, **kwargs):
    """One AWS call; failures become a reason code, never provider text."""
    try:
        return function(**kwargs)
    except (ClientError, BotoCoreError) as exc:
        raise Unavailable(_reason(exc)) from None


def _checked(client):
    # SDK environment or shared-config endpoint overrides must not send signed
    # requests or session tokens over HTTP. Certificate verification stays on.
    endpoint = urlsplit(client.meta.endpoint_url)
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.fragment
    ):
        client.close()
        raise Unavailable("not_configured")
    return client


class Session:
    """One account's AWS session; clients are made on demand and checked."""

    def __init__(self, session, region: str, account_id: str):
        self.session = session
        self.region = region
        self.account_id = account_id

    def client(self, service: str, region: str | None = None):
        return _checked(
            self.session.client(
                service, region_name=region or self.region, config=CLIENT_CONFIG
            )
        )


def region() -> str:
    value = str(settings.aws_region or "").strip() or "us-east-1"
    if not REGION.fullmatch(value):
        raise Unavailable("not_configured")
    return value


def roles() -> list[str]:
    found = split_list(settings.aws_role_arns)
    if (
        len(found) > MAX_SCOPES
        or len(set(found)) != len(found)
        or any(not ROLE_ARN.fullmatch(r) for r in found)
    ):
        raise Unavailable("not_configured")
    return found


def network_regions_setting() -> list[str]:
    found = split_list(settings.aws_regions)
    if len(found) > MAX_REGIONS or any(not REGION.fullmatch(r) for r in found):
        raise Unavailable("not_configured")
    return sorted(set(found))


def _base_session():
    key = str(settings.aws_access_key_id or "")
    secret = str(settings.aws_secret_access_key or "")
    token = str(settings.aws_session_token or "")
    if not key and not secret:
        return boto3.Session()
    if not key or not secret:
        raise Unavailable("not_configured")
    return boto3.Session(
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        aws_session_token=token or None,
    )


def account_session(base, role: str | None) -> Session:
    """The session for one account, confirmed through STS."""
    home = region()
    expected = ""
    if role:
        expected = ROLE_ARN.fullmatch(role).group(1)
        external = str(settings.aws_external_id or "")
        if external and not EXTERNAL_ID.fullmatch(external):
            raise Unavailable("not_configured")
        sts = Session(base, home, "").client("sts")
        try:
            options = {
                "RoleArn": role,
                "RoleSessionName": "keen-posture",
                "DurationSeconds": 900,
            }
            if external:
                options["ExternalId"] = external
            granted = call(sts.assume_role, **options)["Credentials"]
        finally:
            sts.close()
        base = boto3.Session(
            aws_access_key_id=granted["AccessKeyId"],
            aws_secret_access_key=granted["SecretAccessKey"],
            aws_session_token=granted["SessionToken"],
        )
    scoped = Session(base, home, "")
    sts = scoped.client("sts")
    try:
        account = str(call(sts.get_caller_identity).get("Account") or "")
    finally:
        sts.close()
    if not re.fullmatch(r"[0-9]{12}", account) or (expected and account != expected):
        # Results for one account must never come from another.
        raise Unavailable("not_configured")
    scoped.account_id = account
    return scoped


# --- identity -------------------------------------------------------------


def _flag(value) -> bool | None:
    text = str(value or "").strip().upper()
    return True if text == "TRUE" else False if text == "FALSE" else None


def credential_report(iam, *, deadline: float = REPORT_DEADLINE) -> list[dict]:
    """The credential report rows, generated if needed within the deadline."""
    end = time.monotonic() + deadline
    while True:
        state = call(iam.generate_credential_report).get("State")
        if state == "COMPLETE":
            break
        if time.monotonic() + REPORT_PAUSE > end:
            raise Unavailable("unavailable")
        time.sleep(REPORT_PAUSE)
    content = call(iam.get_credential_report).get("Content") or b""
    if isinstance(content, str):
        content = content.encode("utf-8")
    if len(content) > REPORT_LIMIT:
        raise Unavailable("unavailable")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise Unavailable("unavailable") from None
    rows = list(csv.DictReader(io.StringIO(text)))
    if len(rows) > MAX_IDENTITIES:
        raise Unavailable("unavailable")
    return rows


def authorisation_details(iam) -> dict:
    out = {
        "UserDetailList": [],
        "RoleDetailList": [],
        "GroupDetailList": [],
        "Policies": [],
    }
    marker = None
    for _ in range(DETAIL_PAGES):
        options = {
            "Filter": ["User", "Role", "Group", "LocalManagedPolicy"],
            "MaxItems": 1000,
        }
        if marker:
            options["Marker"] = marker
        page = call(iam.get_account_authorization_details, **options)
        for key in out:
            out[key].extend(i for i in page.get(key) or [] if isinstance(i, dict))
        if not page.get("IsTruncated"):
            return out
        marker = page.get("Marker")
    # A partial snapshot would undercount administrators.
    raise Unavailable("unavailable")


def password_policy(iam) -> dict | None:
    try:
        return iam.get_account_password_policy().get("PasswordPolicy") or {}
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "NoSuchEntity":
            return None
        raise Unavailable(_reason(exc)) from None
    except BotoCoreError as exc:
        raise Unavailable(_reason(exc)) from None


def _document(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(unquote(value))
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _listed(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def allows_everything(document) -> bool:
    """An allowed ``*`` action on a ``*`` resource, conditions or not."""
    for statement in _listed(_document(document).get("Statement")):
        if (
            isinstance(statement, dict)
            and statement.get("Effect") == "Allow"
            and "*" in _listed(statement.get("Action"))
            and "*" in _listed(statement.get("Resource"))
        ):
            return True
    return False


def _admin_policies(details: dict) -> set[str]:
    admins = set()
    for policy in details["Policies"]:
        default = next(
            (
                v
                for v in policy.get("PolicyVersionList") or []
                if isinstance(v, dict) and v.get("IsDefaultVersion")
            ),
            None,
        )
        if default and allows_everything(default.get("Document")):
            admins.add(policy.get("Arn"))
    return admins


def _grants_admin(entity: dict, inline_key: str, admins: set[str]) -> bool:
    for attached in entity.get("AttachedManagedPolicies") or []:
        arn = str(attached.get("PolicyArn") or "")
        if ADMIN_POLICY.fullmatch(arn) or arn in admins:
            return True
    return any(
        allows_everything(inline.get("PolicyDocument"))
        for inline in entity.get(inline_key) or []
        if isinstance(inline, dict)
    )


def administrators(details: dict) -> tuple[set[str], list[str]]:
    """IAM user ARNs and role names holding administrator rights; service-linked
    roles are left out."""
    admins = _admin_policies(details)
    groups = {
        g.get("GroupName")
        for g in details["GroupDetailList"]
        if _grants_admin(g, "GroupPolicyList", admins)
    }
    users = {
        str(u.get("Arn") or "")
        for u in details["UserDetailList"]
        if _grants_admin(u, "UserPolicyList", admins)
        or any(g in groups for g in u.get("GroupList") or [])
    }
    role_names = sorted(
        str(r.get("RoleName") or "")
        for r in details["RoleDetailList"]
        if not str(r.get("Path") or "").startswith("/aws-service-role/")
        and _grants_admin(r, "RolePolicyList", admins)
    )
    return users, role_names


def _email(name: str, tags) -> str:
    for tag in tags or []:
        if isinstance(tag, dict) and str(tag.get("Key") or "").lower() in (
            "email",
            "mail",
        ):
            value = str(tag.get("Value") or "").strip()
            if EMAIL.fullmatch(value):
                return value
    return name if EMAIL.fullmatch(name) else ""


def _active_keys(row: dict) -> list[tuple[datetime | None, datetime | None]]:
    """(rotated, last used) for each active access key."""
    return [
        (
            parse_time(row.get(f"access_key_{slot}_last_rotated")),
            parse_time(row.get(f"access_key_{slot}_last_used_date")),
        )
        for slot in ("1", "2")
        if _flag(row.get(f"access_key_{slot}_active"))
    ]


def identity_checks(aws: Session, now: datetime) -> tuple[list[Check], list[Identity]]:
    iam = aws.client("iam")
    try:
        summary = call(iam.get_account_summary).get("SummaryMap") or {}
        rows = credential_report(iam)
        details = authorisation_details(iam)
        policy = password_policy(iam)
    finally:
        iam.close()
    root = next((r for r in rows if r.get("user") == "<root_account>"), None)
    tags = {str(u.get("Arn") or ""): u.get("Tags") for u in details["UserDetailList"]}
    admin_users, admin_roles = administrators(details)
    people = []
    for row in rows:
        if row is root:
            continue
        name = str(row.get("user") or "")
        arn = str(row.get("arn") or name)
        console = bool(_flag(row.get("password_enabled")))
        keys = _active_keys(row)
        uses = [parse_time(row.get("password_last_used"))] + [used for _, used in keys]
        access = ["console"] if console else []
        if keys:
            access.append(plural(len(keys), "access key"))
        people.append(
            Identity(
                name=name,
                email=_email(name, tags.get(arn)),
                kind="service" if keys and not console else "user",
                admin=arn in admin_users,
                mfa=bool(_flag(row.get("mfa_active"))) if console else None,
                last_used=max((u for u in uses if u), default=None),
                created=parse_time(row.get("user_creation_time")),
                detail=(" and ".join(access) or "No console or keys").capitalize(),
            )
        )
    console = [p for p in people if p.mfa is not None]
    checks = [
        mfa_check("identity.mfa", CHECKS["identity.mfa"], console, who="console users"),
        mfa_check(
            "identity.admin_mfa",
            CHECKS["identity.admin_mfa"],
            [p for p in console if p.admin],
            who="administrators",
        ),
    ]
    admin_names = [p.name for p in people if p.admin] + [
        "role/" + r for r in admin_roles
    ]
    admin_people = sum(1 for p in people if p.admin)
    checks.append(
        Check(
            "identity.admins",
            CHECKS["identity.admins"],
            "info",
            f"{plural(admin_people, 'user')} and {plural(len(admin_roles), 'role')} "
            "hold administrator rights",
            admin_names,
        )
    )
    checks.append(
        inactive_check(
            "identity.inactive",
            CHECKS["identity.inactive"],
            people,
            settings.cloud_inactive_days,
            now,
        )
    )
    limit = settings.cloud_key_age_days
    old, total = [], 0
    for row in rows:
        if row is root:
            continue
        for rotated, _ in _active_keys(row):
            total += 1
            if rotated is not None and (now - rotated).days >= limit:
                old.append(str(row.get("user") or ""))
    if not total:
        checks.append(
            Check(
                "credentials.key_age",
                CHECKS["credentials.key_age"],
                "info",
                "No active access keys",
            )
        )
    elif old:
        checks.append(
            Check(
                "credentials.key_age",
                CHECKS["credentials.key_age"],
                "attention",
                f"{len(old)} of {plural(total, 'active access key')} older than "
                f"{limit} days",
                old,
            )
        )
    else:
        checks.append(
            Check(
                "credentials.key_age",
                CHECKS["credentials.key_age"],
                "met",
                f"0 of {plural(total, 'active access key')} older than {limit} days",
            )
        )
    checks.append(root_check(summary, root))
    checks.append(policy_check(policy))
    if root is not None:
        people.insert(
            0,
            Identity(
                name="root",
                kind="root",
                admin=True,
                mfa=bool(summary.get("AccountMFAEnabled")),
                last_used=parse_time(root.get("password_last_used")),
                created=parse_time(root.get("user_creation_time")),
                detail="The account's root user",
            ),
        )
    return checks, people


def root_check(summary: dict, root: dict | None) -> Check:
    problems = []
    if summary.get("AccountMFAEnabled") != 1:
        problems.append("MFA is off")
    if summary.get("AccountAccessKeysPresent"):
        problems.append("it has access keys")
    if summary.get("AccountSigningCertificatesPresent"):
        problems.append("it has signing certificates")
    label = CHECKS["aws.root"]
    if problems:
        return Check(
            "aws.root", label, "attention", "Root user: " + ", ".join(problems)
        )
    return Check("aws.root", label, "met", "Root user has MFA and no access keys")


def policy_check(policy: dict | None) -> Check:
    label = CHECKS["aws.password_policy"]
    if policy is None:
        return Check(
            "aws.password_policy", label, "attention", "No password policy is set"
        )
    problems = []
    length = policy.get("MinimumPasswordLength")
    if not isinstance(length, int) or length < MIN_PASSWORD_LENGTH:
        problems.append(f"minimum length {length} is under {MIN_PASSWORD_LENGTH}")
    reuse = policy.get("PasswordReusePrevention")
    if not isinstance(reuse, int) or reuse < MIN_REUSE_PREVENTION:
        problems.append(
            f"reuse prevention {reuse or 'is off'}"
            + (f" is under {MIN_REUSE_PREVENTION}" if reuse else "")
        )
    if problems:
        return Check(
            "aws.password_policy", label, "attention", "; ".join(problems).capitalize()
        )
    return Check(
        "aws.password_policy",
        label,
        "met",
        f"Minimum length {length}, last {reuse} passwords cannot be reused",
    )


# --- logging and storage ----------------------------------------------------


def audit_trail(aws: Session) -> Check:
    """Whether a multi-region trail is logging, including organisation trails."""
    label = CHECKS["logging.audit_trail"]
    trail = aws.client("cloudtrail")
    try:
        trails = (
            call(trail.describe_trails, includeShadowTrails=True).get("trailList") or []
        )[:MAX_TRAILS]
        total = logging_count = 0
        multi = organisation = validation = undetermined = False
        for item in trails:
            arn = str(item.get("TrailARN") or "") if isinstance(item, dict) else ""
            if not arn:
                continue
            total += 1
            try:
                status = call(trail.get_trail_status, Name=arn)
            except Unavailable:
                if item.get("IsOrganizationTrail") is not True:
                    raise
                # Member accounts may see an organisation trail without being
                # allowed its status.
                undetermined = True
                continue
            on = status.get("IsLogging") is True
            logging_count += on
            if on and item.get("IsMultiRegionTrail") is True:
                multi = True
                organisation = organisation or item.get("IsOrganizationTrail") is True
                validation = validation or item.get("LogFileValidationEnabled") is True
    finally:
        trail.close()
    if multi:
        notes = [
            n
            for n, on in (
                ("organisation trail", organisation),
                ("log file validation on", validation),
            )
            if on
        ]
        return Check(
            "logging.audit_trail",
            label,
            "met",
            "A multi-region trail is logging"
            + (f" ({', '.join(notes)})" if notes else ""),
        )
    if undetermined:
        return Check(
            "logging.audit_trail",
            label,
            "unknown",
            "The organisation trail's status cannot be read from this account",
            reason="permission_denied",
        )
    return Check(
        "logging.audit_trail",
        label,
        "attention",
        f"No multi-region trail is logging ({plural(total, 'trail')}, "
        f"{logging_count} logging)",
    )


BLOCK_FIELDS = (
    ("BlockPublicAcls", "block public ACLs"),
    ("IgnorePublicAcls", "ignore public ACLs"),
    ("BlockPublicPolicy", "block public policies"),
    ("RestrictPublicBuckets", "restrict public buckets"),
)


def public_access(aws: Session) -> Check:
    """The account-level S3 Block Public Access setting."""
    label = CHECKS["storage.public_access"]
    control = aws.client("s3control")
    try:
        try:
            block = (
                control.get_public_access_block(AccountId=aws.account_id).get(
                    "PublicAccessBlockConfiguration"
                )
                or {}
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code != "NoSuchPublicAccessBlockConfiguration":
                raise Unavailable(_reason(exc)) from None
            block = None
        except BotoCoreError as exc:
            raise Unavailable(_reason(exc)) from None
    finally:
        control.close()
    if block is None:
        return Check(
            "storage.public_access",
            label,
            "attention",
            "Block Public Access is not set for the account",
        )
    off = [text for key, text in BLOCK_FIELDS if block.get(key) is not True]
    if off:
        return Check(
            "storage.public_access", label, "attention", "Off: " + ", ".join(off)
        )
    return Check(
        "storage.public_access",
        label,
        "met",
        "All four settings are on for the account",
    )


# --- network ------------------------------------------------------------------


def _all(method, key: str, token: str = "NextToken", **options) -> list:
    """Every item of a paged EC2 or RDS listing; a longer listing is refused,
    since a partial population would misstate a check."""
    items, marker = [], None
    for _ in range(NETWORK_PAGES):
        page = call(method, **options, **({token: marker} if marker else {}))
        items.extend(i for i in page.get(key) or [] if isinstance(i, dict))
        marker = page.get(token)
        if not marker:
            return items
    raise Unavailable("unavailable")


def network_regions(aws: Session) -> list[str]:
    configured = network_regions_setting()
    if configured:
        return configured
    ec2 = aws.client("ec2")
    try:
        found = call(ec2.describe_regions, AllRegions=False).get("Regions") or []
    finally:
        ec2.close()
    names = sorted(
        {
            str(r.get("RegionName"))
            for r in found
            if isinstance(r, dict) and REGION.fullmatch(str(r.get("RegionName") or ""))
        }
    )
    if len(names) > MAX_REGIONS:
        raise Unavailable("unavailable")
    return names


def reaches(group: dict, port) -> bool:
    """Whether a security group admits the internet on a TCP port. Security
    groups only allow; anything no rule allows is denied."""
    if not isinstance(port, int):
        return False
    for permission in group.get("IpPermissions") or []:
        if not isinstance(permission, dict):
            continue
        protocol = str(permission.get("IpProtocol"))
        if protocol not in ("tcp", "6", "-1"):
            continue
        sources = [
            r.get("CidrIp")
            for r in permission.get("IpRanges") or []
            if isinstance(r, dict)
        ] + [
            r.get("CidrIpv6")
            for r in permission.get("Ipv6Ranges") or []
            if isinstance(r, dict)
        ]
        if not any(s in EVERYWHERE for s in sources):
            continue
        if protocol == "-1":
            return True
        low, high = permission.get("FromPort"), permission.get("ToPort")
        if isinstance(low, int) and isinstance(high, int) and low <= port <= high:
            return True
    return False


def network(aws: Session) -> list[Check]:
    """Security groups in use that let SSH or RDP in from the internet, and
    publicly accessible RDS instances whose groups admit the internet."""
    groups_used, exposing, databases, exposed = 0, [], 0, []
    for name in network_regions(aws):
        ec2 = aws.client("ec2", name)
        try:
            groups = _all(
                ec2.describe_security_groups, "SecurityGroups", MaxResults=1000
            )
            interfaces = _all(
                ec2.describe_network_interfaces, "NetworkInterfaces", MaxResults=1000
            )
        finally:
            ec2.close()
        attached = {
            str(g.get("GroupId"))
            for i in interfaces
            for g in i.get("Groups") or []
            if isinstance(g, dict) and g.get("GroupId")
        }
        by_id = {str(g.get("GroupId")): g for g in groups}
        for ident in sorted(attached & set(by_id)):
            groups_used += 1
            if any(reaches(by_id[ident], p) for p in ADMIN_PORTS):
                exposing.append(f"{name}/{ident}")
        rds = aws.client("rds", name)
        try:
            instances = _all(
                rds.describe_db_instances, "DBInstances", "Marker", MaxRecords=100
            )
        finally:
            rds.close()
        for instance in instances:
            databases += 1
            if instance.get("PubliclyAccessible") is not True:
                continue
            endpoint = instance.get("Endpoint")
            port = endpoint.get("Port") if isinstance(endpoint, dict) else None
            used = [
                str(g.get("VpcSecurityGroupId"))
                for g in instance.get("VpcSecurityGroups") or []
                if isinstance(g, dict) and g.get("Status", "active") == "active"
            ]
            if any(reaches(by_id.get(g, {}), port) for g in used):
                exposed.append(f"{name}/{instance.get('DBInstanceIdentifier') or ''}")
    ports = CHECKS["network.admin_ports"]
    out = [
        Check(
            "network.admin_ports",
            ports,
            "attention",
            f"{len(exposing)} of {plural(groups_used, 'security group')} in use admit "
            "SSH or RDP from anywhere",
            exposing,
        )
        if exposing
        else Check(
            "network.admin_ports",
            ports,
            "met",
            f"0 of {plural(groups_used, 'security group')} in use admit SSH or RDP "
            "from anywhere",
        )
    ]
    label = CHECKS["network.databases"]
    out.append(
        Check(
            "network.databases",
            label,
            "attention",
            f"{len(exposed)} of {plural(databases, 'RDS instance')} accept connections "
            "from anywhere",
            exposed,
        )
        if exposed
        else Check(
            "network.databases",
            label,
            "met",
            f"0 of {plural(databases, 'RDS instance')} accept connections from anywhere",
        )
    )
    return out


# --- Security Hub ----------------------------------------------------------------

FINDING_FILTERS = {
    "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}],
    "WorkflowStatus": [
        {"Value": "NEW", "Comparison": "EQUALS"},
        {"Value": "NOTIFIED", "Comparison": "EQUALS"},
    ],
    "SeverityLabel": [
        {"Value": "CRITICAL", "Comparison": "EQUALS"},
        {"Value": "HIGH", "Comparison": "EQUALS"},
    ],
}


def findings(aws: Session) -> Check:
    """Active, unsuppressed critical and high Security Hub findings for the
    account, read from KEEN_AWS_REGION (choose the aggregation region)."""
    counts = {"critical": 0, "high": 0}
    truncated = False
    filters = {
        **FINDING_FILTERS,
        "AwsAccountId": [{"Value": aws.account_id, "Comparison": "EQUALS"}],
    }
    hub = aws.client("securityhub")
    try:
        token = None
        for page_number in range(FINDINGS_PAGES):
            page = call(
                hub.get_findings,
                Filters=filters,
                MaxResults=100,
                **({"NextToken": token} if token else {}),
            )
            for finding in page.get("Findings") or []:
                severity = (
                    finding.get("Severity") if isinstance(finding, dict) else None
                )
                level = str((severity or {}).get("Label") or "").lower()
                if level in counts:
                    counts[level] += 1
            token = page.get("NextToken")
            if not token:
                break
        else:
            truncated = True
    finally:
        hub.close()
    label = CHECKS["findings"]
    total = counts["critical"] + counts["high"]
    if not total:
        return Check("findings", label, "met", "No active critical or high findings")
    detail = f"{counts['critical']} critical and {counts['high']} high findings"
    return Check(
        "findings", label, "attention", ("At least " if truncated else "") + detail
    )


# --- collection ---------------------------------------------------------------


def _alias(aws: Session) -> str:
    iam = aws.client("iam")
    try:
        aliases = call(iam.list_account_aliases).get("AccountAliases") or []
    except Unavailable:
        aliases = []
    finally:
        iam.close()
    return clip(aliases[0], 128) if aliases and isinstance(aliases[0], str) else ""


def account_result(aws: Session) -> ScopeResult:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    people: list[Identity] = []

    def identity():
        checks, found = identity_checks(aws, now)
        people.extend(found)
        return checks

    checks = guarded(CHECKS, IDENTITY_CHECKS, identity)
    checks += guarded(CHECKS, ["logging.audit_trail"], lambda: [audit_trail(aws)])
    checks += guarded(CHECKS, ["storage.public_access"], lambda: [public_access(aws)])
    checks += guarded(
        CHECKS, ["network.admin_ports", "network.databases"], lambda: network(aws)
    )
    checks += guarded(CHECKS, ["findings"], lambda: [findings(aws)])
    alias = _alias(aws)
    return ScopeResult(aws.account_id, alias or aws.account_id, checks, people)


def _targets():
    base = _base_session()
    return base, (roles() or [None])


def collect() -> list[ScopeResult]:
    base, targets = _targets()
    out = []
    for role in targets:
        try:
            aws = account_session(base, role)
        except Unavailable as exc:
            if role is None:
                raise
            account = ROLE_ARN.fullmatch(role).group(1)
            out.append(
                ScopeResult(account, account, unknown(CHECKS, CHECKS, exc.reason))
            )
            continue
        out.append(account_result(aws))
    return out


# --- CloudTrail activity ------------------------------------------------------


def _trail_detail(raw) -> dict:
    if not isinstance(raw, str) or len(raw) > MAX_TRAIL_RECORD:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def trail_entry(event: dict, account: str) -> Activity | None:
    """One LookupEvents entry: console sign-ins, password changes and IAM
    writes are kept; anything else is skipped."""
    detail = _trail_detail(event.get("CloudTrailEvent"))
    name = text_field(event.get("EventName") or detail.get("eventName"))
    source = text_field(detail.get("eventSource") or event.get("EventSource"))
    who = (
        detail.get("userIdentity")
        if isinstance(detail.get("userIdentity"), dict)
        else {}
    )
    kind = text_field(who.get("type"), 32)
    actor = text_field(who.get("arn"), 512)
    if not actor and kind == "Root":
        actor = f"arn:aws:iam::{account}:root"
    error = text_field(detail.get("errorCode"), 128)
    facts = {
        "service": source.split(".", 1)[0],
        "region": text_field(detail.get("awsRegion"), 32),
        "identity_type": kind,
        "source_ip": text_field(detail.get("sourceIPAddress"), 64),
    }
    if source == "signin.amazonaws.com":
        if name in ("ConsoleLogin", "SwitchRole"):
            response = detail.get("responseElements")
            result = response.get(name) if isinstance(response, dict) else None
            outcome = {"Success": "success", "Failure": "failure"}.get(result, "info")
            extra = detail.get("additionalEventData")
            if isinstance(extra, dict) and extra.get("MFAUsed") in ("Yes", "No"):
                facts["mfa_used"] = extra["MFAUsed"] == "Yes"
        elif name.startswith("Password"):
            outcome = "failure" if error else "success"
        else:
            return None
    elif source == "iam.amazonaws.com":
        if (
            str(event.get("ReadOnly")).lower() == "true"
            or detail.get("readOnly") is True
        ):
            return None
        outcome = "failure" if error else "success"
    else:
        return None
    when = event.get("EventTime")
    ident = text_field(event.get("EventId"), 64)
    if not isinstance(when, datetime) or not name or not ident:
        return None
    if when.tzinfo:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    if error:
        facts["error_code"] = error
    who_short = actor.rsplit("/", 1)[-1] if actor else kind or "unknown"
    return Activity(
        id=ident,
        at=when,
        scope=account,
        action=name,
        actor=actor,
        outcome=outcome,
        summary=f"{name} by {who_short} ({outcome})",
        detail={k: v for k, v in facts.items() if v not in ("", None)},
        raw=detail,
    )


def global_region(home: str) -> str:
    """Where CloudTrail records a partition's global service events."""
    if home.startswith("us-gov-"):
        return "us-gov-west-1"
    if home.startswith("cn-"):
        return "cn-north-1"
    return "us-east-1"


def _lookup(client, source, since, until, account, room, pages):
    """Entries from one event source, newest first: up to ``room`` + 1 of
    them, the pages read, and whether the window was read to its end."""
    found, token = [], None
    for page_number in range(pages):
        if page_number:
            time.sleep(LOOKUP_PAUSE)
        options = {
            "LookupAttributes": [
                {"AttributeKey": "EventSource", "AttributeValue": source}
            ],
            "StartTime": since.replace(tzinfo=timezone.utc),
            "EndTime": until.replace(tzinfo=timezone.utc),
            "MaxResults": 50,
        }
        if token:
            options["NextToken"] = token
        page = call(client.lookup_events, **options)
        for event in page.get("Events") or []:
            entry = trail_entry(event, account) if isinstance(event, dict) else None
            if entry is not None:
                found.append(entry)
        token = page.get("NextToken")
        if not token:
            return found, page_number + 1, True
        if len(found) > room:
            return found, page_number + 1, False
    return found, pages, False


def activity(since: datetime, until: datetime) -> tuple[list[Activity], bool]:
    """Console sign-ins from each sign-in region and IAM changes from the
    partition's global-service region, for every account."""
    base, targets = _targets()
    out: list[Activity] = []
    for role in targets:
        aws = account_session(base, role)
        queries = [(r, "signin.amazonaws.com") for r in SIGNIN_REGIONS]
        queries.append((global_region(aws.region), "iam.amazonaws.com"))
        pages = LOOKUP_PAGES
        for name, source in queries:
            client = aws.client("cloudtrail", name)
            try:
                found, used, done = _lookup(
                    client,
                    source,
                    since,
                    until,
                    aws.account_id,
                    MAX_ACTIVITY - len(out),
                    pages,
                )
            finally:
                client.close()
            pages -= used
            out.extend(found)
            if not done or len(out) > MAX_ACTIVITY:
                return sorted(out, key=lambda e: e.at)[-MAX_ACTIVITY:], False
    return sorted(out, key=lambda e: e.at), True


PROVIDER = Provider(
    key="aws",
    label="AWS",
    vendor="Amazon Web Services",
    website="https://aws.amazon.com",
    aliases=("AWS", "Amazon Web Services, Inc."),
    scope_noun="account",
    checks=CHECKS,
    collect=collect,
    activity=activity,
    # CloudTrail delivers management events within about 15 minutes.
    activity_lag=timedelta(minutes=20),
)
