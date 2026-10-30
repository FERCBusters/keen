"""In-memory AWS IAM answers for cloud posture tests; no network is used."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

ACCOUNT = "111122223333"
COLUMNS = (
    "user arn user_creation_time password_enabled password_last_used "
    "password_last_changed password_next_rotation mfa_active "
    "access_key_1_active access_key_1_last_rotated access_key_1_last_used_date "
    "access_key_1_last_used_region access_key_1_last_used_service "
    "access_key_2_active access_key_2_last_rotated access_key_2_last_used_date "
    "access_key_2_last_used_region access_key_2_last_used_service "
    "cert_1_active cert_1_last_rotated cert_2_active cert_2_last_rotated"
).split()
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ago(days):
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def row(name, **values):
    base = {c: "N/A" for c in COLUMNS}
    base.update(
        user=name,
        arn=f"arn:aws:iam::{ACCOUNT}:user/{name}"
        if name != "<root_account>"
        else f"arn:aws:iam::{ACCOUNT}:root",
        user_creation_time=ago(400),
        password_enabled="FALSE",
        mfa_active="FALSE",
        access_key_1_active="FALSE",
        access_key_2_active="FALSE",
        cert_1_active="FALSE",
        cert_2_active="FALSE",
    )
    base.update(values)
    return base


def report(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS)
    writer.writeheader()
    for item in rows:
        writer.writerow(item)
    return out.getvalue().encode()


def default_rows():
    return [
        row("<root_account>", password_enabled="TRUE", mfa_active="TRUE"),
        # Console user with MFA and an administrator through a group.
        row(
            "alice@example.org",
            password_enabled="TRUE",
            password_last_used=ago(2),
            mfa_active="TRUE",
        ),
        # Console administrator without MFA.
        row("bob", password_enabled="TRUE", password_last_used=ago(100)),
        # Programmatic user with an old key that has never been used.
        row(
            "deploy-bot",
            access_key_1_active="TRUE",
            access_key_1_last_rotated=ago(500),
        ),
        # Never used at all.
        row("ghost", user_creation_time=ago(30)),
    ]


ADMIN = {
    "PolicyName": "AdministratorAccess",
    "PolicyArn": "arn:aws:iam::aws:policy/AdministratorAccess",
}
EVERYTHING = {
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}],
}


def default_details():
    local = f"arn:aws:iam::{ACCOUNT}:policy/Everything"
    return {
        "UserDetailList": [
            {
                "UserName": "alice@example.org",
                "Arn": f"arn:aws:iam::{ACCOUNT}:user/alice@example.org",
                "GroupList": ["Admins"],
                "AttachedManagedPolicies": [],
                "UserPolicyList": [],
            },
            {
                "UserName": "bob",
                "Arn": f"arn:aws:iam::{ACCOUNT}:user/bob",
                "GroupList": [],
                "AttachedManagedPolicies": [ADMIN],
                "Tags": [{"Key": "email", "Value": "bob@example.org"}],
            },
            {
                "UserName": "deploy-bot",
                "Arn": f"arn:aws:iam::{ACCOUNT}:user/deploy-bot",
                "GroupList": [],
                "AttachedManagedPolicies": [
                    {"PolicyName": "Everything", "PolicyArn": local}
                ],
            },
            {
                "UserName": "ghost",
                "Arn": f"arn:aws:iam::{ACCOUNT}:user/ghost",
                "GroupList": ["Readers"],
                "UserPolicyList": [
                    {
                        "PolicyName": "narrow",
                        "PolicyDocument": {
                            "Statement": {
                                "Effect": "Allow",
                                "Action": "s3:GetObject",
                                "Resource": "*",
                            }
                        },
                    }
                ],
            },
        ],
        "GroupDetailList": [
            {"GroupName": "Admins", "AttachedManagedPolicies": [ADMIN]},
            {"GroupName": "Readers", "AttachedManagedPolicies": []},
        ],
        "RoleDetailList": [
            {
                "RoleName": "AWSReservedSSO_Admin_0123",
                "Arn": f"arn:aws:iam::{ACCOUNT}:role/AWSReservedSSO_Admin_0123",
                "Path": "/aws-reserved/sso.amazonaws.com/",
                "AttachedManagedPolicies": [ADMIN],
                "AssumeRolePolicyDocument": {
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "Federated": "arn:aws:iam::x:saml-provider/y"
                            },
                        }
                    ]
                },
            },
            {
                "RoleName": "lambda-admin",
                "Arn": f"arn:aws:iam::{ACCOUNT}:role/lambda-admin",
                "Path": "/",
                "RolePolicyList": [{"PolicyName": "all", "PolicyDocument": EVERYTHING}],
                "AssumeRolePolicyDocument": {
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                        }
                    ]
                },
            },
            {
                "RoleName": "ci-deployer",
                "Arn": f"arn:aws:iam::{ACCOUNT}:role/ci-deployer",
                "Path": "/",
                "AttachedManagedPolicies": [ADMIN],
                "AssumeRolePolicyDocument": {
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"AWS": "arn:aws:iam::999:root"},
                        }
                    ]
                },
            },
            {
                "RoleName": "AWSServiceRoleForSupport",
                "Arn": f"arn:aws:iam::{ACCOUNT}:role/aws-service-role/x",
                "Path": "/aws-service-role/support.amazonaws.com/",
                "AttachedManagedPolicies": [ADMIN],
            },
            {
                "RoleName": "readonly",
                "Arn": f"arn:aws:iam::{ACCOUNT}:role/readonly",
                "Path": "/",
                "AttachedManagedPolicies": [],
            },
        ],
        "Policies": [
            {
                "PolicyName": "Everything",
                "Arn": local,
                "PolicyVersionList": [
                    {"IsDefaultVersion": False, "Document": {"Statement": []}},
                    {
                        "IsDefaultVersion": True,
                        # IAM returns policy documents URL-encoded.
                        "Document": "%7B%22Statement%22%3A%5B%7B%22Effect%22%3A%22Allow%22%2C%22Action%22%3A%5B%22%2A%22%5D%2C%22Resource%22%3A%22%2A%22%7D%5D%7D",
                    },
                ],
            }
        ],
    }


def error(code):
    return ClientError({"Error": {"Code": code, "Message": "secret detail"}}, "Op")


class FakeIam:
    def __init__(
        self,
        rows=None,
        details=None,
        summary=None,
        policy="default",
        states=("COMPLETE",),
        fail=None,
    ):
        self.content = report(default_rows() if rows is None else rows)
        self.details = default_details() if details is None else details
        self.summary = (
            {
                "AccountMFAEnabled": 1,
                "AccountAccessKeysPresent": 0,
                "AccountSigningCertificatesPresent": 0,
            }
            if summary is None
            else summary
        )
        self.policy = (
            {
                "MinimumPasswordLength": 14,
                "RequireSymbols": True,
                "RequireNumbers": True,
                "RequireUppercaseCharacters": True,
                "RequireLowercaseCharacters": False,
                "AllowUsersToChangePassword": True,
                "HardExpiry": False,
                "PasswordReusePrevention": 5,
            }
            if policy == "default"
            else policy
        )
        self.states = list(states)
        self.fail = fail or {}
        self.closed = False
        self.calls = []

    def _maybe_fail(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise error(self.fail[name])

    def get_account_summary(self):
        self._maybe_fail("get_account_summary")
        return {"SummaryMap": self.summary}

    def generate_credential_report(self):
        self._maybe_fail("generate_credential_report")
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"State": state}

    def get_credential_report(self):
        self._maybe_fail("get_credential_report")
        return {"Content": self.content, "ReportFormat": "text/csv"}

    def get_account_authorization_details(self, **options):
        self._maybe_fail("get_account_authorization_details")
        # Two pages, to exercise the marker loop.
        if options.get("Marker") != "page-2":
            first = {k: v for k, v in self.details.items() if k != "Policies"}
            return {**first, "Policies": [], "IsTruncated": True, "Marker": "page-2"}
        return {
            "UserDetailList": [],
            "GroupDetailList": [],
            "RoleDetailList": [],
            "Policies": self.details["Policies"],
            "IsTruncated": False,
        }

    def get_account_password_policy(self):
        self._maybe_fail("get_account_password_policy")
        if self.policy is None:
            raise error("NoSuchEntity")
        return {"PasswordPolicy": self.policy}

    def list_account_aliases(self):
        self._maybe_fail("list_account_aliases")
        return {"AccountAliases": ["acme-prod"]}

    def close(self):
        self.closed = True


class QuietNetwork:
    """EC2, RDS and Security Hub answers for an account with nothing exposed."""

    def describe_regions(self, **_options):
        return {"Regions": [{"RegionName": "us-east-1"}]}

    def describe_security_groups(self, **_options):
        return {"SecurityGroups": []}

    def describe_network_interfaces(self, **_options):
        return {"NetworkInterfaces": []}

    def describe_db_instances(self, **_options):
        return {"DBInstances": []}

    def get_findings(self, **_options):
        return {"Findings": []}

    def close(self):
        pass


class FakeSession:
    """Stands in for app.ingest.cloud.aws.Session."""

    def __init__(self, iam=None, account=ACCOUNT, trails=None, control=None):
        self.network = QuietNetwork()
        self.iam = iam or FakeIam()
        self.account_id = account
        self.region = "us-east-1"
        # One CloudTrail client per region; "*" answers any region.
        if not isinstance(trails, dict):
            trails = {"*": trails or FakeCloudTrail()}
        self.trails = trails
        self.control = control
        self.made = []

    def client(self, service, region=None):
        self.made.append((service, region))
        if service == "iam":
            return self.iam
        if service == "cloudtrail":
            return self.trails.get(region) or self.trails.setdefault(
                "*", FakeCloudTrail()
            )
        if service in ("ec2", "rds", "securityhub"):
            return self.network
        assert service == "s3control"
        if self.control is None:
            self.control = FakeS3Control()
        return self.control


TRAIL = f"arn:aws:cloudtrail:us-east-1:{ACCOUNT}:trail/main"


class FakeCloudTrail:
    """DescribeTrails, GetTrailStatus and LookupEvents for one region."""

    def __init__(self, trails=None, status=None, events=None, fail=None, page=2):
        self.trails = (
            [
                {
                    "TrailARN": TRAIL,
                    "IsMultiRegionTrail": True,
                    "IsOrganizationTrail": False,
                    "LogFileValidationEnabled": True,
                }
            ]
            if trails is None
            else trails
        )
        self.status = {TRAIL: True} if status is None else status
        self.events = events or {}
        self.fail = fail or {}
        self.page = page
        self.closed = False
        self.calls = []

    def describe_trails(self, **options):
        self.calls.append(("describe_trails", options))
        if "describe_trails" in self.fail:
            raise error(self.fail["describe_trails"])
        return {"trailList": self.trails}

    def get_trail_status(self, Name):  # noqa: N803 - mirrors boto3
        self.calls.append(("get_trail_status", Name))
        if Name in self.fail:
            raise error(self.fail[Name])
        return {"IsLogging": self.status.get(Name, False)}

    def lookup_events(self, **options):
        self.calls.append(("lookup_events", options))
        if "lookup_events" in self.fail:
            raise error(self.fail["lookup_events"])
        source = options["LookupAttributes"][0]["AttributeValue"]
        found = [
            e
            for e in self.events.get(source, [])
            if options["StartTime"] <= e["EventTime"] < options["EndTime"]
        ]
        # Newest first, as CloudTrail answers.
        found.sort(key=lambda e: e["EventTime"], reverse=True)
        start = int(options.get("NextToken") or 0)
        page = found[start : start + self.page]
        out = {"Events": page}
        if start + self.page < len(found):
            out["NextToken"] = str(start + self.page)
        return out

    def close(self):
        self.closed = True


class FakeS3Control:
    def __init__(self, block="default", fail=None):
        self.block = (
            {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
            if block == "default"
            else block
        )
        self.fail = fail
        self.closed = False
        self.seen = []

    def get_public_access_block(self, AccountId):  # noqa: N803 - mirrors boto3
        self.seen.append(AccountId)
        if self.fail:
            raise error(self.fail)
        if self.block is None:
            raise error("NoSuchPublicAccessBlockConfiguration")
        return {"PublicAccessBlockConfiguration": self.block}

    def close(self):
        self.closed = True


def trail_event(
    name,
    when,
    *,
    source="signin.amazonaws.com",
    event_id=None,
    arn=None,
    kind="IAMUser",
    result="Success",
    mfa="Yes",
    read_only=False,
    error_code="",
    region="us-east-1",
):
    detail = {
        "eventName": name,
        "eventSource": source,
        "awsRegion": region,
        "sourceIPAddress": "192.0.2.10",
        "userIdentity": {"type": kind},
    }
    if arn is not False:
        detail["userIdentity"]["arn"] = arn or f"arn:aws:iam::{ACCOUNT}:user/alice"
    if source == "signin.amazonaws.com" and name in ("ConsoleLogin", "SwitchRole"):
        detail["responseElements"] = {name: result}
        detail["additionalEventData"] = {"MFAUsed": mfa}
    if error_code:
        detail["errorCode"] = error_code
    if read_only:
        detail["readOnly"] = True
    return {
        "EventId": event_id or f"{name}-{when.isoformat()}",
        "EventName": name,
        "EventTime": when,
        "EventSource": source,
        "ReadOnly": "true" if read_only else "false",
        "CloudTrailEvent": json.dumps(detail),
    }
