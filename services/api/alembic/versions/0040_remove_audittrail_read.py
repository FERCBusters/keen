"""The audit trail belongs to administrators; audit managers read evidence.

The audit trail lists every register's change history and every account's
requests, so only administrators read it and the audittrail.read permission is
removed with its user and group assignments. Accounts that held it have their
authorisation version advanced, so signed-in sessions refresh their permission
snapshots on their next request.

audits.manage and audits.admin now include events.read and isms.read, and their
descriptions say so.
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0040_remove_audittrail_read"
down_revision = "0039_measure_submission_secrets"
branch_labels = None
depends_on = None

CODE = "audittrail.read"
DESCRIPTION = "Can view the internal Keen UI->API audit trail (admin audit trail tab)."
DESCRIPTIONS = {
    "audits.manage": (
        "Create audits and scheduled templates; run and edit those you created. "
        "Sampling requires source read access.",
        "Create audits and scheduled templates; run and edit those you created. "
        "Includes events.read and isms.read; sampling other records requires "
        "read access to them.",
    ),
    "audits.admin": (
        "Edit and delete any audit or scheduled audit template.",
        "Edit and delete any audit or scheduled audit template. "
        "Includes events.read and isms.read.",
    ),
}


def _describe(bind, *, index: int) -> None:
    for code, texts in DESCRIPTIONS.items():
        bind.execute(
            sa.text("UPDATE permissions SET description = :d WHERE code = :code"),
            {"d": texts[index], "code": code},
        )


def upgrade():
    bind = op.get_bind()
    # Holders directly or through a group, found before their assignments go.
    bind.execute(
        sa.text("""
        UPDATE users SET authz_version = authz_version + 1
        WHERE id IN (
            SELECT up.user_id FROM user_permissions up
            JOIN permissions p ON p.id = up.permission_id
            WHERE p.code = :code
            UNION
            SELECT ug.user_id FROM user_groups ug
            JOIN group_permissions gp ON gp.group_id = ug.group_id
            JOIN permissions p ON p.id = gp.permission_id
            WHERE p.code = :code
        )
    """),
        {"code": CODE},
    )
    for table in ("user_permissions", "group_permissions"):
        bind.execute(
            sa.text(
                f"DELETE FROM {table} WHERE permission_id IN "
                "(SELECT id FROM permissions WHERE code = :code)"
            ),
            {"code": CODE},
        )
    bind.execute(sa.text("DELETE FROM permissions WHERE code = :code"), {"code": CODE})
    _describe(bind, index=1)


def downgrade():
    bind = op.get_bind()
    # Earlier assignments are not restored.
    bind.execute(
        sa.text("""
        INSERT INTO permissions (id, code, description, created_at)
        SELECT :id, :code, :description, CURRENT_TIMESTAMP
        WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :code)
    """),
        {"id": str(uuid.uuid4()), "code": CODE, "description": DESCRIPTION},
    )
    _describe(bind, index=0)
