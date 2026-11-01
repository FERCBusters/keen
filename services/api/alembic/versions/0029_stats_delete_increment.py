"""Remove mappings from their controls' evidence statistics once per statement.

Deleting a mapping recounted every mapping of its control, so removing the
evidence of many events (retention, a removed connection, a rule backfill)
read each control's evidence once per deleted mapping: the mappings of 200
events with popular controls took half a minute, under the exclusive
ingestion lease during retention. A statement-level trigger now sees every
mapping the statement removed. A control keeps its newest evidence when the
removed events are all older than it, so its count only falls by the number
removed. A control that loses its newest evidence or its last mapping, or
whose removed mapping names an event that has gone, is recounted. The row
trigger's delete branch is left in place for the downgrade.

Names are left unqualified so the migration acts on the schema it runs in.
"""

from alembic import op

revision = "0029_stats_delete_increment"
down_revision = "0028_automatic_purge_job_index"
branch_labels = None
depends_on = None

_STATEMENT_FUNCTION = """
CREATE FUNCTION keen_control_evidence_stats_after_delete() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            v_lowered uuid[];
            v_control uuid;
        BEGIN
            WITH removed_by_control AS (
                SELECT
                    r.control_item_id,
                    count(*) AS removed_count,
                    max(e.timestamp) AS newest_removed,
                    bool_or(e.timestamp IS NULL) AS undated
                FROM removed_mappings r
                LEFT JOIN events e ON e.id = r.event_id
                GROUP BY r.control_item_id
            ), lowered AS (
                UPDATE control_evidence_stats s
                SET evidence_count = s.evidence_count - d.removed_count,
                    updated_at = NOW()
                FROM removed_by_control d
                WHERE s.control_item_id = d.control_item_id
                  AND NOT d.undated
                  AND s.last_evidence IS NOT NULL
                  AND d.newest_removed < s.last_evidence
                  AND s.evidence_count > d.removed_count
                RETURNING s.control_item_id
            )
            SELECT coalesce(array_agg(control_item_id), '{}') INTO v_lowered
            FROM lowered;

            FOR v_control IN
                SELECT DISTINCT r.control_item_id
                FROM removed_mappings r
                WHERE r.control_item_id <> ALL (v_lowered)
            LOOP
                PERFORM keen_refresh_control_evidence_stats(v_control);
            END LOOP;
            RETURN NULL;
        END;
        $$
"""


def upgrade():
    op.execute(_STATEMENT_FUNCTION)
    op.execute("DROP TRIGGER trg_control_evidence_stats_mappings_delete ON mappings")
    op.execute(
        "CREATE TRIGGER trg_control_evidence_stats_mappings_delete "
        "AFTER DELETE ON mappings REFERENCING OLD TABLE AS removed_mappings "
        "FOR EACH STATEMENT EXECUTE FUNCTION keen_control_evidence_stats_after_delete()"
    )


def downgrade():
    op.execute("DROP TRIGGER trg_control_evidence_stats_mappings_delete ON mappings")
    op.execute(
        "CREATE TRIGGER trg_control_evidence_stats_mappings_delete "
        "AFTER DELETE ON mappings FOR EACH ROW "
        "EXECUTE FUNCTION keen_control_evidence_stats_from_mapping()"
    )
    op.execute("DROP FUNCTION keen_control_evidence_stats_after_delete()")
