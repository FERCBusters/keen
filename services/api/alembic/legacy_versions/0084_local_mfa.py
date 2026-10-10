"""Local MFA credentials and short-lived, unprivileged authentication challenges."""

from alembic import op

revision = "0084_local_mfa"
down_revision = "0083_evidence_retention"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE users ADD COLUMN mfa_enabled boolean NOT NULL DEFAULT false,
        ADD COLUMN mfa_version integer NOT NULL DEFAULT 0,
        ADD COLUMN mfa_totp_secret text,
        ADD COLUMN mfa_totp_last_step integer NOT NULL DEFAULT -1;
    CREATE TABLE mfa_credentials (
        id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        credential_id text NOT NULL UNIQUE, public_key text NOT NULL,
        sign_count bigint NOT NULL DEFAULT 0, name varchar(100) NOT NULL,
        created_at timestamp NOT NULL, last_used_at timestamp);
    CREATE INDEX ix_mfa_credentials_user_id ON mfa_credentials(user_id);
    CREATE TABLE mfa_recovery_codes (
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        code_hash varchar(64) NOT NULL, PRIMARY KEY(user_id,code_hash));
    CREATE TABLE mfa_challenges (
        token_hash varchar(64) PRIMARY KEY,
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        purpose varchar(16) NOT NULL, stage varchar(16) NOT NULL, version integer NOT NULL,
        password_digest varchar(64) NOT NULL, expires_at timestamp NOT NULL,
        data jsonb NOT NULL DEFAULT '{}');
    CREATE INDEX ix_mfa_challenges_user_id ON mfa_challenges(user_id);
    CREATE INDEX ix_mfa_challenges_expires_at ON mfa_challenges(expires_at);
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM users WHERE mfa_enabled) THEN
            RAISE EXCEPTION 'Disable MFA through authenticated account management before downgrading';
        END IF;
    END $$;
    DROP TABLE mfa_challenges,mfa_recovery_codes,mfa_credentials;
    ALTER TABLE users DROP COLUMN mfa_enabled,DROP COLUMN mfa_version,
        DROP COLUMN mfa_totp_secret,DROP COLUMN mfa_totp_last_step;
    """)
