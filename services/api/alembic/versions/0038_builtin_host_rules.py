"""Built-in mapping rules for the host events of KEEN's 2026.11 catalogue.

The Keen Agent recognises host faults, clock synchronisation, name-resolution
problems, application errors, account changes, the daily apt upgrade job
completing and protections taking effect once KEEN grants it the 2026.11
catalogue. These rules map them to the Keen Assurance Framework beside the
rules of 0037, without sharing a control with them. Rules an installation
already has are kept as they are; see app/core/builtin_rules.py.
"""

from alembic import op
from app.core.builtin_rules import add_rules, remove_rules, targets

revision = "0038_builtin_host_rules"
down_revision = "0037_builtin_rules"
branch_labels = None
depends_on = None

AGENT = "keen-agent"
FAULTS = "system.fault.detected"


def _rule(rule_id: str, description: str, when: dict, *refs: str) -> dict:
    return {
        "id": rule_id,
        "description": description,
        "when": {"source": AGENT, **when},
        "map_to": targets(*refs),
    }


def _attribute(action: str, name: str, operator: str, value: str, **when) -> dict:
    """Events of one type whose attribute ``name`` meets one condition."""
    return {
        "action": action,
        **when,
        "fields": [
            {
                "path": ["ueu", "attributes", name],
                "operator": operator,
                "value": value,
            }
        ],
    }


RULES = [
    _rule(
        "keen-server-faults",
        "Faults on servers are detected",
        {"action": FAULTS},
        "MON-4",
    ),
    _rule(
        "keen-server-capacity",
        "Servers running out of memory, disk or workers are detected",
        _attribute(
            FAULTS, "fault", "regex", "^(oom_kill|disk_full|worker_limit|hung_task)$"
        ),
        "CAP-7",
    ),
    _rule(
        "keen-server-hardware",
        "Disk, filesystem and hardware errors on servers are detected",
        _attribute(
            FAULTS, "fault", "regex", "^(io_error|filesystem_error|hardware_error)$"
        ),
        "MNT-21",
    ),
    _rule(
        "keen-server-clock",
        "Server clocks synchronising and losing synchronisation are recorded",
        {"action": "clock.sync.changed"},
        "SEA-54",
    ),
    _rule(
        "keen-server-dns",
        "Failed name resolution on servers is detected",
        {"action": "dns.lookup.failed"},
        "NET-92",
    ),
    _rule(
        "keen-server-application-errors",
        "Application errors on servers are recorded",
        {"action": "application.error.logged"},
        "MON-120",
    ),
    _rule(
        "keen-server-account-changes",
        "Accounts and group memberships changed on servers are recorded",
        {"action": "user.account.changed"},
        "IAC-198",
        "IAC-200",
    ),
    # Only completed runs match, so failing runs cannot keep the rule looking
    # active in Rule insights; a job that fails is recorded as a fault.
    _rule(
        "keen-server-patching",
        "Servers complete their daily upgrade job",
        _attribute(
            "routine.task.completed", "task", "equals", "patching", outcome="success"
        ),
        "VPM-1",
    ),
    _rule(
        "keen-server-protections",
        "Protections such as AppArmor take effect on servers",
        {"action": "system.protection.activated", "outcome": "success"},
        "NET-5",
    ),
]


def upgrade():
    add_rules(op.get_bind(), RULES)


def downgrade():
    remove_rules(op.get_bind(), RULES)
