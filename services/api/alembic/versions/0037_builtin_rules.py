"""Built-in mapping rules for cloud posture, workstation posture and server events.

Each rule maps evidence Keen collects itself to the Keen Assurance Framework:
cloud vendors' posture checks, the Keen Agent's workstation observations, and
the sign-ins, privileged commands, refused access, software changes and
security alerts it recognises on servers. Rules an installation already has
are kept as they are; see app/core/builtin_rules.py.
"""

from alembic import op
from app.core.builtin_rules import add_rules, remove_rules, targets

revision = "0037_builtin_rules"
down_revision = "0036_control_owners"
branch_labels = None
depends_on = None

CLOUD = r"^(aws|azure|digitalocean|google_cloud|google_workspace|linode|scaleway)$"
AGENT = "keen-agent"


def _cloud(rule_id: str, description: str, checks: str | None, *refs: str) -> dict:
    """Cloud posture checks; ``checks`` is a pattern of check keys, or None
    for every check."""
    return {
        "id": rule_id,
        "description": description,
        "when": {
            "source_regex": CLOUD,
            "action_regex": rf"^posture\.({checks})$" if checks else r"^posture\.",
        },
        "map_to": targets(*refs),
    }


def _agent(rule_id: str, description: str, action: str, *refs: str) -> dict:
    return {
        "id": rule_id,
        "description": description,
        "when": {"source": AGENT, "action": action},
        "map_to": targets(*refs),
    }


RULES = [
    _cloud(
        "keen-cloud-posture",
        "Cloud posture checks show how cloud services are secured",
        None,
        "CLD-7",
    ),
    _cloud(
        "keen-cloud-mfa",
        "Cloud accounts sign in with multi-factor authentication",
        r"identity\.(mfa|admin_mfa|mfa_enforced)",
        "IAC-203",
        "IAC-12",
    ),
    _cloud(
        "keen-cloud-privileged-access",
        "Cloud administrators and root users are few and protected",
        r"identity\.admins|aws\.root",
        "IAC-201",
    ),
    _cloud(
        "keen-cloud-access-review",
        "Inactive and guest cloud accounts are found for review",
        r"identity\.(inactive|guests)",
        "IAC-7",
        "IAC-200",
    ),
    _cloud(
        "keen-cloud-key-age",
        "Cloud access keys are rotated",
        r"credentials\.key_age",
        "IAC-199",
    ),
    _cloud(
        "keen-cloud-password-policy",
        "The cloud account enforces a password policy",
        r"aws\.password_policy",
        "IAC-11",
    ),
    _cloud(
        "keen-cloud-audit-logging",
        "Cloud activity is recorded in an audit trail",
        r"logging\.audit_trail",
        "MON-120",
        "MON-2",
    ),
    _cloud(
        "keen-cloud-network-exposure",
        "Cloud servers sit behind firewalls with administration ports closed",
        r"network\.(admin_ports|firewall_coverage)",
        "NET-4",
        "IAC-6",
    ),
    _cloud(
        "keen-cloud-databases",
        "Cloud databases are not open to the internet",
        r"network\.databases",
        "IAC-3",
        "NET-2",
    ),
    _cloud(
        "keen-cloud-public-storage",
        "Cloud storage is not open to the public",
        r"storage\.public_access",
        "IAC-202",
        "DCH-112",
    ),
    _cloud(
        "keen-cloud-findings",
        "The cloud vendor's security findings are tracked",
        r"findings",
        "VPM-2",
        "VPM-75",
    ),
    _cloud(
        "keen-cloud-backups",
        "Cloud servers are backed up",
        r"resilience\.backups",
        "BCD-69",
        "GOV-5",
    ),
    _cloud(
        "keen-cloud-kubernetes",
        "Kubernetes clusters are highly available and upgraded automatically",
        r"kubernetes\.control_plane",
        "BCD-70",
        "VPM-1",
    ),
    {
        "id": "keen-workstation-posture",
        "description": "Workstation posture is observed on each device",
        "when": {"source": AGENT, "action_regex": r"^endpoint\.[a-z_]+\.observed$"},
        "map_to": targets("END-40"),
    },
    _agent(
        "keen-workstation-disk-encryption",
        "Workstation disks are encrypted",
        "endpoint.disk_encryption.observed",
        "CRY-3",
        "CRY-4",
    ),
    _agent(
        "keen-workstation-screen-lock",
        "Workstation screens lock when left idle",
        "endpoint.screen_lock.observed",
        "PES-78",
    ),
    _agent(
        "keen-workstation-antivirus",
        "Workstations run anti-malware protection",
        "endpoint.antivirus.observed",
        "END-1",
        "END-41",
    ),
    _agent(
        "keen-workstation-firewall",
        "Workstation firewalls are on",
        "endpoint.firewall.observed",
        "NET-4",
    ),
    _agent(
        "keen-workstation-updates",
        "Workstations install updates automatically",
        "endpoint.automatic_updates.observed",
        "VPM-75",
    ),
    _agent(
        "keen-server-logins",
        "Server sign-ins are recorded",
        "user.authenticated",
        "IAC-5",
        "IAC-203",
    ),
    _agent(
        "keen-server-privileged-commands",
        "Privileged commands on servers are recorded",
        "privileged.action.completed",
        "IAC-201",
        "IAC-204",
    ),
    _agent(
        "keen-server-access-denied",
        "Refused access on servers is recorded",
        "access.denied",
        "IAC-202",
    ),
    _agent(
        "keen-server-software-changes",
        "Software installed, upgraded or removed on servers is recorded",
        "software.package.changed",
        "CHG-38",
        "VPM-1",
    ),
    _agent(
        "keen-server-security-alerts",
        "Intrusion detection alerts from servers are recorded",
        "security.alert.generated",
        "MON-1",
        "MON-121",
    ),
]


def upgrade():
    add_rules(op.get_bind(), RULES)


def downgrade():
    remove_rules(op.get_bind(), RULES)
