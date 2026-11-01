"""Index automatic purge jobs by status and time.

Automatic retention looks up the latest completed automatic job every minute,
and prunes automatic job history older than thirty days. A partial index
answers both from the automatic jobs alone, as the manual one does for manual
jobs.
"""

from alembic import op

revision = "0028_automatic_purge_job_index"
down_revision = "0027_cloud_posture"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX ix_evidence_purge_jobs_automatic_status "
        "ON evidence_purge_jobs (status, updated_at) WHERE automatic"
    )
    op.execute("ANALYZE evidence_purge_jobs")


def downgrade():
    op.drop_index(
        "ix_evidence_purge_jobs_automatic_status", table_name="evidence_purge_jobs"
    )
