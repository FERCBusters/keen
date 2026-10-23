"""Fictional operational evidence mapped to the ISO 27001:2022 controls it shows.

Each event imitates the shape its Keen collector stores for that source, so
the event page, structured mapping and source views behave as they would with
real collection. Hosts, accounts and addresses use reserved example ranges and
domains. Mappings name the control an auditor would expect the record to
evidence; other frameworks receive the evidence through Keen's crosswalks.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

AWS_ACCOUNT = "111122223333"
AWS_REGION = "eu-west-2"
DOMAIN = "example.invalid"


def _stamp(seeder, days_ago: int, at: tuple[int, int]) -> datetime:
    return seeder._demo_event_timestamp(days_ago, at[0], at[1])


def _iso(value: datetime) -> str:
    return value.isoformat() + "Z"


def _seed(seeder, kind):
    return {"prefix": seeder.prefix, "kind": kind}


# --- Google Workspace admin and audit reports --------------------------------


def google_workspace(
    seeder,
    key,
    days_ago,
    at,
    application,
    names,
    actor,
    params,
    controls,
    *,
    outcome="success",
    severity=3,
    ip="192.0.2.10",
):
    stamp = _stamp(seeder, days_ago, at)
    target = params.get("USER_EMAIL")
    head = f"Google Workspace {application}: {', '.join(names)}"
    summary = " ".join(
        part
        for part in (head, f"target={target}" if target else "", f"by {actor}")
        if part
    )
    activity = {
        "id": {
            "time": _iso(stamp),
            "uniqueQualifier": f"demo-{key}",
            "applicationName": application,
        },
        "actor": {"email": actor},
        "ipAddress": ip,
        "events": [
            {
                "name": name,
                "parameters": [{"name": k, "value": v} for k, v in params.items()],
            }
            for name in names
        ],
    }
    stream = f"{application}-reports"
    return dict(
        key=key,
        source="google_workspace",
        timestamp=stamp,
        system="google-workspace",
        actor=actor,
        action="+".join(names[:3]),
        outcome=outcome,
        severity=severity,
        summary=summary,
        raw_pointer={
            "google_workspace": {
                "stream": stream,
                "application": application,
                "uniqueQualifier": f"demo-{key}",
                "event_names": names,
                "actor": actor,
                "ip": ip,
            }
        },
        normalized_payload={
            "google_workspace": {
                "stream": stream,
                "application": application,
                "activity": activity,
                "parsed": {"event_names": names, "params": params},
                "fetched_at": (stamp + timedelta(minutes=5)).isoformat(),
            },
            "seed": _seed(seeder, "google_workspace"),
        },
        controls=controls,
    )


# (key, days ago, time, application, event names, actor, parameters, controls)
GOOGLE_WORKSPACE = (
    (
        "gws-2sv-enforced",
        88,
        (10, 4),
        "admin",
        ["ENFORCE_STRONG_AUTHENTICATION"],
        f"alex@{DOMAIN}",
        {"ORG_UNIT_NAME": "/", "NEW_VALUE": "ENFORCED"},
        ["A.5.17", "A.8.5"],
    ),
    (
        "gws-external-sharing",
        76,
        (14, 12),
        "admin",
        ["CHANGE_APPLICATION_SETTING"],
        f"alex@{DOMAIN}",
        {
            "APPLICATION_NAME": "Drive and Docs",
            "SETTING_NAME": "Sharing outside of the organisation",
            "OLD_VALUE": "ALLOWED",
            "NEW_VALUE": "ALLOWLISTED_DOMAINS",
        },
        ["A.5.14", "A.8.3", "A.8.12"],
    ),
    (
        "gws-screen-lock",
        69,
        (9, 48),
        "admin",
        ["CHANGE_APPLICATION_SETTING"],
        f"jordan@{DOMAIN}",
        {
            "APPLICATION_NAME": "Device management",
            "SETTING_NAME": "Screen lock timeout",
            "NEW_VALUE": "5 minutes",
        },
        ["A.7.7", "A.8.1"],
    ),
    (
        "gws-drive-labels",
        63,
        (11, 20),
        "admin",
        ["CHANGE_APPLICATION_SETTING"],
        f"casey@{DOMAIN}",
        {
            "APPLICATION_NAME": "Drive labels",
            "SETTING_NAME": "Classification label values",
            "NEW_VALUE": "Public, Internal, Confidential, Restricted",
        },
        ["A.5.12", "A.5.13"],
    ),
    (
        "gws-context-aware-access",
        55,
        (10, 37),
        "admin",
        ["CHANGE_APPLICATION_SETTING"],
        f"alex@{DOMAIN}",
        {
            "APPLICATION_NAME": "Context-aware access",
            "SETTING_NAME": "Access level for Gmail and Drive",
            "NEW_VALUE": "Managed devices only",
        },
        ["A.6.7", "A.8.1"],
    ),
    (
        "gws-oauth-app-blocked",
        51,
        (13, 15),
        "token",
        ["revoke"],
        f"alex@{DOMAIN}",
        {"APP_NAME": "Unverified calendar sync", "SCOPE": "drive"},
        ["A.5.15", "A.5.23"],
    ),
    (
        "gws-mobile-wipe",
        47,
        (19, 41),
        "mobile",
        ["DEVICE_REMOTE_WIPE"],
        f"jordan@{DOMAIN}",
        {"USER_EMAIL": f"riley@{DOMAIN}", "DEVICE_MODEL": "Managed phone"},
        ["A.7.9", "A.8.1"],
    ),
    (
        "gws-phishing-reported",
        33,
        (8, 52),
        "gmail",
        ["report_phishing"],
        f"jordan@{DOMAIN}",
        {
            "MESSAGE_SUBJECT": "Invoice overdue: action required",
            "SENDER": "accounts@invoices.example.net",
        },
        ["A.5.25", "A.6.8"],
    ),
    (
        "gws-phishing-quarantined",
        33,
        (8, 58),
        "gmail",
        ["quarantine_message"],
        f"alex@{DOMAIN}",
        {
            "MESSAGE_SUBJECT": "Invoice overdue: action required",
            "MESSAGES_AFFECTED": "14",
        },
        ["A.5.26", "A.8.7"],
    ),
    (
        "gws-help-desk-role",
        30,
        (16, 5),
        "admin",
        ["ASSIGN_ROLE"],
        f"alex@{DOMAIN}",
        {"ROLE_NAME": "Help desk admin", "USER_EMAIL": f"jordan@{DOMAIN}"},
        ["A.5.15", "A.8.2"],
    ),
    (
        "gws-suspend-contractor",
        21,
        (17, 32),
        "admin",
        ["SUSPEND_USER"],
        f"jordan@{DOMAIN}",
        {"USER_EMAIL": f"morgan@{DOMAIN}"},
        ["A.5.18", "A.6.5"],
    ),
)
# Events that call for attention: (key, days ago, time, application, names,
# actor, params, controls, outcome, severity).
GOOGLE_WORKSPACE_FLAGGED = (
    (
        "gws-dlp-card-numbers",
        41,
        (14, 26),
        "rules",
        ["rule_match"],
        f"riley@{DOMAIN}",
        {
            "RULE_NAME": "Block card numbers in external shares",
            "ACTION": "BLOCK_EXTERNAL_SHARING",
            "RESOURCE_TITLE": "Refund requests export",
        },
        ["A.5.14", "A.8.12"],
        "warning",
        4,
    ),
    (
        "gws-suspicious-login",
        12,
        (3, 17),
        "login",
        ["suspicious_login"],
        f"riley@{DOMAIN}",
        {"LOGIN_TYPE": "google_password", "LOGIN_CHALLENGE_METHOD": "security_key"},
        ["A.5.25", "A.8.5", "A.8.16"],
        "failure",
        6,
    ),
)


# --- AWS CloudTrail and service events through CloudWatch Logs ----------------


def cloudwatch(
    seeder,
    key,
    days_ago,
    at,
    query,
    log_group,
    record,
    summary,
    actor,
    action,
    controls,
    *,
    outcome="success",
    severity=3,
):
    stamp = _stamp(seeder, days_ago, at)
    ms = int((stamp - datetime(1970, 1, 1)).total_seconds() * 1000)
    stream = f"{AWS_ACCOUNT}_{query}_{AWS_REGION}"
    message = json.dumps(record, sort_keys=True)
    extracted = {
        k: record[k] for k in ("eventName", "eventSource") if record.get(k) is not None
    }
    if actor:
        extracted["actor"] = actor
    return dict(
        key=key,
        source="cloudwatch_logs",
        timestamp=stamp,
        system=query,
        actor=actor,
        action=action,
        outcome=outcome,
        severity=severity,
        summary=summary,
        raw_pointer={
            "cloudwatch_logs": {
                "name": query,
                "region": AWS_REGION,
                "log_group": log_group,
                "log_stream": stream,
                "event_id": f"demo-{key}",
                "filter_pattern": "",
                "window_start_ms": ms - 300000,
                "window_end_ms": ms + 60000,
            }
        },
        normalized_payload={
            "cloudwatch": {
                "log_group": log_group,
                "log_stream": stream,
                "event_id": f"demo-{key}",
                "timestamp_ms": ms,
                "region": AWS_REGION,
            },
            "message": message,
            "json": record,
            "extracted": extracted,
            "seed": _seed(seeder, "cloudwatch_logs"),
        },
        controls=controls,
    )


def cloudtrail(
    seeder,
    key,
    days_ago,
    at,
    event_source,
    event_name,
    user,
    params,
    controls,
    *,
    outcome="success",
    severity=3,
    extra=None,
):
    record = {
        "eventVersion": "1.09",
        "eventTime": _iso(_stamp(seeder, days_ago, at)),
        "eventSource": event_source,
        "eventName": event_name,
        "eventType": "AwsApiCall",
        "awsRegion": AWS_REGION,
        "sourceIPAddress": "192.0.2.44",
        "userIdentity": {
            "type": "AssumedRole",
            "userName": user,
            "arn": f"arn:aws:sts::{AWS_ACCOUNT}:assumed-role/platform-admin/{user}",
        },
        "requestParameters": params,
        "recipientAccountId": AWS_ACCOUNT,
        **(extra or {}),
    }
    return cloudwatch(
        seeder,
        key,
        days_ago,
        at,
        "cloudtrail-security",
        "/aws/cloudtrail/demo-organisation",
        record,
        f"{event_source} {event_name} by {user}",
        user,
        event_name,
        controls,
        outcome=outcome,
        severity=severity,
    )


CLOUDTRAIL = (
    # (key, days ago, time, event source, event name, user, params, controls)
    (
        "aws-s3-public-access-block",
        72,
        (11, 3),
        "s3.amazonaws.com",
        "PutPublicAccessBlock",
        "sam.example",
        {
            "accountId": AWS_ACCOUNT,
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        },
        ["A.5.23", "A.8.3"],
    ),
    (
        "aws-kms-key-rotation",
        66,
        (15, 40),
        "kms.amazonaws.com",
        "EnableKeyRotation",
        "sam.example",
        {"keyId": "alias/customer-data"},
        ["A.8.24"],
    ),
    (
        "aws-audit-log-object-lock",
        61,
        (10, 18),
        "s3.amazonaws.com",
        "PutObjectLockConfiguration",
        "sam.example",
        {
            "bucketName": "demo-audit-logs",
            "ObjectLockConfiguration": {
                "ObjectLockEnabled": "Enabled",
                "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 400}},
            },
        },
        ["A.5.33", "A.8.15"],
    ),
    (
        "aws-database-multi-az",
        49,
        (21, 30),
        "rds.amazonaws.com",
        "ModifyDBInstance",
        "sam.example",
        {"dBInstanceIdentifier": "customer-db", "multiAZ": True},
        ["A.8.14"],
    ),
    (
        "aws-load-balancer-tls-policy",
        44,
        (9, 12),
        "elasticloadbalancing.amazonaws.com",
        "ModifyListener",
        "sam.example",
        {
            "listenerArn": f"arn:aws:elasticloadbalancing:{AWS_REGION}:{AWS_ACCOUNT}:listener/app/portal/demo",
            "sslPolicy": "ELBSecurityPolicy-TLS13-1-2-2021-06",
        },
        ["A.8.21", "A.8.24"],
    ),
    (
        "aws-staging-network-deny",
        37,
        (16, 48),
        "ec2.amazonaws.com",
        "CreateNetworkAclEntry",
        "sam.example",
        {
            "networkAclId": "acl-prod-database",
            "ruleNumber": 90,
            "ruleAction": "deny",
            "egress": False,
            "cidrBlock": "10.20.0.0/16",
        },
        ["A.8.22", "A.8.31"],
    ),
    (
        "aws-contractor-key-deactivated",
        21,
        (17, 45),
        "iam.amazonaws.com",
        "UpdateAccessKey",
        "sam.example",
        {"userName": "morgan.example", "status": "Inactive"},
        ["A.5.18", "A.8.2"],
    ),
    (
        "aws-snapshot-quota-increase",
        17,
        (7, 20),
        "servicequotas.amazonaws.com",
        "RequestServiceQuotaIncrease",
        "sam.example",
        {"serviceCode": "rds", "quotaName": "Manual DB snapshots", "desiredValue": 200},
        ["A.8.6", "A.8.13"],
    ),
    (
        "aws-ssh-rule-revoked",
        5,
        (9, 12),
        "ec2.amazonaws.com",
        "RevokeSecurityGroupIngress",
        "sam.example",
        {
            "groupId": "sg-app01",
            "ipPermissions": [
                {
                    "ipProtocol": "tcp",
                    "fromPort": 22,
                    "toPort": 22,
                    "cidrIp": "0.0.0.0/0",
                }
            ],
        },
        ["A.8.9", "A.8.20", "A.8.22"],
    ),
)


def aws_findings(seeder):
    """GuardDuty, Config, alarm and WAF records routed to CloudWatch Logs."""
    return [
        cloudwatch(
            seeder,
            "aws-guardduty-ssh-brute-force",
            5,
            (2, 21),
            "guardduty-findings",
            "/aws/events/guardduty",
            {
                "detail-type": "GuardDuty Finding",
                "source": "aws.guardduty",
                "account": AWS_ACCOUNT,
                "detail": {
                    "type": "UnauthorizedAccess:EC2/SSHBruteForce",
                    "severity": 5,
                    "resource": {"instanceDetails": {"instanceId": "i-app01"}},
                    "service": {"remoteIp": "203.0.113.24", "count": 214},
                },
            },
            "GuardDuty UnauthorizedAccess:EC2/SSHBruteForce on app01 from 203.0.113.24",
            None,
            "guardduty_finding",
            ["A.5.25", "A.8.16"],
            outcome="warning",
            severity=5,
        ),
        cloudwatch(
            seeder,
            "aws-config-unencrypted-volume",
            29,
            (6, 10),
            "aws-config-compliance",
            "/aws/events/config",
            {
                "detail-type": "Config Rules Compliance Change",
                "source": "aws.config",
                "detail": {
                    "configRuleName": "encrypted-volumes",
                    "resourceId": "vol-report01",
                    "newEvaluationResult": {"complianceType": "NON_COMPLIANT"},
                },
            },
            "AWS Config encrypted-volumes NON_COMPLIANT for vol-report01 (report01)",
            None,
            "config_compliance_change",
            ["A.8.9", "A.8.24"],
            outcome="warning",
            severity=4,
        ),
        cloudwatch(
            seeder,
            "aws-database-storage-alarm",
            27,
            (4, 55),
            "cloudwatch-alarms",
            "/aws/events/alarms",
            {
                "detail-type": "CloudWatch Alarm State Change",
                "source": "aws.cloudwatch",
                "detail": {
                    "alarmName": "customer-db-free-storage-below-20pc",
                    "state": {"value": "ALARM"},
                    "previousState": {"value": "OK"},
                },
            },
            "CloudWatch alarm customer-db-free-storage-below-20pc entered ALARM",
            None,
            "alarm_state_change",
            ["A.8.6", "A.8.16"],
            outcome="warning",
            severity=4,
        ),
        cloudwatch(
            seeder,
            "aws-waf-sql-injection-blocked",
            15,
            (22, 7),
            "waf-portal",
            "aws-waf-logs-portal",
            {
                "action": "BLOCK",
                "terminatingRuleId": "AWSManagedRulesSQLiRuleSet",
                "httpRequest": {"clientIp": "198.51.100.23", "uri": "/api/search"},
                "blockedRequests": 312,
            },
            "AWS WAF blocked 312 requests matching the SQL injection rule set on the customer portal",
            None,
            "waf_block",
            ["A.8.20", "A.8.21"],
            outcome="warning",
            severity=4,
        ),
    ]


def _backup_record(job_id, state, detail):
    return {
        "detail-type": "Backup Job State Change",
        "source": "aws.backup",
        "account": AWS_ACCOUNT,
        "detail": {
            "backupJobId": job_id,
            "backupVaultName": "demo-immutable-vault",
            "resourceType": "RDS",
            "resourceArn": f"arn:aws:rds:{AWS_REGION}:{AWS_ACCOUNT}:db:customer-db",
            "state": state,
            **detail,
        },
    }


def backup_jobs(seeder):
    """Nightly database backups for six weeks, with one failure and its rerun."""
    events = []
    for days_ago in range(2, 46):
        failed = days_ago == 17
        state = "FAILED" if failed else "COMPLETED"
        detail = (
            {"statusMessage": "Manual DB snapshot quota exceeded"}
            if failed
            else {"backupSizeInBytes": 18_400_000_000 + days_ago * 9_000_000}
        )
        events.append(
            cloudwatch(
                seeder,
                f"aws-backup-customer-db-{days_ago:02d}",
                days_ago,
                (1, 5 + (days_ago * 7) % 40),
                "aws-backup-jobs",
                "/aws/events/backup",
                _backup_record(f"demo-{days_ago:02d}", state, detail),
                f"AWS Backup job {state} for customer-db (RDS) in demo-immutable-vault",
                None,
                "backup_job",
                ["A.8.13", "A.8.16"] if failed else ["A.8.13"],
                outcome="failed" if failed else "success",
                severity=5 if failed else 2,
            )
        )
    events.append(
        cloudwatch(
            seeder,
            "aws-backup-customer-db-17-rerun",
            17,
            (7, 41),
            "aws-backup-jobs",
            "/aws/events/backup",
            _backup_record(
                "demo-17-rerun", "COMPLETED", {"backupSizeInBytes": 18_553_000_000}
            ),
            "AWS Backup job COMPLETED for customer-db (RDS) in demo-immutable-vault after a rerun",
            None,
            "backup_job",
            ["A.8.13"],
        )
    )
    return events


# --- Loki: syslog, OSSEC and service logs ---------------------------------------


def loki(
    seeder,
    key,
    days_ago,
    at,
    host,
    program,
    actor,
    action,
    summary,
    line,
    controls,
    *,
    outcome="info",
    severity=3,
    parsed=None,
):
    stamp = _stamp(seeder, days_ago, at)
    labels = {"job": "varlogs", "host": host, "agent": "alloy", "program": program}
    return dict(
        key=key,
        source="loki",
        timestamp=stamp,
        system=host,
        actor=actor,
        action=action,
        outcome=outcome,
        severity=severity,
        summary=summary,
        raw_pointer={
            "loki": {
                "query_name": "demo-ossec-and-syslog",
                "logql": '{job="varlogs", agent="alloy"}',
                "labels": labels,
                "ts_ns": str(int(stamp.timestamp() * 1_000_000_000)),
            }
        },
        normalized_payload={
            "labels": labels,
            "line": line,
            "parsed": {
                "message": line,
                "system": host,
                "actor": actor,
                "action": action,
                "outcome": outcome,
                "severity": severity,
                **(parsed or {}),
            },
            "ingested_via": "Alloy remote syslog pipeline",
            "seed": _seed(seeder, "loki"),
        },
        controls=controls,
    )


def loki_events(seeder):
    host = "{}.demo.internal".format
    return [
        loki(
            seeder,
            "loki-fail2ban-ban",
            5,
            (2, 15),
            host("app01"),
            "fail2ban",
            "fail2ban",
            "ip_banned",
            "Loki fail2ban app01 banned 203.0.113.24 after repeated SSH failures",
            "fail2ban.actions: NOTICE [sshd] Ban 203.0.113.24",
            ["A.8.16", "A.8.20"],
            outcome="success",
            severity=4,
        ),
        loki(
            seeder,
            "loki-chrony-source",
            9,
            (4, 2),
            host("app01"),
            "chronyd",
            "chronyd",
            "clock_sync",
            "Loki chronyd app01 selected the internal time source",
            "chronyd: Selected source 10.0.0.2 (ntp1.demo.internal)",
            ["A.8.17"],
            outcome="success",
            severity=2,
        ),
        loki(
            seeder,
            "loki-chrony-offset",
            34,
            (6, 44),
            host("db01"),
            "chronyd",
            "chronyd",
            "clock_sync",
            "Loki chronyd db01 corrected a clock offset of 2.41 seconds",
            "chronyd: System clock wrong by 2.410 seconds, adjustment started",
            ["A.8.17"],
            outcome="warning",
            severity=3,
        ),
        loki(
            seeder,
            "loki-clamav-quarantine",
            39,
            (13, 27),
            host("web01"),
            "clamd",
            "clamd",
            "malware_detected",
            "Loki clamd web01 quarantined a malicious upload",
            "clamd: /srv/uploads/tmp/invoice.pdf.exe: Win.Trojan.Agent FOUND; moved to quarantine",
            ["A.5.25", "A.8.7"],
            outcome="success",
            severity=5,
        ),
        loki(
            seeder,
            "loki-privileged-utility",
            22,
            (14, 3),
            host("bastion01"),
            "sudo",
            "sam",
            "sudo_command",
            "Loki sudo bastion01 packet capture run by sam for a support case",
            "sudo: sam : TTY=pts/1 ; PWD=/home/sam ; USER=root ; COMMAND=/usr/sbin/tcpdump -i eth0 -c 500 port 443",
            ["A.8.2", "A.8.15", "A.8.18"],
            outcome="success",
            severity=3,
        ),
        loki(
            seeder,
            "loki-unexpected-package",
            31,
            (10, 50),
            host("app01"),
            "ossec",
            "ossec-logcollector",
            "package_installed",
            "Loki OSSEC app01 new package installed outside configuration management",
            "ossec: Alert level 7, Rule 2902: New dpkg (Debian Package) installed: netcat-openbsd",
            ["A.8.16", "A.8.19"],
            outcome="warning",
            severity=4,
            parsed={
                "ossec": {"rule_id": "2902", "level": 7, "package": "netcat-openbsd"}
            },
        ),
        loki(
            seeder,
            "loki-usb-storage-blocked",
            46,
            (11, 9),
            host("lt-0187"),
            "kernel",
            "endpoint-agent",
            "device_blocked",
            "Loki endpoint lt-0187 blocked a USB storage device",
            "kernel: usb 2-1: usb-storage device blocked by endpoint policy (vendor 0781)",
            ["A.7.10", "A.8.12"],
            outcome="success",
            severity=3,
        ),
        loki(
            seeder,
            "loki-retention-deletion",
            7,
            (2, 30),
            host("db01"),
            "retention-job",
            "retention-job",
            "records_deleted",
            "Loki retention job db01 deleted 42 customer records past their retention period",
            "retention-job: policy=customer-accounts-12m deleted=42 dry_run=false",
            ["A.5.34", "A.8.10"],
            outcome="success",
            severity=2,
        ),
        loki(
            seeder,
            "loki-legacy-tls-refused",
            44,
            (18, 22),
            host("web01"),
            "nginx",
            "nginx",
            "tls_handshake_refused",
            "Loki nginx web01 refused a TLS 1.0 handshake",
            "nginx: SSL_do_handshake() failed (SSL: error:0A000102:unsupported protocol) while SSL handshaking, client: 198.51.100.9",
            ["A.8.21", "A.8.24"],
            outcome="success",
            severity=2,
        ),
        loki(
            seeder,
            "loki-service-credential-rotation",
            52,
            (3, 0),
            host("db01"),
            "vault-agent",
            "vault-agent",
            "credential_rotated",
            "Loki vault-agent db01 rotated the reporting service credential",
            "vault-agent: rotated database credential for role reporting-ro; lease 72h",
            ["A.5.17"],
            outcome="success",
            severity=2,
        ),
    ]


# --- Jenkins builds -------------------------------------------------------------


def jenkins(
    seeder,
    key,
    days_ago,
    at,
    job,
    build,
    result,
    kind,
    label,
    summary,
    controls,
    extra=None,
):
    stamp = _stamp(seeder, days_ago, at)
    lowered = result.lower()
    outcome = {"failure": "failed", "unstable": "warning"}.get(lowered, "success")
    return dict(
        key=key,
        source="jenkins",
        timestamp=stamp,
        system=label,
        actor="jenkins",
        action=kind,
        outcome=outcome,
        severity={"failed": 4, "warning": 4}.get(outcome, 3),
        summary=summary,
        raw_pointer={
            "jenkins": {
                "job": job,
                "build_number": build,
                "url": f"https://jenkins.{DOMAIN}/job/{job}/{build}/",
            }
        },
        normalized_payload={
            "job": job,
            "build": build,
            "label": label,
            "kind": kind,
            "result": result,
            **(extra or {}),
            "seed": _seed(seeder, "jenkins"),
        },
        controls=controls,
    )


def jenkins_events(seeder):
    return [
        jenkins(
            seeder,
            "jenkins-sast-scan",
            36,
            (2, 10),
            "security/sast",
            211,
            "SUCCESS",
            "security-test",
            "security",
            "Jenkins static analysis build 211 passed with no high findings",
            ["A.8.28", "A.8.29"],
            {"findings": {"high": 0, "medium": 2, "low": 9}},
        ),
        jenkins(
            seeder,
            "jenkins-dependency-audit",
            19,
            (2, 40),
            "security/dependency-audit",
            88,
            "UNSTABLE",
            "security-test",
            "security",
            "Jenkins dependency audit build 88 found two high-severity transitive dependencies",
            ["A.5.21", "A.8.8", "A.8.28"],
            {"findings": {"high": 2, "medium": 4}},
        ),
        jenkins(
            seeder,
            "jenkins-dast-staging",
            13,
            (1, 30),
            "security/dast-staging",
            45,
            "SUCCESS",
            "security-test",
            "staging",
            "Jenkins dynamic scan of staging build 45 completed with three medium findings",
            ["A.8.26", "A.8.29"],
            {"findings": {"high": 0, "medium": 3}, "target": "staging"},
        ),
        jenkins(
            seeder,
            "jenkins-infrastructure-scan",
            30,
            (15, 5),
            "infra/terraform-plan",
            402,
            "UNSTABLE",
            "infrastructure-check",
            "infrastructure",
            "Jenkins infrastructure scan build 402 flagged an unencrypted volume on report01",
            ["A.8.9", "A.8.24", "A.8.27"],
            {"check": "CKV_AWS_3", "resource": "aws_ebs_volume.report01"},
        ),
        jenkins(
            seeder,
            "jenkins-container-scan-critical",
            5,
            (11, 0),
            "security/container-scan",
            133,
            "FAILURE",
            "security-test",
            "security",
            "Jenkins container scan build 133 found a critical OpenSSL vulnerability in the keen-api image",
            ["A.8.8"],
            {"findings": {"critical": 1, "high": 1}, "package": "openssl"},
        ),
        jenkins(
            seeder,
            "jenkins-masked-staging-refresh",
            16,
            (23, 10),
            "data/staging-refresh-masked",
            27,
            "SUCCESS",
            "data-refresh",
            "staging",
            "Jenkins masked staging data refresh build 27 masked 18,204 customer rows",
            ["A.8.11", "A.8.31", "A.8.33"],
            {"masked_rows": 18204, "masking_profile": "customer-v3"},
        ),
        jenkins(
            seeder,
            "jenkins-licence-check",
            43,
            (2, 25),
            "security/licence-check",
            64,
            "SUCCESS",
            "compliance-check",
            "security",
            "Jenkins licence check build 64 found no incompatible licences in release dependencies",
            ["A.5.32"],
            {"packages_checked": 412},
        ),
        jenkins(
            seeder,
            "jenkins-approved-deployment",
            11,
            (14, 20),
            "deploy/customer-portal",
            319,
            "SUCCESS",
            "deployment",
            "production",
            "Jenkins production deployment build 319 approved by a second engineer under CHG-2291",
            ["A.5.3", "A.8.32"],
            {"change": "CHG-2291", "approved_by": "alice-security"},
        ),
        jenkins(
            seeder,
            "jenkins-container-scan-clear",
            1,
            (9, 15),
            "security/container-scan",
            135,
            "SUCCESS",
            "security-test",
            "security",
            "Jenkins container scan build 135 confirmed no critical vulnerabilities after the rebuild",
            ["A.8.8"],
            {"findings": {"critical": 0, "high": 0}},
        ),
    ]


def weekly_scans(seeder):
    """Twelve weekly vulnerability scans with an improving trend."""
    criticals = [2, 1, 1, 0, 0, 1, 0, 0, 0, 0, 0, 1]
    highs = [9, 8, 6, 6, 5, 4, 4, 3, 3, 2, 2, 2]
    events = []
    for week in range(12):
        days_ago = 82 - week * 7
        critical, high = criticals[week], highs[week]
        result = "FAILURE" if critical else "UNSTABLE" if high else "SUCCESS"
        build = 301 + week
        events.append(
            jenkins(
                seeder,
                f"jenkins-weekly-vulnerability-scan-{build}",
                days_ago,
                (3, 0),
                "security/weekly-vulnerability-scan",
                build,
                result,
                "vulnerability-scan",
                "security",
                f"Jenkins weekly vulnerability scan build {build}: {critical} critical, {high} high",
                ["A.8.8"],
                {"findings": {"critical": critical, "high": high}},
            )
        )
    return events


def patch_windows(seeder):
    events = []
    for build, days_ago, hosts in ((57, 67, 14), (58, 39, 14), (59, 9, 15)):
        events.append(
            jenkins(
                seeder,
                f"jenkins-monthly-patching-{build}",
                days_ago,
                (22, 30),
                "ops/monthly-patching",
                build,
                "SUCCESS",
                "maintenance",
                "production",
                f"Jenkins monthly patch window build {build} updated {hosts} hosts",
                ["A.8.8", "A.8.32"],
                {"hosts_patched": hosts, "reboots": 3},
            )
        )
    return events


# --- GitHub and Forgejo ---------------------------------------------------------


def github_audit(seeder, key, days_ago, at, action, actor, controls, **fields):
    stamp = _stamp(seeder, days_ago, at)
    entry = {
        "@timestamp": int((stamp - datetime(1970, 1, 1)).total_seconds() * 1000),
        "_document_id": f"demo-{key}",
        "action": action,
        "actor": actor,
        "org": "demo-org",
        **fields,
    }
    parts = ["github org demo-org", action]
    if fields.get("repo"):
        parts.append(f"repo={fields['repo']}")
    if fields.get("user"):
        parts.append(f"target={fields['user']}")
    parts.append(f"by {actor}")
    return dict(
        key=key,
        source="github",
        timestamp=stamp,
        system="org:demo-org",
        actor=actor,
        action=action,
        outcome="info",
        severity=3,
        summary=" ".join(parts),
        raw_pointer={
            "github": {
                "org": "demo-org",
                "audit": True,
                "action": action,
                "id": f"demo-{key}",
            }
        },
        normalized_payload={"audit": entry, "seed": _seed(seeder, "github")},
        controls=controls,
    )


def github_event(
    seeder, key, days_ago, at, etype, actor, repo, summary, payload, controls
):
    stamp = _stamp(seeder, days_ago, at)
    event = {
        "id": f"demo-{key}",
        "type": etype,
        "actor": {"login": actor},
        "repo": {"name": repo},
        "created_at": _iso(stamp),
        "payload": payload,
    }
    owner, name = repo.split("/", 1)
    return dict(
        key=key,
        source="github",
        timestamp=stamp,
        system=repo,
        actor=actor,
        action=etype,
        outcome="success",
        severity=3,
        summary=summary,
        raw_pointer={
            "github": {
                "endpoint": "events",
                "owner": owner,
                "repo": name,
                "id": event["id"],
                "type": etype,
                "created_at": event["created_at"],
            }
        },
        normalized_payload={"github_event": event, "seed": _seed(seeder, "github")},
        controls=controls,
    )


def code_events(seeder):
    portal = "demo-org/customer-portal"
    return [
        github_audit(
            seeder,
            "github-branch-protection",
            74,
            (10, 26),
            "protected_branch.update",
            "alice-security",
            ["A.8.4", "A.8.32"],
            repo=portal,
            branch="main",
            required_approving_review_count=2,
        ),
        github_event(
            seeder,
            "github-codeowners-security-review",
            74,
            (10, 40),
            "PushEvent",
            "alice-security",
            portal,
            "GitHub push requires security team review for authentication and payment code",
            {
                "ref": "refs/heads/main",
                "commits": [
                    {
                        "sha": "4f1c2aa",
                        "message": "CODEOWNERS: security review for auth/ and payments/",
                    }
                ],
            },
            ["A.5.3", "A.8.4", "A.8.25"],
        ),
        github_audit(
            seeder,
            "github-secret-alert-resolved",
            57,
            (12, 2),
            "secret_scanning_alert.resolve",
            "alice-security",
            ["A.5.17", "A.8.4"],
            repo=portal,
            resolution="revoked",
        ),
        github_event(
            seeder,
            "github-outsourced-change-reviewed",
            25,
            (16, 33),
            "PullRequestEvent",
            "alice-security",
            portal,
            "GitHub pull request from the outsourced development partner reviewed and merged",
            {
                "action": "closed",
                "pull_request": {
                    "number": 188,
                    "merged": True,
                    "title": "Add CSV export for invoices",
                    "user": {"login": "partner-dev-agency"},
                    "requested_reviewers": [{"login": "alice-security"}],
                },
            },
            ["A.8.25", "A.8.28", "A.8.30"],
        ),
        github_audit(
            seeder,
            "github-contractor-removed",
            21,
            (17, 40),
            "org.remove_member",
            "sam-example",
            ["A.5.18", "A.8.4"],
            user="morgan-example",
        ),
        github_event(
            seeder,
            "github-patched-base-image",
            4,
            (15, 2),
            "PullRequestEvent",
            "sam-example",
            portal,
            "GitHub pull request merged to rebuild images on the patched OpenSSL base",
            {
                "action": "closed",
                "pull_request": {
                    "number": 214,
                    "merged": True,
                    "title": "Rebuild images on patched OpenSSL base image",
                },
            },
            ["A.8.8", "A.8.32"],
        ),
    ]


def forgejo_events(seeder):
    stamp = _stamp(seeder, 20, (11, 5))
    owner, repo = "mig5", "keen-demo"
    activity = {
        "id": "demo-forgejo-threat-model",
        "op_type": "pull_request",
        "act_user": {"login": "platform-reviewer"},
        "repo": {"owner_name": owner, "name": repo},
        "created": _iso(stamp),
        "content": "Threat model for payment page script monitoring",
        "pull_request": {"number": 81, "title": "Threat model: payment page scripts"},
    }
    host = "git.mig5.net"
    return [
        dict(
            key="forgejo-payment-threat-model",
            source="forgejo",
            timestamp=stamp,
            system=f"{host}/{owner}/{repo}",
            actor="platform-reviewer",
            action="pull_request",
            outcome="success",
            severity=3,
            summary="Forgejo Keen demo pull request adds a threat model for payment page scripts",
            raw_pointer={
                "forgejo": {
                    "host": host,
                    "owner": owner,
                    "repo": repo,
                    "activity_id": activity["id"],
                    "url": f"https://{host}/{owner}/{repo}",
                }
            },
            normalized_payload={
                "activity": activity,
                "feed_url": f"https://{host}/{owner}/{repo}.rss",
                "seed": _seed(seeder, "forgejo"),
            },
            controls=["A.5.8", "A.8.27"],
        )
    ]


# --- Redmine tickets ------------------------------------------------------------

REDMINE_PROJECTS = {
    "it-service-desk": (11, "IT service desk"),
    "hr-operations": (12, "HR operations"),
    "facilities": (13, "Facilities"),
    "change-advisory": (14, "Change advisory"),
    "security": (15, "Security"),
    "privacy": (16, "Privacy"),
    "supplier-onboarding": (17, "Supplier onboarding"),
    "projects": (18, "Projects"),
    "operations": (19, "Operations"),
}
# (key, days ago, time, project, issue, tracker, status, subject, journal note
#  or None, author of the note, controls)
REDMINE = (
    (
        "redmine-access-request",
        84,
        (10, 12),
        "it-service-desk",
        4302,
        "Access request",
        "Closed",
        "Read-only access to the customer data store for Jordan Example",
        "Approved by the line manager and the system owner; granted through the cds-readonly group.",
        "Jordan Example",
        ["A.5.15", "A.5.18"],
    ),
    (
        "redmine-screening-complete",
        79,
        (15, 0),
        "hr-operations",
        2214,
        "New starter",
        "Resolved",
        "New platform engineer: pre-employment screening",
        "Identity, right to work, two references and a basic criminal record check complete.",
        "HR team",
        ["A.6.1"],
    ),
    (
        "redmine-contract-signed",
        78,
        (11, 45),
        "hr-operations",
        2215,
        "New starter",
        "Closed",
        "New platform engineer: contract with security responsibilities and NDA signed",
        None,
        None,
        ["A.6.2", "A.6.6"],
    ),
    (
        "redmine-home-working-checklist",
        77,
        (9, 30),
        "hr-operations",
        2216,
        "New starter",
        "Closed",
        "Home working security checklist completed by a new remote starter",
        None,
        None,
        ["A.6.7"],
    ),
    (
        "redmine-fire-inspection",
        87,
        (13, 10),
        "facilities",
        70,
        "Maintenance",
        "Closed",
        "Six-monthly fire detection and suppression inspection",
        "Inspection passed; certificate attached.",
        "Facilities",
        ["A.7.5"],
    ),
    (
        "redmine-firewall-review",
        73,
        (14, 0),
        "operations",
        498,
        "Review",
        "Closed",
        "Six-monthly firewall rule review",
        "Four unused inbound rules removed; every remaining rule has an owner and a reason.",
        "Sam Example",
        ["A.8.20", "A.8.22"],
    ),
    (
        "redmine-cabinet-relocated",
        71,
        (8, 40),
        "facilities",
        79,
        "Change",
        "Closed",
        "Network cabinet moved away from the kitchen water pipe",
        None,
        None,
        ["A.7.8"],
    ),
    (
        "redmine-badge-review",
        68,
        (10, 5),
        "facilities",
        77,
        "Review",
        "Closed",
        "Quarterly badge access review",
        "Three badges deactivated for leavers and one for a finished supplier contract.",
        "Facilities",
        ["A.5.18", "A.7.2"],
    ),
    (
        "redmine-payments-dpa",
        67,
        (16, 20),
        "supplier-onboarding",
        311,
        "Contract",
        "Closed",
        "Data processing agreement signed with Example Payments",
        None,
        None,
        ["A.5.20", "A.5.34"],
    ),
    (
        "redmine-door-alarm-test",
        62,
        (19, 0),
        "facilities",
        80,
        "Maintenance",
        "Closed",
        "Out-of-hours door alarm test",
        "All perimeter doors reported to the monitoring centre within ten seconds.",
        "Facilities",
        ["A.7.1", "A.7.4"],
    ),
    (
        "redmine-comms-room-reader",
        59,
        (12, 15),
        "facilities",
        81,
        "Change",
        "Closed",
        "Communications room key lock replaced with a badge reader",
        None,
        None,
        ["A.7.2", "A.7.3"],
    ),
    (
        "redmine-payroll-sftp",
        56,
        (15, 35),
        "it-service-desk",
        4250,
        "Change",
        "Closed",
        "Payroll provider transfer moved to SFTP with PGP encryption",
        None,
        None,
        ["A.5.14", "A.8.24"],
    ),
    (
        "redmine-crm-requirements",
        53,
        (11, 0),
        "projects",
        12,
        "Project",
        "In progress",
        "CRM migration: security requirements and data classification agreed",
        None,
        None,
        ["A.5.8", "A.5.12", "A.8.26"],
    ),
    (
        "redmine-cctv-check",
        48,
        (10, 30),
        "facilities",
        84,
        "Maintenance",
        "Closed",
        "CCTV health check",
        "All six cameras recording; 30-day retention confirmed.",
        "Facilities",
        ["A.7.4"],
    ),
    (
        "redmine-laptop-disposal",
        45,
        (14, 45),
        "it-service-desk",
        4288,
        "Disposal",
        "Closed",
        "Six retired laptops collected for disposal",
        "Certificate of data destruction received listing all six serial numbers.",
        "Jordan Example",
        ["A.7.14", "A.8.10"],
    ),
    (
        "redmine-ups-service",
        40,
        (8, 15),
        "facilities",
        85,
        "Maintenance",
        "Closed",
        "UPS service visit and battery replacement",
        "Engineer escorted in the communications room throughout; 22-minute runtime test passed.",
        "Facilities",
        ["A.7.6", "A.7.11", "A.7.13"],
    ),
    (
        "redmine-capacity-review",
        38,
        (14, 10),
        "operations",
        503,
        "Review",
        "Closed",
        "Quarterly capacity review",
        "Database storage is forecast to reach 80% in ten weeks; volume increase scheduled.",
        "Sam Example",
        ["A.8.6"],
    ),
    (
        "redmine-clear-desk-walkthrough",
        35,
        (17, 30),
        "facilities",
        86,
        "Review",
        "Closed",
        "Clear desk and clear screen walkthrough",
        "Three findings: two unlocked screens and one printed customer list; owners reminded.",
        "Alex Example",
        ["A.7.7"],
    ),
    (
        "redmine-tester-nda",
        34,
        (11, 25),
        "supplier-onboarding",
        318,
        "Contract",
        "Closed",
        "NDA signed with the penetration testing supplier",
        None,
        None,
        ["A.5.20", "A.6.6"],
    ),
    (
        "redmine-test-rules-of-engagement",
        26,
        (15, 15),
        "security",
        612,
        "Approval",
        "Closed",
        "Penetration test rules of engagement approved",
        "Production testing limited to Tuesday 22:00-02:00; backups verified beforehand.",
        "Alex Example",
        ["A.8.29", "A.8.34"],
    ),
    (
        "redmine-subject-access-request",
        24,
        (12, 40),
        "privacy",
        41,
        "Request",
        "Closed",
        "Subject access request answered in 19 days",
        None,
        None,
        ["A.5.34"],
    ),
    (
        "redmine-contractor-leaver",
        23,
        (10, 5),
        "it-service-desk",
        4330,
        "Leaver",
        "New",
        "Leaver: Morgan Example (contractor), last day Friday",
        None,
        None,
        ["A.5.18", "A.6.5"],
    ),
    (
        "redmine-contractor-equipment-returned",
        21,
        (16, 10),
        "it-service-desk",
        4330,
        "Leaver",
        "Resolved",
        "Leaver: Morgan Example (contractor), last day Friday",
        "Laptop DEMO-LT-0142 and the access badge returned and checked in.",
        "Jordan Example",
        ["A.5.11", "A.6.5"],
    ),
    (
        "redmine-change-approved",
        11,
        (10, 0),
        "change-advisory",
        2291,
        "Change",
        "Approved",
        "CHG-2291: deploy customer portal 1.2.0",
        "Risk assessed as low; staging tests passed; rollback plan reviewed.",
        "Alice Security",
        ["A.8.32"],
    ),
    (
        "redmine-ssh-incident-closed",
        3,
        (16, 0),
        "security",
        5120,
        "Incident",
        "Closed",
        "Incident: SSH brute force against app01",
        "No successful logins. Source blocked, SSH limited to the bastion and lessons recorded.",
        "Sam Example",
        ["A.5.26", "A.5.27"],
    ),
)


def redmine(
    seeder,
    key,
    days_ago,
    at,
    project,
    issue,
    tracker,
    status,
    subject,
    note,
    author,
    controls,
):
    stamp = _stamp(seeder, days_ago, at)
    project_id, project_name = REDMINE_PROJECTS[project]
    issue_data = {
        "id": issue,
        "project": {"id": project_id, "name": project_name},
        "tracker": {"name": tracker},
        "status": {"name": status},
        "subject": subject,
        "updated_on": _iso(stamp),
    }
    fields = {
        "issue.id": issue,
        "project.id": project_id,
        "tracker.name": tracker,
        "status.name": status,
        "subject": subject,
    }
    payload = {"fields": fields, "issue": issue_data}
    url = f"https://redmine.{DOMAIN}/issues/{issue}"
    summary = f"Redmine #{issue}: {subject}"
    action = "issue.snapshot"
    if note:
        journal_id = issue * 10 + days_ago % 10
        payload["journal"] = {
            "id": journal_id,
            "notes": note,
            "user": {"name": author},
            "created_on": _iso(stamp),
        }
        fields.update({"journal.id": journal_id, "journal.notes": note})
        summary += f": {note}"
        action = "issue.comment"
        url += f"#change-{journal_id}"
    payload["seed"] = _seed(seeder, "redmine")
    return dict(
        key=key,
        source="redmine",
        timestamp=stamp,
        system=project,
        actor=author,
        action=action,
        outcome="info",
        severity=3,
        summary=summary,
        raw_pointer={
            "redmine": {
                "section": "projects",
                "project_selector": project,
                "project_id": project_id,
                "issue_id": issue,
                "url": url,
            }
        },
        normalized_payload=payload,
        controls=controls,
    )


# --- BookStack and Risk Ledger --------------------------------------------------

BOOKSTACK = (
    # (key, days ago, time, book, page slug, page title, actor, preview, controls)
    (
        "bookstack-joiner-checklist",
        81,
        (9, 10),
        "service-desk",
        "joiner-checklist",
        "Service desk joiner checklist",
        "Jordan Example",
        "Create the account from the HR ticket, add role groups, issue an enrolled laptop and book induction training.",
        ["A.5.16", "A.5.37"],
    ),
    (
        "bookstack-failover-runbook",
        50,
        (16, 0),
        "ops-runbooks",
        "database-failover",
        "Database failover",
        "Sam Example",
        "Confirm the standby is current, promote it, repoint the application and record the timings.",
        ["A.5.37", "A.8.14"],
    ),
    (
        "bookstack-severity-matrix",
        32,
        (11, 30),
        "on-call-handbook",
        "incident-severity-matrix",
        "Incident severity matrix",
        "Alex Example",
        "Severity 1 affects customers or personal data; page the incident lead and the data protection officer.",
        ["A.5.25", "A.5.37"],
    ),
    (
        "bookstack-restore-runbook",
        1,
        (10, 30),
        "ops-runbooks",
        "restore-from-immutable-vault",
        "Restore from the immutable vault",
        "Sam Example",
        "Request the snapshot quota in advance; restore into the recovery account, verify checksums and run the application checks.",
        ["A.5.37", "A.8.13"],
    ),
)


def bookstack(seeder, key, days_ago, at, book, slug, title, actor, preview, controls):
    stamp = _stamp(seeder, days_ago, at)
    url = f"https://wiki.{DOMAIN}/books/{book}/page/{slug}"
    page_id = 100 + sum(map(ord, slug)) % 900
    return dict(
        key=key,
        source="bookstack",
        timestamp=stamp,
        system=f"wiki:{book}:{slug}",
        actor=actor,
        action="page_update",
        outcome="info",
        severity=2,
        summary=f"bookstack: page_update page '{title}' ({book}-{slug})",
        raw_pointer={
            "bookstack": {
                "instance": f"https://wiki.{DOMAIN}",
                "page_id": page_id,
                "page_slug": slug,
                "book_slug": book,
                "url": url,
            }
        },
        normalized_payload={
            "bookstack": {
                "instance": "Operations wiki",
                "page_id": page_id,
                "page_slug": slug,
                "page_title": title,
                "book_slug": book,
                "action": "page_update",
                "url": url,
                "updated_at": _iso(stamp),
                "updated_by": actor,
                "preview": preview,
            },
            "seed": _seed(seeder, "bookstack"),
        },
        controls=controls,
    )


RISK_LEDGER = (
    # (key, days ago, time, supplier, snapshot fields, controls)
    (
        "riskledger-payments-assessment",
        66,
        (9, 0),
        "Example Payments",
        {
            "assessmentProgress": 100,
            "compliance": {"PCI DSS": "Level 1 service provider"},
        },
        ["A.5.19", "A.5.22"],
    ),
    (
        "riskledger-identity-assessment",
        29,
        (9, 0),
        "Example Identity Services",
        {"assessmentProgress": 80, "openActions": 2},
        ["A.5.22"],
    ),
    (
        "riskledger-hosting-assessment",
        9,
        (9, 0),
        "Example Hosting",
        {
            "assessmentProgress": 100,
            "compliance": {"ISO 27001": "Certified", "SOC 2": "Type 2"},
        },
        ["A.5.19", "A.5.22"],
    ),
)


def risk_ledger(seeder, key, days_ago, at, supplier, fields, controls):
    stamp = _stamp(seeder, days_ago, at)
    record_id = f"demo-{key}"
    return dict(
        key=key,
        source="riskledger",
        timestamp=stamp,
        system="Risk Ledger",
        actor=None,
        action="supplier.snapshot",
        outcome="info",
        severity=3,
        summary=f"Risk Ledger supplier: {supplier}",
        raw_pointer={
            "riskledger": {
                "organization_id": "demo-organisation",
                "section": "organizations",
                "org": "demo-organisation",
                "kind": "supplier",
                "id": record_id,
            }
        },
        normalized_payload={
            "riskledger": {"id": record_id, "name": supplier, **fields},
            "fields": {
                "organisation.id": "demo-organisation",
                "record.kind": "supplier",
                "record.id": record_id,
            },
            "seed": _seed(seeder, "riskledger"),
        },
        controls=controls,
    )


# --- Diary entries recorded by people -------------------------------------------

DIARY = (
    # (key, days ago, time, summary, details, controls)
    (
        "board_roles",
        118,
        (16, 0),
        "Board approved ISMS roles, responsibilities and the information security policy",
        {"decision": "Approved", "attendees": 5, "next_review": "In twelve months"},
        ["A.5.1", "A.5.2", "A.5.4", "5.1"],
    ),
    (
        "acceptable_use",
        105,
        (12, 0),
        "Acceptable use policy acknowledged by 18 of 19 staff",
        {"outstanding": 1, "follow_up": "Reminder sent to the remaining contractor"},
        ["A.5.10", "A.6.2"],
    ),
    (
        "internal_audit_plan",
        100,
        (14, 30),
        "Internal audit programme approved for the year",
        {"audits_planned": 6, "external_review": "Booked for the third quarter"},
        ["A.5.35", "9.2"],
    ),
    (
        "risk_review",
        95,
        (10, 0),
        "Quarterly risk register review; treatment owners confirmed",
        {"risks_reviewed": 20, "new_risks": 3, "accepted": 1},
        ["6.1", "8.2", "8.3"],
    ),
    (
        "disciplinary_review",
        90,
        (15, 0),
        "Disciplinary procedure reviewed with HR to cover information security breaches",
        {"changes": "Added examples of security breaches and a proportionate response"},
        ["A.6.4"],
    ),
    (
        "authority_contacts",
        86,
        (11, 0),
        "Regulator and law enforcement contact list verified; data protection fee renewed",
        {"contacts_verified": 4},
        ["A.5.5", "A.5.31"],
    ),
    (
        "segregation_review",
        70,
        (10, 30),
        "Segregation of duties review: deployments and payment refunds",
        {"conflicts_found": 1, "action": "Refund approval moved to the finance lead"},
        ["A.5.3"],
    ),
    (
        "licence_review",
        65,
        (14, 0),
        "Annual software licence compliance review",
        {"products_reviewed": 38, "unused_licences_removed": 6},
        ["A.5.32"],
    ),
    (
        "classification_briefing",
        62,
        (11, 30),
        "Information classification briefing delivered to data owners",
        {"attendees": 9},
        ["A.5.12", "A.5.13"],
    ),
    (
        "records_review",
        60,
        (15, 30),
        "Records retention schedule reviewed; financial records archived to write-once storage",
        {"record_types_reviewed": 14},
        ["A.5.33"],
    ),
    (
        "legal_register_review",
        58,
        (10, 0),
        "Legal and regulatory register reviewed; proposed cyber resilience legislation added",
        {"entries": 23, "new_entries": 2},
        ["A.5.31"],
    ),
    (
        "continuity_exercise",
        52,
        (9, 0),
        "Continuity exercise: office unavailable, security controls maintained while working remotely",
        {"participants": 12, "outcome": "Objectives met; two improvements raised"},
        ["A.5.29", "A.5.30"],
    ),
    (
        "objectives_review",
        44,
        (14, 0),
        "Quarterly review of security objectives and effectiveness measures",
        {"objectives_on_track": 4, "objectives_at_risk": 2},
        ["6.2", "9.1"],
    ),
    (
        "forensic_image",
        39,
        (15, 10),
        "Evidence preserved: copy of the quarantined upload and chain-of-custody record",
        {"hash_recorded": True, "custodian": "Security Lead"},
        ["A.5.26", "A.5.28"],
    ),
    (
        "phishing_simulation",
        20,
        (16, 0),
        "Quarterly phishing simulation: 4 of 19 clicked, 12 reported",
        {"click_rate_percent": 21, "report_rate_percent": 63},
        ["A.6.3", "A.6.8"],
    ),
    (
        "penetration_test",
        14,
        (17, 0),
        "External penetration test completed; report received",
        {"high": 1, "medium": 2, "low": 4},
        ["A.5.35", "A.8.29", "A.8.34"],
    ),
    (
        "access_reconciliation",
        14,
        (11, 0),
        "Monthly access reconciliation found no remaining accounts for leavers",
        {"systems_checked": 9, "leavers_this_month": 1},
        ["A.5.16", "A.5.18"],
    ),
    (
        "supplier_service_review",
        10,
        (14, 30),
        "Hosting supplier service review: SLA met, two support actions open",
        {"availability_percent": 99.97, "open_actions": 2},
        ["A.5.22"],
    ),
    (
        "openssl_advisory",
        6,
        (9, 15),
        "Threat intelligence advisory: critical OpenSSL vulnerability under active exploitation",
        {
            "applies": True,
            "urgency": "Patch within 72 hours",
            "owner": "Platform Engineer",
        },
        ["A.5.7", "A.8.8"],
    ),
    (
        "ssh_corrective_action",
        2,
        (12, 0),
        "Corrective action closed: an SSH rule left open after a supplier test",
        {
            "root_cause": "Temporary rule without an expiry",
            "fix": "Rule expiry added to the change checklist",
        },
        ["A.8.32", "10.2"],
    ),
)


def seed_evidence(seeder):
    """Seed the events; returns them by key for stories."""
    specs = [google_workspace(seeder, *row) for row in GOOGLE_WORKSPACE] + [
        google_workspace(seeder, *row[:8], outcome=row[8], severity=row[9])
        for row in GOOGLE_WORKSPACE_FLAGGED
    ]
    specs += [cloudtrail(seeder, *row) for row in CLOUDTRAIL]
    specs += aws_findings(seeder) + backup_jobs(seeder)
    specs += loki_events(seeder) + jenkins_events(seeder)
    specs += weekly_scans(seeder) + patch_windows(seeder)
    specs += code_events(seeder) + forgejo_events(seeder)
    specs += [redmine(seeder, *row) for row in REDMINE]
    specs += [bookstack(seeder, *row) for row in BOOKSTACK]
    specs += [risk_ledger(seeder, *row) for row in RISK_LEDGER]
    events = {}
    for spec in specs:
        key = spec.pop("key")
        events[key] = seeder._seed_source_event(key=key, **spec)
    for key, days_ago, at, summary, details, controls in DIARY:
        events[key] = seeder.seed_diary_entry(
            key, summary, days_ago, details, controls, at=at
        )
    return events


CE, SOC2, PCI = "CYBER-ESSENTIALS:2026", "soc2_tsc", "pci_dss_v4"
# Controls in other frameworks that an event evidences directly, beside its
# ISO 27001 controls. A key ending in "*" applies to every event it prefixes.
ALSO_EVIDENCES = {
    "aws-ssh-rule-revoked": {CE: ["A4.7", "A4.8"]},
    "aws-staging-network-deny": {CE: ["A4.1"]},
    "loki-fail2ban-ban": {CE: ["A4.1.1"]},
    "redmine-firewall-review": {CE: ["A4.6"]},
    "aws-s3-public-access-block": {CE: ["A5.4"]},
    "loki-unexpected-package": {CE: ["A5.1"]},
    "gws-screen-lock": {CE: ["A5.9"]},
    "loki-syslog-unattended-upgrade": {CE: ["A6.4.1"]},
    "jenkins-monthly-patching-*": {CE: ["A6.4.2"]},
    "jenkins-container-scan-critical": {CE: ["A6.5"]},
    "jenkins-dependency-audit": {CE: ["A6.5"]},
    "github-patched-base-image": {CE: ["A6.5", "A6.5.2"]},
    "redmine-access-request": {CE: ["A7.1", "A7.4"]},
    "gws-help-desk-role": {CE: ["A7.5", "A7.8"]},
    "loki-privileged-utility": {CE: ["A7.7"]},
    "gws-suspicious-login": {CE: ["A7.13"]},
    "gws-2sv-enforced": {CE: ["A7.14", "A7.17"], SOC2: ["CC6.1-POF6"]},
    "gws-suspend-contractor": {CE: ["A7.3"], SOC2: ["CC6.2"], PCI: ["8.2"]},
    "github-contractor-removed": {CE: ["A7.3"]},
    "aws-contractor-key-deactivated": {CE: ["A7.3"], PCI: ["8.2"]},
    "loki-clamav-quarantine": {CE: ["A8.1", "A8.3"]},
    "gws-phishing-quarantined": {CE: ["A8.1"], PCI: ["5.4"]},
    "jenkins-weekly-vulnerability-scan-*": {SOC2: ["CC7.1"], PCI: ["11.3"]},
    "aws-backup-customer-db-*": {SOC2: ["A1.2"]},
    "aws-guardduty-ssh-brute-force": {SOC2: ["CC7.2"]},
    "redmine-ssh-incident-closed": {SOC2: ["CC7.4"]},
    "redmine-change-approved": {SOC2: ["CC8.1"], PCI: ["6.5"]},
    "jenkins-approved-deployment": {SOC2: ["CC8.1"], PCI: ["6.5"]},
    "aws-waf-sql-injection-blocked": {PCI: ["6.4"]},
    "gws-dlp-card-numbers": {PCI: ["3.4"]},
    "riskledger-payments-assessment": {SOC2: ["CC9.2"], PCI: ["12.8"]},
    "riskledger-hosting-assessment": {SOC2: ["CC9.2"]},
    "phishing_simulation": {SOC2: ["CC1.4"], PCI: ["12.6"]},
}


def seed_further_mappings(seeder, events):
    """Map events to the controls they evidence in the other frameworks."""
    for pattern, frameworks in ALSO_EVIDENCES.items():
        if pattern.endswith("*"):
            keys = [key for key in events if key.startswith(pattern[:-1])]
        else:
            keys = [pattern]
        for key in keys:
            for framework, refs in frameworks.items():
                seeder.map_event(events[key], refs, framework)
