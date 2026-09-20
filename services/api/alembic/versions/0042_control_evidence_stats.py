"""durable control evidence aggregate stats

Revision ID: 0042_control_evidence_stats
Revises: 0041_interested_party_notes
Create Date: 2026-05-26

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0042_control_evidence_stats"
down_revision = "0041_interested_party_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "control_evidence_stats",
        sa.Column(
            "control_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control_items.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_evidence", sa.DateTime(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "evidence_count >= 0",
            name="ck_control_evidence_stats_count_nonnegative",
        ),
    )
    op.create_index(
        "ix_control_evidence_stats_last_evidence",
        "control_evidence_stats",
        ["last_evidence"],
    )

    # Historical backfill: mappings are the source of truth. Do this before
    # creating triggers so the existing corpus starts out correct.
    op.execute("""
        INSERT INTO control_evidence_stats (
            control_item_id,
            evidence_count,
            last_evidence,
            updated_at
        )
        SELECT
            m.control_item_id,
            COUNT(*)::integer AS evidence_count,
            MAX(e.timestamp) AS last_evidence,
            NOW() AS updated_at
        FROM mappings m
        JOIN events e ON e.id = m.event_id
        GROUP BY m.control_item_id
        """)

    # Exact refresh helper used when a mapping is deleted/updated or an event
    # timestamp changes. Deletes the aggregate row when a control has no evidence.
    op.execute("""
        CREATE OR REPLACE FUNCTION keen_refresh_control_evidence_stats(
            p_control_item_id uuid
        ) RETURNS void AS $$
        BEGIN
            INSERT INTO control_evidence_stats (
                control_item_id,
                evidence_count,
                last_evidence,
                updated_at
            )
            SELECT
                m.control_item_id,
                COUNT(*)::integer AS evidence_count,
                MAX(e.timestamp) AS last_evidence,
                NOW() AS updated_at
            FROM mappings m
            JOIN events e ON e.id = m.event_id
            WHERE m.control_item_id = p_control_item_id
            GROUP BY m.control_item_id
            ON CONFLICT (control_item_id) DO UPDATE SET
                evidence_count = EXCLUDED.evidence_count,
                last_evidence = EXCLUDED.last_evidence,
                updated_at = NOW();

            DELETE FROM control_evidence_stats ces
            WHERE ces.control_item_id = p_control_item_id
              AND NOT EXISTS (
                  SELECT 1
                  FROM mappings m
                  WHERE m.control_item_id = p_control_item_id
              );
        END;
        $$ LANGUAGE plpgsql;
        """)

    # Mapping maintenance trigger. Inserts use an incremental bump because this
    # is the hot ingestion path. Deletes/updates recompute the affected control(s)
    # so last_evidence remains correct even when the newest mapping is removed.
    op.execute("""
        CREATE OR REPLACE FUNCTION keen_control_evidence_stats_from_mapping()
        RETURNS trigger AS $$
        DECLARE
            v_event_timestamp timestamp;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                SELECT e.timestamp INTO v_event_timestamp
                FROM events e
                WHERE e.id = NEW.event_id;

                INSERT INTO control_evidence_stats (
                    control_item_id,
                    evidence_count,
                    last_evidence,
                    updated_at
                ) VALUES (
                    NEW.control_item_id,
                    1,
                    v_event_timestamp,
                    NOW()
                )
                ON CONFLICT (control_item_id) DO UPDATE SET
                    evidence_count = control_evidence_stats.evidence_count + 1,
                    last_evidence = CASE
                        WHEN control_evidence_stats.last_evidence IS NULL
                            THEN EXCLUDED.last_evidence
                        WHEN EXCLUDED.last_evidence IS NULL
                            THEN control_evidence_stats.last_evidence
                        ELSE GREATEST(
                            control_evidence_stats.last_evidence,
                            EXCLUDED.last_evidence
                        )
                    END,
                    updated_at = NOW();
                RETURN NEW;
            ELSIF TG_OP = 'DELETE' THEN
                PERFORM keen_refresh_control_evidence_stats(OLD.control_item_id);
                RETURN OLD;
            ELSIF TG_OP = 'UPDATE' THEN
                IF OLD.control_item_id IS DISTINCT FROM NEW.control_item_id THEN
                    PERFORM keen_refresh_control_evidence_stats(OLD.control_item_id);
                    PERFORM keen_refresh_control_evidence_stats(NEW.control_item_id);
                ELSIF OLD.event_id IS DISTINCT FROM NEW.event_id THEN
                    PERFORM keen_refresh_control_evidence_stats(NEW.control_item_id);
                END IF;
                RETURN NEW;
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """)

    op.execute("""
        CREATE TRIGGER trg_control_evidence_stats_mappings_insert
        AFTER INSERT ON mappings
        FOR EACH ROW
        EXECUTE FUNCTION keen_control_evidence_stats_from_mapping();
        """)
    op.execute("""
        CREATE TRIGGER trg_control_evidence_stats_mappings_delete
        AFTER DELETE ON mappings
        FOR EACH ROW
        EXECUTE FUNCTION keen_control_evidence_stats_from_mapping();
        """)
    op.execute("""
        CREATE TRIGGER trg_control_evidence_stats_mappings_update
        AFTER UPDATE OF event_id, control_item_id ON mappings
        FOR EACH ROW
        EXECUTE FUNCTION keen_control_evidence_stats_from_mapping();
        """)

    # If an event timestamp changes, recompute last_evidence for all controls
    # mapped to that event. This is rare, but keeps the aggregate table truthful.
    op.execute("""
        CREATE OR REPLACE FUNCTION keen_control_evidence_stats_from_event_timestamp()
        RETURNS trigger AS $$
        DECLARE
            v_control_item_id uuid;
        BEGIN
            IF OLD.timestamp IS DISTINCT FROM NEW.timestamp THEN
                FOR v_control_item_id IN
                    SELECT DISTINCT m.control_item_id
                    FROM mappings m
                    WHERE m.event_id = NEW.id
                LOOP
                    PERFORM keen_refresh_control_evidence_stats(v_control_item_id);
                END LOOP;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """)
    op.execute("""
        CREATE TRIGGER trg_control_evidence_stats_events_timestamp
        AFTER UPDATE OF timestamp ON events
        FOR EACH ROW
        EXECUTE FUNCTION keen_control_evidence_stats_from_event_timestamp();
        """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_control_evidence_stats_events_timestamp ON events"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS keen_control_evidence_stats_from_event_timestamp()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_control_evidence_stats_mappings_update ON mappings"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_control_evidence_stats_mappings_delete ON mappings"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_control_evidence_stats_mappings_insert ON mappings"
    )
    op.execute("DROP FUNCTION IF EXISTS keen_control_evidence_stats_from_mapping()")
    op.execute("DROP FUNCTION IF EXISTS keen_refresh_control_evidence_stats(uuid)")
    op.drop_index(
        "ix_control_evidence_stats_last_evidence",
        table_name="control_evidence_stats",
    )
    op.drop_table("control_evidence_stats")
