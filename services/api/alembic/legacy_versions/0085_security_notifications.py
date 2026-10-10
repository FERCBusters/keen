"""Successful-login IP history and durable security notification outbox."""

from alembic import op

revision = "0085_security_notifications"
down_revision = "0084_local_mfa"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE user_login_ips (
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      address varchar(45) NOT NULL, first_seen_at timestamp NOT NULL,
      PRIMARY KEY(user_id,address));
    CREATE TABLE security_notifications (
      id uuid PRIMARY KEY, user_id uuid REFERENCES users(id) ON DELETE SET NULL,
      recipient text NOT NULL, subject text NOT NULL, body text NOT NULL,
      status varchar(16) NOT NULL, attempts integer NOT NULL,
      created_at timestamp NOT NULL, next_attempt_at timestamp NOT NULL,
      sent_at timestamp, last_error varchar(100));
    CREATE INDEX ix_security_notifications_pending ON security_notifications(status,next_attempt_at);
    """)


def downgrade():
    op.execute("DROP TABLE security_notifications,user_login_ips")
