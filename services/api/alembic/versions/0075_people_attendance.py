"""Link audit and meeting attendees to optional directory people.

Revision ID: 0075_people_attendance
Revises: 0074_seed_frameworks
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0075_people_attendance"
down_revision = "0074_seed_frameworks"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("audit_attendees", sa.Column("person_id", UUID(as_uuid=True), sa.ForeignKey("isms_people.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_audit_attendees_person_id", "audit_attendees", ["person_id"])
    op.execute("""
        UPDATE audit_attendees AS attendee SET person_id = person.id
        FROM isms_people AS person
        WHERE attendee.person_id IS NULL AND attendee.user_id = person.user_id
          AND attendee.user_id IS NOT NULL
    """)
    # Only attach free-text entries when an email identifies exactly one Person.
    op.execute("""
        UPDATE audit_attendees AS attendee SET person_id = matched.id
        FROM (
            SELECT lower(trim(email)) AS key, min(id::text)::uuid AS id
            FROM isms_people WHERE trim(email) <> ''
            GROUP BY lower(trim(email)) HAVING count(*) = 1
        ) AS matched
        WHERE attendee.person_id IS NULL AND attendee.user_id IS NULL
          AND lower(trim(coalesce(attendee.email, ''))) = matched.key
    """)
    op.create_table("isms_meeting_people",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("meeting_id", UUID(as_uuid=True), sa.ForeignKey("isms_meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_id", UUID(as_uuid=True), sa.ForeignKey("isms_people.id", ondelete="SET NULL")),
        sa.Column("attendance_type", sa.String(16), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("email", sa.String(256), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("attendance_type in ('attendee','apology')", name="ck_isms_meeting_people_type"))
    op.create_index("ix_isms_meeting_people_meeting_id", "isms_meeting_people", ["meeting_id"])
    op.create_index("ix_isms_meeting_people_person_id", "isms_meeting_people", ["person_id"])


def downgrade():
    op.drop_index("ix_isms_meeting_people_person_id", table_name="isms_meeting_people")
    op.drop_index("ix_isms_meeting_people_meeting_id", table_name="isms_meeting_people")
    op.drop_table("isms_meeting_people")
    op.drop_index("ix_audit_attendees_person_id", table_name="audit_attendees")
    op.drop_column("audit_attendees", "person_id")
