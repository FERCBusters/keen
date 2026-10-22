"""Fictional stories: evidence, records and notes that explain what happened.

Each story places events from several sources beside the documents, assets,
risks and objectives involved, with notes for the decisions in between. Boxes
sit on the flow chart from left to right in time order, and labelled links
show how one step led to the next.
"""

from datetime import datetime, time, timedelta

from app.db.models import Story, StoryComment, StoryItem, StoryLink
from app.services.entity_changelog import record_entity_changelog
from app.services.stories import MARGIN, summary

# An item: (name, kind, record key, label, note, column, row). A note names
# no record; its third value is its time, as (days ago, (hour, minute)). The
# columns and rows are wider apart than KEEN's arrangement for new items, so
# link labels sit clear of the boxes.
COLUMN, ROW = 300, 170
STORIES = (
    dict(
        title="SSH brute force against app01: from detection to hardening",
        status="closed",
        created=(5, (9, 40)),
        updated=(2, (12, 20)),
        summary=(
            "Overnight, an internet scanner tried hundreds of SSH passwords against app01. "
            "Monitoring caught it within minutes and nothing got in. The investigation found a "
            "firewall rule left open after a supplier test. This story follows the alerts, the "
            "containment and the lasting fix, with the policy, standard and risk behind each step."
        ),
        items=(
            (
                "ossec",
                "event",
                "loki-ossec-ssh-bruteforce",
                "OSSEC raises a brute-force alert",
                "",
                0,
                0,
            ),
            (
                "ban",
                "event",
                "loki-fail2ban-ban",
                "fail2ban blocks the source",
                "Banned within a minute of the alert.",
                1,
                0,
            ),
            (
                "guardduty",
                "event",
                "aws-guardduty-ssh-brute-force",
                "GuardDuty confirms the pattern",
                "214 attempts from one address in 20 minutes.",
                2,
                0,
            ),
            (
                "revoke",
                "event",
                "aws-ssh-rule-revoked",
                "Open SSH rule removed",
                "Port 22 had been open to the internet since a supplier test.",
                3,
                0,
            ),
            (
                "harden",
                "event",
                "loki-ossec-sshd-config-change",
                "SSH configuration tightened",
                "Password authentication disabled; integrity monitoring recorded the approved change.",
                4,
                0,
            ),
            (
                "closed",
                "event",
                "redmine-ssh-incident-closed",
                "Incident closed",
                "",
                5,
                0,
            ),
            (
                "triage",
                "note",
                (5, (3, 5)),
                "Triage: no successful logins",
                "Authentication logs show only failures; no session opened and no account locked out.",
                0,
                1,
            ),
            ("standard", "document", "network_security", "", "", 2, 1),
            (
                "lesson",
                "event",
                "ssh_corrective_action",
                "Corrective action: rule expiry",
                "",
                5,
                1,
            ),
            ("policy", "document", "incident_process", "", "", 0, 2),
            ("asset", "asset", "platform", "", "", 3, 2),
            (
                "risk",
                "risk",
                "admin_access",
                "",
                "Reassessed after the incident; likelihood unchanged.",
                4,
                2,
            ),
        ),
        links=(
            ("ossec", "ban", "blocked by"),
            ("ban", "guardduty", "seen by"),
            ("ossec", "triage", "assessed"),
            ("guardduty", "revoke", "led to"),
            ("standard", "revoke", "requires"),
            ("revoke", "harden", "then"),
            ("revoke", "asset", "changed"),
            ("harden", "closed", "verified"),
            ("closed", "lesson", "lesson"),
            ("policy", "triage", "guided"),
            ("risk", "asset", "threatens"),
        ),
        comments=(
            "Shared with the board at the management review as an example of layered monitoring working as intended.",
        ),
    ),
    dict(
        title="Contractor leaver: every route in closed on the last day",
        status="closed",
        created=(21, (18, 5)),
        updated=(14, (11, 30)),
        summary=(
            "Morgan Example's contract ended early. HR raised the leaver ticket two days ahead, "
            "and the service desk removed access across single sign-on, source control and the "
            "cloud console within 15 minutes of the end of the last day. The equipment came back "
            "the same afternoon, and the monthly reconciliation confirmed that nothing was missed."
        ),
        items=(
            (
                "ticket",
                "event",
                "redmine-contractor-leaver",
                "Leaver ticket raised",
                "",
                0,
                0,
            ),
            (
                "returned",
                "event",
                "redmine-contractor-equipment-returned",
                "Laptop and badge returned",
                "",
                1,
                0,
            ),
            (
                "suspend",
                "event",
                "gws-suspend-contractor",
                "Single sign-on suspended",
                "",
                2,
                0,
            ),
            (
                "github",
                "event",
                "github-contractor-removed",
                "Removed from GitHub",
                "",
                3,
                0,
            ),
            (
                "aws",
                "event",
                "aws-contractor-key-deactivated",
                "Cloud access key deactivated",
                "",
                4,
                0,
            ),
            (
                "reconcile",
                "event",
                "access_reconciliation",
                "Reconciliation: nothing left",
                "",
                5,
                0,
            ),
            ("procedure", "document", "people_security", "", "", 0, 1),
            ("laptops", "asset", "laptops", "", "", 1, 1),
            ("identity", "asset", "identity", "", "", 2, 1),
            (
                "timing",
                "note",
                (21, (17, 50)),
                "All access removed in 15 minutes",
                "From 17:30 to 17:45, checked against the system list in the procedure.",
                4,
                1,
            ),
            ("risk", "risk", "leaver_access", "", "", 3, 2),
            ("objective", "objective", "access_reviews", "", "", 5, 2),
        ),
        links=(
            ("procedure", "ticket", "followed"),
            ("ticket", "returned", "last day"),
            ("returned", "laptops", "returned"),
            ("returned", "suspend", "then"),
            ("suspend", "identity", "account in"),
            ("suspend", "github", "then"),
            ("github", "aws", "then"),
            ("aws", "timing", "all within"),
            ("aws", "reconcile", "checked by"),
            ("risk", "identity", "threatens"),
            ("reconcile", "objective", "evidence for"),
        ),
        comments=(),
    ),
    dict(
        title="Critical OpenSSL vulnerability: from advisory to patched",
        status="open",
        created=(6, (9, 40)),
        updated=(1, (12, 5)),
        summary=(
            "A national advisory reported a critical OpenSSL vulnerability under active "
            "exploitation. The team confirmed the exposure with the weekly and image scans, "
            "rebuilt and deployed the images within two days and verified the fix with a clean "
            "scan. The legacy reporting server still waits for its vendor's fixed build, so the "
            "story stays open."
        ),
        items=(
            ("advisory", "event", "openssl_advisory", "Advisory received", "", 0, 0),
            (
                "weekly",
                "event",
                "jenkins-weekly-vulnerability-scan-312",
                "Weekly scan flags a critical",
                "",
                1,
                0,
            ),
            (
                "image",
                "event",
                "jenkins-container-scan-critical",
                "Image scan confirms OpenSSL",
                "",
                2,
                0,
            ),
            (
                "rebuild",
                "event",
                "github-patched-base-image",
                "Images rebuilt",
                "",
                3,
                0,
            ),
            (
                "deploy",
                "event",
                "jenkins-keen-api-deploy-success",
                "Deployed to production",
                "",
                4,
                0,
            ),
            (
                "clear",
                "event",
                "jenkins-container-scan-clear",
                "Clean rescan",
                "",
                5,
                0,
            ),
            (
                "hosts",
                "event",
                "loki-syslog-unattended-upgrade",
                "Hosts upgraded overnight",
                "",
                4,
                1,
            ),
            (
                "exception",
                "note",
                (1, (12, 0)),
                "Exception: legacy reporting server",
                "report01 waits for the vendor's fixed build. It is isolated from the internet and the exception expires in 14 days.",
                5,
                1,
            ),
            ("procedure", "document", "vulnerability", "", "", 1, 2),
            ("asset", "asset", "web_app", "", "", 2, 2),
            ("risk", "risk", "unpatched_vulnerability", "", "", 3, 2),
        ),
        links=(
            ("advisory", "weekly", "checked by"),
            ("procedure", "weekly", "requires"),
            ("weekly", "image", "confirmed by"),
            ("image", "asset", "affects"),
            ("image", "rebuild", "fixed in"),
            ("risk", "rebuild", "treated by"),
            ("rebuild", "deploy", "released by"),
            ("deploy", "clear", "verified by"),
            ("hosts", "clear", "verified by"),
            ("clear", "exception", "except"),
        ),
        comments=(
            "The vendor expects the fixed build for the reporting server this week; rescan and close the story then.",
        ),
    ),
    dict(
        title="Backup failure and the restore test that proved recovery",
        status="closed",
        created=(17, (8, 0)),
        updated=(1, (10, 45)),
        summary=(
            "One night the database backup failed because the manual snapshot quota was full. "
            "The alert reached the on-call engineer, who raised the quota and reran the job "
            "before the working day began. The monthly restore test then met the 60-minute "
            "recovery objective, and the runbook now includes the quota check."
        ),
        items=(
            (
                "failed",
                "event",
                "aws-backup-customer-db-17",
                "Nightly backup fails",
                "",
                0,
                0,
            ),
            (
                "quota",
                "event",
                "aws-snapshot-quota-increase",
                "Snapshot quota raised",
                "",
                1,
                0,
            ),
            (
                "rerun",
                "event",
                "aws-backup-customer-db-17-rerun",
                "Backup rerun succeeds",
                "",
                2,
                0,
            ),
            (
                "restore",
                "event",
                "jenkins-restore-smoke-test-success",
                "Restore test passes",
                "",
                3,
                0,
            ),
            (
                "runbook",
                "event",
                "bookstack-restore-runbook",
                "Runbook updated",
                "",
                4,
                0,
            ),
            ("asset", "asset", "backups", "", "", 0, 1),
            ("procedure", "document", "backup_restore", "", "", 2, 1),
            (
                "measured",
                "note",
                (1, (7, 15)),
                "Restored in 39 minutes",
                "Measured from declaration to validated service against the 60-minute objective.",
                3,
                1,
            ),
            ("risk", "risk", "restore_failure", "", "", 4, 1),
            ("plan", "document", "continuity", "", "", 3, 2),
        ),
        links=(
            ("failed", "asset", "for"),
            ("failed", "quota", "fixed by"),
            ("quota", "rerun", "allowed"),
            ("procedure", "rerun", "followed"),
            ("rerun", "restore", "tested in"),
            ("restore", "measured", "measured"),
            ("plan", "measured", "objective"),
            ("risk", "restore", "tested by"),
            ("restore", "runbook", "led to"),
        ),
        comments=(),
    ),
)


def _at(seeder, when):
    days_ago, (hour, minute) = when
    return datetime.combine(
        seeder.seed_date - timedelta(days=days_ago), time(hour, minute)
    )


def seed_stories(seeder, events, docs, assets, risks, objectives):
    """Seed the stories once; later runs keep any rearrangement made in KEEN."""
    db = seeder.db
    records = {
        "event": events,
        "document": docs,
        "asset": assets,
        "risk": risks,
        "objective": objectives,
    }
    for spec in STORIES:
        title = seeder.label(spec["title"])
        story = db.query(Story).filter_by(title=title).first()
        created = story is None
        if created:
            story = Story(
                title=title,
                summary=spec["summary"],
                status=spec["status"],
                version=1,
                created_by_user_id=seeder.user.id,
                created_at=_at(seeder, spec["created"]),
                updated_at=_at(seeder, spec["updated"]),
            )
            db.add(story)
            db.flush()
            seeder.stats["stories.created"] += 1
        else:
            seeder.stats["stories.unchanged"] += 1
        items = {}
        for position, (name, kind, ref, label, note, column, row) in enumerate(
            spec["items"]
        ):
            values = {
                "label": label,
                "note": note,
                "x": MARGIN + column * COLUMN,
                "y": MARGIN + row * ROW,
                "position": position,
            }
            if kind == "note":
                filters = {"story_id": story.id, "kind": "note", "label": label}
                values["occurred_at"] = _at(seeder, ref)
            else:
                filters = {
                    "story_id": story.id,
                    "kind": kind,
                    "entity_id": records[kind][ref].id,
                }
            values = {k: v for k, v in values.items() if k not in filters}
            items[name] = seeder.ensure_link(StoryItem, filters, values)
        for source, target, label in spec["links"]:
            seeder.ensure_link(
                StoryLink,
                {
                    "story_id": story.id,
                    "from_item_id": items[source].id,
                    "to_item_id": items[target].id,
                },
                {"label": label},
            )
        for offset, body in enumerate(spec["comments"]):
            seeder.ensure_link(
                StoryComment,
                {"story_id": story.id, "body": body},
                {
                    "created_by_user_id": seeder.user.id,
                    "created_at": story.updated_at + timedelta(minutes=5 + offset),
                },
            )
        if created:
            record_entity_changelog(
                db,
                entity_type="story",
                entity_id=story.id,
                action="created",
                before=None,
                after={
                    **summary(story, item_count=len(spec["items"])),
                    "source": "fictional_demo_seed",
                },
                user=seeder.user,
                changed_at=story.created_at,
            )
