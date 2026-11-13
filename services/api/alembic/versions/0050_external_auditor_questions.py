"""External auditors read stories and ask questions.

The External auditors starting group arrived in 0049 reading evidence, ISMS
records, risks, PESTLE(E) and quiz results, and running its members' own
audits. An auditor also follows the stories an organisation keeps and asks
about what they read, so the group gains stories.read, which includes
commenting on stories, and question.create, which asks questions on events and
on risks. It still holds no permission that edits or deletes a record.

The group changes only while it is still the one 0049 added, recognised by its
name and the description it arrived with. A group an administrator has
redescribed, renamed or deleted is the installation's own and is left as it
is. Its members' sessions refresh their permissions on their next request.
"""

import sqlalchemy as sa
from alembic import op

revision = "0050_external_auditor_questions"
down_revision = "0049_default_groups"
branch_labels = None
depends_on = None

GROUP = "External auditors"
ADDED = ("stories.read", "question.create")
BEFORE = (
    "Create and run their own audits; read evidence, ISMS records, risks, "
    "PESTLE(E) and anonymised quiz results. Change nothing else."
)
AFTER = (
    "Create and run their own audits; read evidence, ISMS records, risks, "
    "PESTLE(E), stories and anonymised quiz results; ask questions and comment "
    "on stories. Change no record."
)


def _starting_group(bind, description):
    return bind.execute(
        sa.text("SELECT id FROM groups WHERE name = :name AND description = :d"),
        {"name": GROUP, "d": description},
    ).scalar()


def _refresh_members(bind, group_id):
    bind.execute(
        sa.text(
            "UPDATE users SET authz_version = authz_version + 1 WHERE id IN "
            "(SELECT user_id FROM user_groups WHERE group_id = :id)"
        ),
        {"id": group_id},
    )


def upgrade():
    bind = op.get_bind()
    group_id = _starting_group(bind, BEFORE)
    if group_id is None:
        return
    bind.execute(
        sa.text(
            "INSERT INTO group_permissions (group_id, permission_id) "
            "SELECT :id, p.id FROM permissions p WHERE p.code = ANY(:codes) "
            "ON CONFLICT DO NOTHING"
        ),
        {"id": group_id, "codes": list(ADDED)},
    )
    bind.execute(
        sa.text("UPDATE groups SET description = :d WHERE id = :id"),
        {"d": AFTER, "id": group_id},
    )
    _refresh_members(bind, group_id)


def downgrade():
    bind = op.get_bind()
    group_id = _starting_group(bind, AFTER)
    if group_id is None:
        return
    bind.execute(
        sa.text(
            "DELETE FROM group_permissions WHERE group_id = :id AND permission_id IN "
            "(SELECT id FROM permissions WHERE code = ANY(:codes))"
        ),
        {"id": group_id, "codes": list(ADDED)},
    )
    bind.execute(
        sa.text("UPDATE groups SET description = :d WHERE id = :id"),
        {"d": BEFORE, "id": group_id},
    )
    _refresh_members(bind, group_id)
