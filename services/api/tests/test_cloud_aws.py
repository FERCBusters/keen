"""AWS posture provider: each check, the account session and CloudTrail
activity, against in-memory answers. No network is used."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.ingest.cloud import aws
from app.ingest.cloud.base import Unavailable, parse_time
from botocore.exceptions import EndpointConnectionError, NoCredentialsError

from tests.cloud_fakes import (
    ACCOUNT,
    TRAIL,
    FakeCloudTrail,
    FakeIam,
    FakeS3Control,
    FakeSession,
    ago,
    default_rows,
    error,
    report,
    row,
    trail_event,
)

NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def by_key(checks):
    return {c.key: c for c in checks}


# --- identity -------------------------------------------------------------


def test_identity_checks_measure_each_user():
    checks, people = aws.identity_checks(FakeSession(), NOW)
    found = by_key(checks)
    assert found["identity.mfa"].status == "attention"
    assert found["identity.mfa"].detail == "MFA off for 1 of 2 console users"
    assert found["identity.mfa"].subjects == ["bob"]
    assert found["identity.admin_mfa"].detail == "MFA off for 1 of 2 administrators"
    admins = found["identity.admins"]
    assert admins.status == "info"
    assert admins.detail == "3 users and 3 roles hold administrator rights"
    # Service-linked roles are left out; SSO and workload roles count.
    assert sorted(admins.subjects) == [
        "alice@example.org",
        "bob",
        "deploy-bot",
        "role/AWSReservedSSO_Admin_0123",
        "role/ci-deployer",
        "role/lambda-admin",
    ]
    inactive = found["identity.inactive"]
    assert inactive.detail == "2 of 4 users unused for 45 days or more"
    assert sorted(inactive.subjects) == ["bob", "deploy-bot"]
    keys = found["credentials.key_age"]
    assert keys.status == "attention" and keys.subjects == ["deploy-bot"]
    assert keys.detail == "1 of 1 active access key older than 90 days"
    assert found["aws.root"].status == "met"
    policy = found["aws.password_policy"]
    assert policy.status == "attention"
    assert policy.detail == "Reuse prevention 5 is under 24"

    named = {p.name: p for p in people}
    assert people[0].kind == "root" and people[0].mfa is True
    assert named["alice@example.org"].email == "alice@example.org"
    assert named["bob"].email == "bob@example.org" and named["bob"].admin
    assert named["deploy-bot"].kind == "service" and named["deploy-bot"].mfa is None
    assert named["deploy-bot"].detail == "1 access key"
    assert (
        named["ghost"].kind == "user" and named["ghost"].detail == "No console or keys"
    )
    assert named["alice@example.org"].detail == "Console"


def test_key_age_and_inactivity_follow_the_configured_days(monkeypatch):
    monkeypatch.setattr(settings, "cloud_key_age_days", 600)
    monkeypatch.setattr(settings, "cloud_inactive_days", 500)
    checks, _ = aws.identity_checks(FakeSession(), NOW)
    found = by_key(checks)
    assert found["credentials.key_age"].status == "met"
    assert found["credentials.key_age"].detail == (
        "0 of 1 active access key older than 600 days"
    )
    assert found["identity.inactive"].status == "met"


def test_root_and_password_policy_shortfalls_are_named():
    iam = FakeIam(
        summary={
            "AccountMFAEnabled": 0,
            "AccountAccessKeysPresent": 1,
            "AccountSigningCertificatesPresent": 0,
        },
        policy={"MinimumPasswordLength": 8},
    )
    found = by_key(aws.identity_checks(FakeSession(iam), NOW)[0])
    assert found["aws.root"].detail == "Root user: MFA is off, it has access keys"
    assert found["aws.password_policy"].detail == (
        "Minimum length 8 is under 14; reuse prevention is off"
    )
    missing = by_key(aws.identity_checks(FakeSession(FakeIam(policy=None)), NOW)[0])
    assert missing["aws.password_policy"].detail == "No password policy is set"
    good = FakeIam(policy={"MinimumPasswordLength": 16, "PasswordReusePrevention": 24})
    found = by_key(aws.identity_checks(FakeSession(good), NOW)[0])
    assert found["aws.password_policy"].status == "met"


def test_accounts_without_console_users_or_keys_report_information():
    iam = FakeIam(
        rows=[row("<root_account>", password_enabled="TRUE")],
        details={
            "UserDetailList": [],
            "GroupDetailList": [],
            "RoleDetailList": [],
            "Policies": [],
        },
    )
    found = by_key(aws.identity_checks(FakeSession(iam), NOW)[0])
    assert found["identity.mfa"].status == "info"
    assert found["identity.admin_mfa"].detail == "No administrators to check"
    assert found["credentials.key_age"].detail == "No active access keys"
    assert found["identity.inactive"].status == "info"


def test_credential_report_waits_within_its_deadline(monkeypatch):
    monkeypatch.setattr(aws, "REPORT_PAUSE", 0.0)
    iam = FakeIam(states=("STARTED", "COMPLETE"))
    assert aws.credential_report(iam)
    with pytest.raises(Unavailable):
        aws.credential_report(FakeIam(states=("STARTED",)), deadline=0.0)


def test_oversized_and_overlong_reports_are_refused(monkeypatch):
    monkeypatch.setattr(aws, "REPORT_LIMIT", 10)
    with pytest.raises(Unavailable):
        aws.credential_report(FakeIam())
    monkeypatch.setattr(aws, "REPORT_LIMIT", 16 * 1024 * 1024)
    monkeypatch.setattr(aws, "MAX_IDENTITIES", 2)
    with pytest.raises(Unavailable):
        aws.credential_report(FakeIam())


def test_authorisation_snapshots_must_be_complete(monkeypatch):
    monkeypatch.setattr(aws, "DETAIL_PAGES", 1)
    with pytest.raises(Unavailable):
        aws.authorisation_details(FakeIam())


def test_policy_documents_are_read_in_either_form():
    assert aws.allows_everything(
        {"Statement": {"Effect": "Allow", "Action": "*", "Resource": "*"}}
    )
    assert aws.allows_everything(
        "%7B%22Statement%22%3A%5B%7B%22Effect%22%3A%22Allow%22%2C%22Action%22%3A%22%2A%22%2C%22Resource%22%3A%22%2A%22%7D%5D%7D"
    )
    assert not aws.allows_everything("{not json")
    assert not aws.allows_everything(
        {"Statement": [{"Effect": "Deny", "Action": "*", "Resource": "*"}]}
    )


def test_a_denied_read_marks_its_group_and_leaves_the_rest():
    session = FakeSession(
        FakeIam(fail={"get_account_authorization_details": "AccessDenied"})
    )
    result = aws.account_result(session)
    found = by_key(result.checks)
    for key in aws.IDENTITY_CHECKS:
        assert found[key].status == "unknown"
        assert found[key].reason == "permission_denied"
    assert found["logging.audit_trail"].status == "met"
    assert result.identities == []
    assert result.label == "acme-prod"
    assert session.iam.closed


# --- logging and storage ----------------------------------------------------


def test_audit_trail_needs_a_logging_multi_region_trail():
    check = aws.audit_trail(FakeSession())
    assert check.status == "met"
    assert check.detail == "A multi-region trail is logging (log file validation on)"
    off = FakeCloudTrail(status={TRAIL: False})
    check = aws.audit_trail(FakeSession(trails=off))
    assert check.status == "attention"
    assert check.detail == "No multi-region trail is logging (1 trail, 0 logging)"
    assert off.closed


def test_organisation_trails_without_status_leave_logging_undetermined():
    org = FakeCloudTrail(
        trails=[
            {"TrailARN": TRAIL, "IsMultiRegionTrail": True, "IsOrganizationTrail": True}
        ],
        fail={TRAIL: "AccessDenied"},
    )
    check = aws.audit_trail(FakeSession(trails=org))
    assert check.status == "unknown" and check.reason == "permission_denied"
    own = FakeCloudTrail(fail={TRAIL: "AccessDenied"})
    with pytest.raises(Unavailable):
        aws.audit_trail(FakeSession(trails=own))


def test_public_access_block_reads_the_account_setting():
    control = FakeS3Control()
    assert aws.public_access(FakeSession(control=control)).status == "met"
    assert control.seen == [ACCOUNT] and control.closed
    partial = FakeS3Control(
        block={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": False,
        }
    )
    assert aws.public_access(FakeSession(control=partial)).detail == (
        "Off: restrict public buckets"
    )
    unset = aws.public_access(FakeSession(control=FakeS3Control(block=None)))
    assert unset.status == "attention"
    with pytest.raises(Unavailable) as caught:
        aws.public_access(FakeSession(control=FakeS3Control(fail="AccessDenied")))
    assert caught.value.reason == "permission_denied"


# --- network and Security Hub ------------------------------------------------


class FakeNetwork:
    """EC2, RDS and Security Hub answers for one region."""

    def __init__(self, groups=(), interfaces=(), databases=(), findings=(), page=1):
        self.groups, self.interfaces = list(groups), list(interfaces)
        self.databases, self.findings = list(databases), list(findings)
        self.page = page
        self.calls = []
        self.closed = 0

    def _paged(self, items, key, options, token="NextToken"):
        start = int(options.get(token) or 0)
        out = {key: items[start : start + self.page]}
        if start + self.page < len(items):
            out[token] = str(start + self.page)
        return out

    def describe_regions(self, **options):
        self.calls.append(("regions", options))
        names = ["us-east-1", "ap-southeast-2", "../bad"]
        return {"Regions": [{"RegionName": n} for n in names]}

    def describe_security_groups(self, **options):
        self.calls.append(("groups", options))
        return self._paged(self.groups, "SecurityGroups", options)

    def describe_network_interfaces(self, **options):
        self.calls.append(("interfaces", options))
        return self._paged(self.interfaces, "NetworkInterfaces", options)

    def describe_db_instances(self, **options):
        self.calls.append(("databases", options))
        return self._paged(self.databases, "DBInstances", options, "Marker")

    def get_findings(self, **options):
        self.calls.append(("findings", options))
        return self._paged(self.findings, "Findings", options)

    def close(self):
        self.closed += 1


def permission(low, high, cidr="0.0.0.0/0", protocol="tcp", v6=False):
    ranges = (
        {"Ipv6Ranges": [{"CidrIpv6": cidr}]} if v6 else {"IpRanges": [{"CidrIp": cidr}]}
    )
    return {"IpProtocol": protocol, "FromPort": low, "ToPort": high, **ranges}


class NetworkSession:
    def __init__(self, regions):
        self.regions = regions
        self.region = "us-east-1"
        self.account_id = ACCOUNT

    def client(self, service, region=None):
        return self.regions[region or self.region]


def regions_fixture():
    east = FakeNetwork(
        groups=[
            {"GroupId": "sg-ssh", "IpPermissions": [permission(20, 25), "junk"]},
            {"GroupId": "sg-web", "IpPermissions": [permission(443, 443)]},
            {"GroupId": "sg-db", "IpPermissions": [permission(5432, 5432)]},
            # Opens everything, but nothing uses it.
            {
                "GroupId": "sg-unused",
                "IpPermissions": [permission(-1, -1, protocol="-1")],
            },
        ],
        interfaces=[
            {"Groups": [{"GroupId": "sg-ssh"}, {"GroupId": "sg-web"}]},
            {"Groups": [{"GroupId": "sg-db"}, "junk"]},
        ],
        databases=[
            {
                "DBInstanceIdentifier": "reporting",
                "PubliclyAccessible": True,
                "Endpoint": {"Port": 5432},
                "VpcSecurityGroups": [
                    {"VpcSecurityGroupId": "sg-db", "Status": "active"}
                ],
            },
            {
                "DBInstanceIdentifier": "orders",
                "PubliclyAccessible": False,
                "Endpoint": {"Port": 5432},
                "VpcSecurityGroups": [{"VpcSecurityGroupId": "sg-db"}],
            },
            {
                "DBInstanceIdentifier": "legacy",
                "PubliclyAccessible": True,
                "Endpoint": {"Port": 3306},
                "VpcSecurityGroups": [{"VpcSecurityGroupId": "sg-db"}],
            },
        ],
        findings=[
            {"Severity": {"Label": "CRITICAL"}},
            {"Severity": {"Label": "HIGH"}},
            {"Severity": {"Label": "HIGH"}},
            "junk",
        ],
    )
    sydney = FakeNetwork(
        groups=[
            {
                "GroupId": "sg-rdp",
                "IpPermissions": [permission(3389, 3389, "::/0", v6=True)],
            }
        ],
        interfaces=[{"Groups": [{"GroupId": "sg-rdp"}]}],
    )
    return {"us-east-1": east, "ap-southeast-2": sydney}


def test_network_checks_read_every_enabled_region(monkeypatch):
    monkeypatch.setattr(settings, "aws_regions", "")
    regions = regions_fixture()
    ports, databases = aws.network(NetworkSession(regions))
    assert ports.status == "attention"
    assert (
        ports.detail == "2 of 4 security groups in use admit SSH or RDP from anywhere"
    )
    assert ports.subjects == ["ap-southeast-2/sg-rdp", "us-east-1/sg-ssh"]
    assert databases.detail == "1 of 3 RDS instances accept connections from anywhere"
    assert databases.subjects == ["us-east-1/reporting"]
    east = regions["us-east-1"]
    assert ("regions", {"AllRegions": False}) in east.calls
    assert next(o for n, o in east.calls if n == "databases")["MaxRecords"] == 100
    # Every client is closed, and listings are paged to the end.
    assert east.closed == 3 and regions["ap-southeast-2"].closed == 2
    assert len([1 for n, _ in east.calls if n == "groups"]) == 4


def test_network_regions_come_from_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "aws_regions", "ap-southeast-2")
    regions = regions_fixture()
    ports, _ = aws.network(NetworkSession(regions))
    assert ports.subjects == ["ap-southeast-2/sg-rdp"]
    assert not any(n == "regions" for n, _ in regions["us-east-1"].calls)
    for bad in ("../x", ",".join(["us-east-1"] * 41)):
        monkeypatch.setattr(settings, "aws_regions", bad)
        with pytest.raises(Unavailable) as caught:
            aws.network_regions_setting()
        assert caught.value.reason == "not_configured"


def test_network_listings_and_regions_are_bounded(monkeypatch):
    monkeypatch.setattr(settings, "aws_regions", "")
    monkeypatch.setattr(aws, "NETWORK_PAGES", 1)
    with pytest.raises(Unavailable):
        aws.network(NetworkSession(regions_fixture()))
    monkeypatch.setattr(aws, "NETWORK_PAGES", 50)
    monkeypatch.setattr(aws, "MAX_REGIONS", 1)
    with pytest.raises(Unavailable):
        aws.network(NetworkSession(regions_fixture()))


@pytest.mark.parametrize(
    "group, port, expected",
    [
        ({"IpPermissions": [permission(22, 22)]}, 22, True),
        ({"IpPermissions": [permission(22, 22, "10.0.0.0/8")]}, 22, False),
        ({"IpPermissions": [permission(22, 22, protocol="udp")]}, 22, False),
        ({"IpPermissions": [permission(0, 0, protocol="-1")]}, 3389, True),
        (
            {
                "IpPermissions": [
                    {"IpProtocol": "tcp", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
                ]
            },
            22,
            False,
        ),
        ({"IpPermissions": [permission(22, 22)]}, None, False),
        ({}, 22, False),
    ],
)
def test_security_groups_allow_only_what_their_rules_allow(group, port, expected):
    assert aws.reaches(group, port) is expected


def test_findings_count_active_critical_and_high(monkeypatch):
    regions = regions_fixture()
    check = aws.findings(NetworkSession(regions))
    assert check.status == "attention"
    assert check.detail == "1 critical and 2 high findings"
    filters = next(o for n, o in regions["us-east-1"].calls if n == "findings")[
        "Filters"
    ]
    assert filters["AwsAccountId"] == [{"Value": ACCOUNT, "Comparison": "EQUALS"}]
    assert filters["RecordState"] == [{"Value": "ACTIVE", "Comparison": "EQUALS"}]
    assert {f["Value"] for f in filters["WorkflowStatus"]} == {"NEW", "NOTIFIED"}
    monkeypatch.setattr(aws, "FINDINGS_PAGES", 2)
    check = aws.findings(NetworkSession(regions_fixture()))
    assert check.detail == "At least 1 critical and 1 high findings"
    quiet = aws.findings(NetworkSession({"us-east-1": FakeNetwork()}))
    assert quiet.status == "met"
    hub = FakeNetwork()
    hub.get_findings = lambda **o: (_ for _ in ()).throw(
        error("InvalidAccessException")
    )
    with pytest.raises(Unavailable) as caught:
        aws.findings(NetworkSession({"us-east-1": hub}))
    assert caught.value.reason == "not_configured"
    assert hub.closed == 1


# --- sessions ---------------------------------------------------------------


class FakeClient:
    def __init__(self, endpoint="https://sts.amazonaws.com", **answers):
        self.meta = SimpleNamespace(endpoint_url=endpoint)
        self.answers = answers
        self.closed = False
        self.seen = []

    def __getattr__(self, name):
        answer = self.answers[name]

        def call(**kwargs):
            self.seen.append((name, kwargs))
            if isinstance(answer, Exception):
                raise answer
            return answer

        return call

    def close(self):
        self.closed = True


class FakeBoto:
    """Records each boto3.Session made and hands out its clients."""

    def __init__(
        self, caller=ACCOUNT, endpoint="https://sts.amazonaws.com", assume=None
    ):
        self.sessions = []
        self.caller = caller
        self.endpoint = endpoint
        self.assume = assume

    def Session(self, **kwargs):  # noqa: N802 - mirrors boto3
        fake = self

        class Made:
            def __init__(self):
                self.kwargs = kwargs
                self.clients = []

            def client(self, service, **options):
                answers = {
                    "get_caller_identity": {"Account": fake.caller},
                    "assume_role": fake.assume
                    or {
                        "Credentials": {
                            "AccessKeyId": "ASIAROLE",
                            "SecretAccessKey": "role-secret",
                            "SessionToken": "role-token",
                        }
                    },
                }
                client = FakeClient(fake.endpoint, **answers)
                self.clients.append((service, options, client))
                return client

        made = Made()
        self.sessions.append(made)
        return made


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(settings, "aws_access_key_id", "AKIA")
    monkeypatch.setattr(settings, "aws_secret_access_key", "base-secret")
    monkeypatch.setattr(settings, "aws_session_token", "")
    monkeypatch.setattr(settings, "aws_external_id", "")
    monkeypatch.setattr(settings, "aws_region", "us-east-1")
    monkeypatch.setattr(settings, "aws_role_arns", "")


def test_blank_keys_use_the_host_role_chain(monkeypatch, keys):
    boto = FakeBoto()
    monkeypatch.setattr(aws, "boto3", boto)
    monkeypatch.setattr(settings, "aws_access_key_id", "")
    monkeypatch.setattr(settings, "aws_secret_access_key", "")
    session = aws.account_session(aws._base_session(), None)
    assert boto.sessions[0].kwargs == {}
    assert session.account_id == ACCOUNT
    service, options, client = boto.sessions[0].clients[0]
    assert service == "sts" and options["region_name"] == "us-east-1"
    assert (
        options["config"].connect_timeout == 10 and options["config"].read_timeout == 30
    )
    assert client.closed
    monkeypatch.setattr(settings, "aws_secret_access_key", "half")
    with pytest.raises(Unavailable):
        aws._base_session()


def test_roles_are_assumed_with_the_external_id_and_the_account_confirmed(
    monkeypatch, keys
):
    boto = FakeBoto()
    monkeypatch.setattr(aws, "boto3", boto)
    monkeypatch.setattr(settings, "aws_external_id", "keen-sub")
    role = f"arn:aws:iam::{ACCOUNT}:role/Keen"
    aws.account_session(aws._base_session(), role)
    base, assumed = boto.sessions
    assert base.kwargs["aws_access_key_id"] == "AKIA"
    assert base.kwargs["aws_session_token"] is None
    name, options = base.clients[0][2].seen[0]
    assert name == "assume_role"
    assert options["ExternalId"] == "keen-sub" and options["DurationSeconds"] == 900
    assert assumed.kwargs["aws_session_token"] == "role-token"
    boto.caller = "999999999999"
    with pytest.raises(Unavailable) as caught:
        aws.account_session(aws._base_session(), role)
    assert caught.value.reason == "not_configured"


def test_failed_role_assumption_is_a_reason_without_provider_text(monkeypatch, keys):
    monkeypatch.setattr(aws, "boto3", FakeBoto(assume=error("AccessDenied")))
    with pytest.raises(Unavailable) as caught:
        aws.account_session(aws._base_session(), f"arn:aws:iam::{ACCOUNT}:role/Keen")
    assert caught.value.reason == "permission_denied"
    assert "secret detail" not in str(caught.value)


def test_plain_http_endpoints_are_refused(monkeypatch, keys):
    boto = FakeBoto(endpoint="http://localhost:4566")
    monkeypatch.setattr(aws, "boto3", boto)
    with pytest.raises(Unavailable):
        aws.account_session(aws._base_session(), None)
    assert boto.sessions[0].clients[0][2].closed


def test_bad_settings_are_configuration_problems(monkeypatch, keys):
    monkeypatch.setattr(aws, "boto3", FakeBoto())
    monkeypatch.setattr(settings, "aws_region", "not a region")
    with pytest.raises(Unavailable):
        aws.account_session(aws._base_session(), None)
    monkeypatch.setattr(settings, "aws_region", "us-east-1")
    monkeypatch.setattr(settings, "aws_external_id", "bad id!")
    with pytest.raises(Unavailable):
        aws.account_session(aws._base_session(), f"arn:aws:iam::{ACCOUNT}:role/Keen")
    for value in (
        "arn:aws:iam::12:role/Keen",
        f"arn:aws:iam::{ACCOUNT}:role/A,arn:aws:iam::{ACCOUNT}:role/A",
    ):
        monkeypatch.setattr(settings, "aws_role_arns", value)
        with pytest.raises(Unavailable):
            aws.roles()
    monkeypatch.setattr(
        settings,
        "aws_role_arns",
        f"arn:aws:iam::{ACCOUNT}:role/A\n arn:aws:iam::444455556666:role/B",
    )
    assert len(aws.roles()) == 2


@pytest.mark.parametrize(
    "exc, reason",
    [
        (error("AccessDenied"), "permission_denied"),
        (error("InvalidClientTokenId"), "not_configured"),
        (error("Throttling"), "unavailable"),
        (NoCredentialsError(), "not_configured"),
        (EndpointConnectionError(endpoint_url="https://iam"), "unavailable"),
    ],
)
def test_call_failures_become_reasons(exc, reason):
    def fail():
        raise exc

    with pytest.raises(Unavailable) as caught:
        aws.call(fail)
    assert caught.value.reason == reason


def test_collect_marks_an_unreachable_account_and_reads_the_others(monkeypatch, keys):
    good, bad = (
        f"arn:aws:iam::{ACCOUNT}:role/Keen",
        "arn:aws:iam::444455556666:role/Keen",
    )
    monkeypatch.setattr(settings, "aws_role_arns", f"{good},{bad}")

    def session(_base, role):
        if role == bad:
            raise Unavailable("permission_denied")
        return FakeSession()

    monkeypatch.setattr(aws, "account_session", session)
    monkeypatch.setattr(aws, "_base_session", lambda: object())
    monkeypatch.setattr(aws, "network_regions", lambda _aws: [])
    first, second = aws.collect()
    assert first.key == ACCOUNT and first.label == "acme-prod"
    assert {c.key for c in first.checks} == set(aws.CHECKS)
    assert second.key == "444455556666"
    assert {c.status for c in second.checks} == {"unknown"}
    assert {c.reason for c in second.checks} == {"permission_denied"}
    # Without roles, a failure of the credentials' own account fails the run.
    monkeypatch.setattr(settings, "aws_role_arns", "")
    monkeypatch.setattr(
        aws,
        "account_session",
        lambda *_: (_ for _ in ()).throw(Unavailable("not_configured")),
    )
    with pytest.raises(Unavailable):
        aws.collect()


# --- CloudTrail activity -------------------------------------------------------


def test_trail_events_become_activity_entries():
    when = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
    entry = aws.trail_entry(trail_event("ConsoleLogin", when, mfa="No"), ACCOUNT)
    assert entry.action == "ConsoleLogin" and entry.outcome == "success"
    assert entry.at == datetime(2026, 10, 1, 9, 30)
    assert entry.actor == f"arn:aws:iam::{ACCOUNT}:user/alice"
    assert entry.detail == {
        "service": "signin",
        "region": "us-east-1",
        "identity_type": "IAMUser",
        "source_ip": "192.0.2.10",
        "mfa_used": False,
    }
    assert entry.summary == "ConsoleLogin by alice (success)"
    failed = aws.trail_entry(
        trail_event("ConsoleLogin", when, result="Failure"), ACCOUNT
    )
    assert failed.outcome == "failure"
    root = aws.trail_entry(
        trail_event("ConsoleLogin", when, arn=False, kind="Root"), ACCOUNT
    )
    assert root.actor == f"arn:aws:iam::{ACCOUNT}:root"
    change = trail_event(
        "CreateUser", when, source="iam.amazonaws.com", error_code="AccessDenied"
    )
    entry = aws.trail_entry(change, ACCOUNT)
    assert entry.outcome == "failure" and entry.detail["error_code"] == "AccessDenied"
    for skipped in (
        trail_event("GetUser", when, source="iam.amazonaws.com", read_only=True),
        trail_event("CheckMfa", when),
        trail_event("RunInstances", when, source="ec2.amazonaws.com"),
    ):
        assert aws.trail_entry(skipped, ACCOUNT) is None
    # An oversized record keeps only the lookup's own fields.
    oversized = trail_event("ConsoleLogin", when)
    oversized["CloudTrailEvent"] = "x" * (aws.MAX_TRAIL_RECORD + 1)
    kept = aws.trail_entry(oversized, ACCOUNT)
    assert (kept.outcome, kept.raw, kept.detail) == ("info", {}, {"service": "signin"})


def test_activity_reads_sign_in_regions_and_iam_changes(monkeypatch, keys):
    monkeypatch.setattr(aws, "LOOKUP_PAUSE", 0.0)
    since = datetime(2026, 10, 1)
    until = since + timedelta(hours=6)
    at = since.replace(tzinfo=timezone.utc)
    trails = {
        "us-east-1": FakeCloudTrail(
            events={
                "signin.amazonaws.com": [
                    trail_event("ConsoleLogin", at + timedelta(hours=2), event_id="a"),
                    trail_event(
                        "ConsoleLogin", at + timedelta(hours=7), event_id="late"
                    ),
                ],
                "iam.amazonaws.com": [
                    trail_event(
                        "CreateUser",
                        at + timedelta(hours=1),
                        source="iam.amazonaws.com",
                        event_id="b",
                    ),
                ],
            }
        ),
        "*": FakeCloudTrail(),
    }
    session = FakeSession(trails=trails)
    monkeypatch.setattr(aws, "account_session", lambda *_: session)
    monkeypatch.setattr(aws, "_base_session", lambda: object())
    entries, complete = aws.activity(since, until)
    assert complete is True
    assert [e.id for e in entries] == ["b", "a"]
    regions = [r for s, r in session.made if s == "cloudtrail"]
    assert regions == [*aws.SIGNIN_REGIONS, "us-east-1"]
    assert trails["us-east-1"].closed


def test_activity_reports_an_incomplete_window(monkeypatch, keys):
    monkeypatch.setattr(aws, "LOOKUP_PAUSE", 0.0)
    monkeypatch.setattr(aws, "MAX_ACTIVITY", 3)
    since = datetime(2026, 10, 1)
    at = since.replace(tzinfo=timezone.utc)
    events = [
        trail_event("ConsoleLogin", at + timedelta(minutes=m), event_id=str(m))
        for m in range(10)
    ]
    session = FakeSession(
        trails={"*": FakeCloudTrail(events={"signin.amazonaws.com": events})}
    )
    monkeypatch.setattr(aws, "account_session", lambda *_: session)
    monkeypatch.setattr(aws, "_base_session", lambda: object())
    entries, complete = aws.activity(since, since + timedelta(hours=1))
    assert complete is False
    assert len(entries) == 3
    # The newest entries are kept, oldest first.
    assert [e.id for e in entries] == ["7", "8", "9"]
    monkeypatch.setattr(aws, "MAX_ACTIVITY", 1000)
    monkeypatch.setattr(aws, "LOOKUP_PAGES", 1)
    entries, complete = aws.activity(since, since + timedelta(hours=1))
    assert complete is False and len(entries) == 2


def test_global_regions_follow_the_partition():
    assert aws.global_region("ap-southeast-2") == "us-east-1"
    assert aws.global_region("us-gov-east-1") == "us-gov-west-1"
    assert aws.global_region("cn-northwest-1") == "cn-north-1"


def test_last_use_is_the_latest_of_password_and_keys():
    iam = FakeIam(
        rows=[
            row(
                "carol",
                password_enabled="TRUE",
                password_last_used=ago(60),
                access_key_1_active="TRUE",
                access_key_1_last_rotated=ago(10),
                access_key_1_last_used_date=ago(3),
            )
        ],
        details={
            "UserDetailList": [],
            "GroupDetailList": [],
            "RoleDetailList": [],
            "Policies": [],
        },
    )
    _, people = aws.identity_checks(FakeSession(iam), NOW)
    (carol,) = people
    assert (NOW - carol.last_used).days == 3
    assert carol.detail == "Console and 1 access key"


def test_help_documents_every_action_check_and_setting():
    import html
    import json
    import re
    from pathlib import Path

    page = Path(__file__).resolve().parents[2] / "ui/public/help/administrator-aws.html"
    text = page.read_text()
    blocks = [
        html.unescape(b)
        for b in re.findall(r"<pre><code>(.*?)</code></pre>", text, re.S)
    ]
    policy = next(json.loads(b) for b in blocks if "KeenPostureReadOnly" in b)
    (statement,) = policy["Statement"]
    assert statement["Action"] == list(aws.PERMISSIONS)
    for key, label in aws.CHECKS.items():
        assert label in text and f"posture.{key}" in text, key
    for name in (
        "KEEN_AWS_ENABLED",
        "AWS_POSTURE_ACCESS_KEY_ID",
        "AWS_POSTURE_SECRET_ACCESS_KEY",
        "AWS_POSTURE_SESSION_TOKEN",
        "AWS_POSTURE_EXTERNAL_ID",
        "KEEN_AWS_ROLE_ARNS",
        "KEEN_AWS_REGION",
        "KEEN_AWS_REGIONS",
    ):
        assert f"<code>{name}</code>" in text, name
    for region in aws.SIGNIN_REGIONS:
        assert f"<code>{region}</code>" in text


# --- edge cases -------------------------------------------------------------------


def test_unreadable_report_values_and_documents_are_tolerated():
    assert parse_time("not a date") is None
    assert parse_time("2026-10-01T09:30:00+10:00") == datetime(2026, 9, 30, 23, 30)
    iam = FakeIam()
    iam.content = report(default_rows()).decode()
    assert aws.credential_report(iam)
    iam.content = b"\xff\xfe"
    with pytest.raises(Unavailable):
        aws.credential_report(iam)
    assert aws._trail_detail("{not json") == {}
    assert aws._trail_detail("[]") == {}
    assert aws._email("alice", [{"Key": "Email", "Value": "not an address"}]) == ""
    assert aws._email("alice", ["not a tag", {"Key": "mail", "Value": "a@b.org"}]) == (
        "a@b.org"
    )


def test_password_policy_and_alias_failures(monkeypatch):
    with pytest.raises(Unavailable) as caught:
        aws.password_policy(
            FakeIam(fail={"get_account_password_policy": "AccessDenied"})
        )
    assert caught.value.reason == "permission_denied"

    class Broken(FakeIam):
        def get_account_password_policy(self):
            raise EndpointConnectionError(endpoint_url="https://iam.amazonaws.com")

    with pytest.raises(Unavailable):
        aws.password_policy(Broken())
    session = FakeSession(FakeIam(fail={"list_account_aliases": "AccessDenied"}))
    assert aws._alias(session) == ""


def test_signing_certificates_and_policies_granting_everything():
    check = aws.root_check(
        {
            "AccountMFAEnabled": 1,
            "AccountAccessKeysPresent": 0,
            "AccountSigningCertificatesPresent": 1,
        },
        None,
    )
    assert check.detail == "Root user: it has signing certificates"
    details = {
        "Policies": [
            {
                "Arn": "arn:aws:iam::111122223333:policy/Everything",
                "PolicyVersionList": [
                    {
                        "IsDefaultVersion": True,
                        "Document": {
                            "Statement": [
                                {"Effect": "Allow", "Action": "*", "Resource": "*"}
                            ]
                        },
                    }
                ],
            },
            {"Arn": "arn:aws:iam::111122223333:policy/Old", "PolicyVersionList": []},
        ],
        "GroupDetailList": [],
        "UserDetailList": [
            {
                "Arn": "arn:aws:iam::111122223333:user/ops",
                "AttachedManagedPolicies": [
                    {"PolicyArn": "arn:aws:iam::111122223333:policy/Everything"}
                ],
            },
            {
                "Arn": "arn:aws:iam::111122223333:user/viewer",
                "AttachedManagedPolicies": [
                    {"PolicyArn": "arn:aws:iam::aws:policy/ReadOnlyAccess"}
                ],
            },
        ],
        "RoleDetailList": [],
    }
    users, roles = aws.administrators(details)
    assert users == {"arn:aws:iam::111122223333:user/ops"} and roles == []


def test_trails_without_an_arn_and_s3_endpoint_failures():
    class Trails(FakeCloudTrail):
        def describe_trails(self, **options):
            return {"trailList": [{"Name": "no-arn"}, "not a trail"]}

    session = FakeSession(FakeIam(), trails=Trails())
    assert aws.audit_trail(session).detail.startswith("No multi-region trail")

    class Broken(FakeS3Control):
        def get_public_access_block(self, AccountId):  # noqa: N803 - mirrors boto3
            raise EndpointConnectionError(endpoint_url="https://s3-control")

    with pytest.raises(Unavailable):
        aws.public_access(FakeSession(FakeIam(), control=Broken()))
    group = {"IpPermissions": ["not a permission"]}
    assert aws.reaches(group, 22) is False


def test_password_events_and_incomplete_lookups():
    when = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
    changed = aws.trail_entry(trail_event("PasswordUpdated", when), ACCOUNT)
    assert changed.outcome == "success"
    failed = aws.trail_entry(
        trail_event("PasswordUpdated", when, error_code="Denied"), ACCOUNT
    )
    assert failed.outcome == "failure"
    naive = aws.trail_entry(
        trail_event("ConsoleLogin", when.replace(tzinfo=None)), ACCOUNT
    )
    assert naive.at == datetime(2026, 10, 1, 9, 30)
    assert parse_time("2026-10-01T09:30:00") == datetime(2026, 10, 1, 9, 30)
    unnamed = trail_event("ConsoleLogin", when)
    unnamed["EventTime"] = "not a time"
    assert aws.trail_entry(unnamed, ACCOUNT) is None

    class Lookups:
        def __init__(self):
            self.calls = 0

        def lookup_events(self, **options):
            self.calls += 1
            return {
                "Events": ["not an event", trail_event("ConsoleLogin", when)],
                "NextToken": "more",
            }

    client = Lookups()
    found, pages, complete = aws._lookup(
        client, "signin.amazonaws.com", NOW, NOW, ACCOUNT, room=0, pages=3
    )
    assert (len(found), pages, complete) == (1, 1, False)
