"""Starting groups for common roles, and the end of a reserved permission code.

A new installation had no groups, so an administrator built each role from the
permission list. Three groups arrive ready to use. They are ordinary groups:
an administrator renames them, changes their permissions or deletes them, and
adding an account to one grants what the group holds at that time.

- Internal auditors run the management system: they manage ISMS records,
  risks and PESTLE(E), read evidence, raise and delete incidents and
  questions, manage every audit, story and quiz. Nothing here is the
  administrator role: accounts, groups and permissions, sources and mapping
  rules, frameworks and control owners, retention, the audit trail and diary
  entries stay with administrators.
- External auditors conduct audits and read: they create and run their own
  audits, which includes reading evidence and ISMS records, and read risks,
  PESTLE(E) and anonymised quiz results. They change nothing else. Stories are
  left out because reading them includes commenting on them.
- Staff read evidence and ISMS records. Sitting an assigned quiz needs no
  permission.

A group is added only where no group has its name, compared without regard to
case, and is given the permissions that exist in the installation. The role of
each is left unset, so a member's own role decides.

"*" stands for an administrator in a permission snapshot, and creating a
permission with that code has been refused since 54fccee. A permission stored
under it before then would still make its holders pass every permission check,
so the row goes, with its assignments, and its holders' sessions refresh.
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0049_default_groups"
down_revision = "0048_events_retention_time"
branch_labels = None
depends_on = None

RESERVED = "*"

GROUPS = (
    (
        "Internal auditors",
        "Manage the ISMS, risks and PESTLE(E); read evidence; raise and delete "
        "incidents and questions; manage every audit, story and quiz. No "
        "administrator rights.",
        (
            "events.read",
            "isms.manage",
            "risk.manage",
            "pestle.manage",
            "audits.admin",
            "incident.create",
            "incident.delete",
            "question.create",
            "question.delete",
            "stories.admin",
            "quiz.manage",
        ),
    ),
    (
        "External auditors",
        "Create and run their own audits; read evidence, ISMS records, risks, "
        "PESTLE(E) and anonymised quiz results. Change nothing else.",
        (
            "audits.manage",
            "events.read",
            "isms.read",
            "risk.read",
            "pestle.read",
            "quiz.read",
        ),
    ),
    (
        "Staff",
        "Read evidence and ISMS records, and sit the quizzes assigned to them. "
        "Change nothing.",
        ("events.read", "isms.read"),
    ),
)


def upgrade():
    bind = op.get_bind()
    # Holders directly or through a group, found before their assignments go;
    # the foreign keys remove the assignments with the row.
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
        {"code": RESERVED},
    )
    bind.execute(
        sa.text("DELETE FROM permissions WHERE code = :code"), {"code": RESERVED}
    )
    for name, description, codes in GROUPS:
        taken = bind.execute(
            sa.text("SELECT 1 FROM groups WHERE lower(name) = lower(:name)"),
            {"name": name},
        ).first()
        if taken:
            continue
        group_id = uuid.uuid4()
        bind.execute(
            sa.text(
                "INSERT INTO groups (id, name, description, created_at) "
                "VALUES (:id, :name, :description, now())"
            ),
            {"id": group_id, "name": name, "description": description},
        )
        bind.execute(
            sa.text(
                "INSERT INTO group_permissions (group_id, permission_id) "
                "SELECT :id, p.id FROM permissions p WHERE p.code = ANY(:codes)"
            ),
            {"id": group_id, "codes": list(codes)},
        )


def downgrade():
    # A starting group nobody was added to and nobody redescribed goes; one in
    # use is the installation's own by now and stays. The reserved permission
    # is not brought back.
    bind = op.get_bind()
    for name, description, _ in GROUPS:
        bind.execute(
            sa.text("""
            DELETE FROM groups g
            WHERE g.name = :name AND g.description = :description
              AND NOT EXISTS (SELECT 1 FROM user_groups ug WHERE ug.group_id = g.id)
        """),
            {"name": name, "description": description},
        )
