"""Built-in mapping rules for the host events of KEEN's 2026.11 catalogue.

The Keen Agent recognises host faults, clock and name-resolution problems,
application errors and account changes once KEEN grants it the 2026.11
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


def _faults(pattern: str) -> dict:
    return {
        "action": FAULTS,
        "fields": [
            {
                "path": ["ueu", "attributes", "fault"],
                "operator": "regex",
                "value": pattern,
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
        _faults("^(oom_kill|disk_full|worker_limit|hung_task)$"),
        "CAP-7",
    ),
    _rule(
        "keen-server-hardware",
        "Disk, filesystem and hardware errors on servers are detected",
        _faults("^(io_error|filesystem_error|hardware_error)$"),
        "MNT-21",
    ),
    _rule(
        "keen-server-clock",
        "Server clocks that lose synchronisation are detected",
        {"action": "clock.sync.degraded"},
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
]


def upgrade():
    add_rules(op.get_bind(), RULES)


def downgrade():
    remove_rules(op.get_bind(), RULES)
