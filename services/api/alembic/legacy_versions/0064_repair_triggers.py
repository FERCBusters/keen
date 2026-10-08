"""repair framework event stats triggers

Revision ID: 0064_repair_triggers
Revises: 0063_user_authz_version
Create Date: 2026-06-04
"""

from __future__ import annotations

from alembic import op

revision = "0064_repair_triggers"
down_revision = "0063_user_authz_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Reinstall and repair the durable event-counter trigger path.

    The controls dashboard reads ``global_event_stats`` and
    ``framework_event_stats`` so it does not have to run expensive distinct
    event counts on every load. If one of the counter triggers was missing,
    stale, or created from an older broken migration, new mapped events could
    increment the global total without incrementing the per-framework mapped
    total. The visible symptom is that ``controls.html`` reports too many
    unmapped events while ``/events.html?unmapped=true`` remains correct.

    This migration is intentionally idempotent: it drops/recreates the trigger
    functions and trigger bindings, then rebuilds the durable rows from the
    source-of-truth tables.
    """

    op.execute("DROP TRIGGER IF EXISTS trg_global_event_stats_events_insert ON events")
    op.execute("DROP TRIGGER IF EXISTS trg_global_event_stats_events_delete ON events")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_framework_event_stats_mappings_insert ON mappings"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_framework_event_stats_mappings_delete ON mappings"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_framework_event_stats_mappings_update ON mappings"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_framework_event_stats_control_items_framework "
        "ON control_items"
    )

    op.execute("""
        CREATE OR REPLACE FUNCTION keen_bump_global_event_stats()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO global_event_stats (
                    stats_key,
                    total_events,
                    updated_at
                ) VALUES ('events', 1, NOW())
                ON CONFLICT (stats_key) DO UPDATE SET
                    total_events = global_event_stats.total_events + 1,
                    updated_at = NOW();
                RETURN NEW;
            ELSIF TG_OP = 'DELETE' THEN
                UPDATE global_event_stats
                SET total_events = GREATEST(total_events - 1, 0),
                    updated_at = NOW()
                WHERE stats_key = 'events';
                RETURN OLD;
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """)

    op.execute("""
        CREATE OR REPLACE FUNCTION keen_increment_framework_event_stats(
            p_framework_slug text
        ) RETURNS void AS $$
        BEGIN
            IF p_framework_slug IS NULL OR p_framework_slug = '' THEN
                RETURN;
            END IF;

            INSERT INTO framework_event_stats (
                framework_slug,
                mapped_event_count,
                updated_at
            ) VALUES (p_framework_slug, 1, NOW())
            ON CONFLICT (framework_slug) DO UPDATE SET
                mapped_event_count = framework_event_stats.mapped_event_count + 1,
                updated_at = NOW();
        END;
        $$ LANGUAGE plpgsql;
        """)

    op.execute("""
        CREATE OR REPLACE FUNCTION keen_decrement_framework_event_stats(
            p_framework_slug text
        ) RETURNS void AS $$
        BEGIN
            IF p_framework_slug IS NULL OR p_framework_slug = '' THEN
                RETURN;
            END IF;

            INSERT INTO framework_event_stats (
                framework_slug,
                mapped_event_count,
                updated_at
            ) VALUES (p_framework_slug, 0, NOW())
            ON CONFLICT (framework_slug) DO UPDATE SET
                mapped_event_count = GREATEST(
                    framework_event_stats.mapped_event_count - 1,
                    0
                ),
                updated_at = NOW();
        END;
        $$ LANGUAGE plpgsql;
        """)

    op.execute("""
        CREATE OR REPLACE FUNCTION keen_rebuild_framework_event_stats()
        RETURNS void AS $$
        BEGIN
            TRUNCATE framework_event_stats;
            INSERT INTO framework_event_stats (
                framework_slug,
                mapped_event_count,
                updated_at
            )
            SELECT
                c.framework_slug,
                COUNT(DISTINCT m.event_id)::integer AS mapped_event_count,
                NOW() AS updated_at
            FROM mappings m
            JOIN control_items c ON c.id = m.control_item_id
            GROUP BY c.framework_slug;
        END;
        $$ LANGUAGE plpgsql;
        """)

    op.execute("""
        CREATE OR REPLACE FUNCTION keen_framework_event_stats_from_mapping()
        RETURNS trigger AS $$
        DECLARE
            v_old_framework text;
            v_new_framework text;
            v_has_other boolean;
            v_has_remaining boolean;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                SELECT c.framework_slug INTO v_new_framework
                FROM control_items c
                WHERE c.id = NEW.control_item_id;

                SELECT EXISTS (
                    SELECT 1
                    FROM mappings m
                    JOIN control_items c ON c.id = m.control_item_id
                    WHERE m.event_id = NEW.event_id
                      AND c.framework_slug = v_new_framework
                      AND m.id <> NEW.id
                ) INTO v_has_other;

                IF NOT COALESCE(v_has_other, FALSE) THEN
                    PERFORM keen_increment_framework_event_stats(v_new_framework);
                END IF;
                RETURN NEW;
            ELSIF TG_OP = 'DELETE' THEN
                SELECT c.framework_slug INTO v_old_framework
                FROM control_items c
                WHERE c.id = OLD.control_item_id;

                SELECT EXISTS (
                    SELECT 1
                    FROM mappings m
                    JOIN control_items c ON c.id = m.control_item_id
                    WHERE m.event_id = OLD.event_id
                      AND c.framework_slug = v_old_framework
                ) INTO v_has_remaining;

                IF NOT COALESCE(v_has_remaining, FALSE) THEN
                    PERFORM keen_decrement_framework_event_stats(v_old_framework);
                END IF;
                RETURN OLD;
            ELSIF TG_OP = 'UPDATE' THEN
                SELECT c.framework_slug INTO v_old_framework
                FROM control_items c
                WHERE c.id = OLD.control_item_id;

                SELECT c.framework_slug INTO v_new_framework
                FROM control_items c
                WHERE c.id = NEW.control_item_id;

                IF OLD.event_id IS DISTINCT FROM NEW.event_id
                   OR v_old_framework IS DISTINCT FROM v_new_framework THEN
                    SELECT EXISTS (
                        SELECT 1
                        FROM mappings m
                        JOIN control_items c ON c.id = m.control_item_id
                        WHERE m.event_id = OLD.event_id
                          AND c.framework_slug = v_old_framework
                    ) INTO v_has_remaining;

                    IF NOT COALESCE(v_has_remaining, FALSE) THEN
                        PERFORM keen_decrement_framework_event_stats(v_old_framework);
                    END IF;

                    SELECT EXISTS (
                        SELECT 1
                        FROM mappings m
                        JOIN control_items c ON c.id = m.control_item_id
                        WHERE m.event_id = NEW.event_id
                          AND c.framework_slug = v_new_framework
                          AND m.id <> NEW.id
                    ) INTO v_has_other;

                    IF NOT COALESCE(v_has_other, FALSE) THEN
                        PERFORM keen_increment_framework_event_stats(v_new_framework);
                    END IF;
                END IF;
                RETURN NEW;
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """)

    op.execute("""
        CREATE OR REPLACE FUNCTION keen_framework_event_stats_from_control_item()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.framework_slug IS DISTINCT FROM NEW.framework_slug THEN
                PERFORM keen_rebuild_framework_event_stats();
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """)

    op.execute("""
        CREATE TRIGGER trg_global_event_stats_events_insert
        AFTER INSERT ON events
        FOR EACH ROW
        EXECUTE FUNCTION keen_bump_global_event_stats();
        """)
    op.execute("""
        CREATE TRIGGER trg_global_event_stats_events_delete
        AFTER DELETE ON events
        FOR EACH ROW
        EXECUTE FUNCTION keen_bump_global_event_stats();
        """)
    op.execute("""
        CREATE TRIGGER trg_framework_event_stats_mappings_insert
        AFTER INSERT ON mappings
        FOR EACH ROW
        EXECUTE FUNCTION keen_framework_event_stats_from_mapping();
        """)
    op.execute("""
        CREATE TRIGGER trg_framework_event_stats_mappings_delete
        AFTER DELETE ON mappings
        FOR EACH ROW
        EXECUTE FUNCTION keen_framework_event_stats_from_mapping();
        """)
    op.execute("""
        CREATE TRIGGER trg_framework_event_stats_mappings_update
        AFTER UPDATE OF event_id, control_item_id ON mappings
        FOR EACH ROW
        EXECUTE FUNCTION keen_framework_event_stats_from_mapping();
        """)
    op.execute("""
        CREATE TRIGGER trg_framework_event_stats_control_items_framework
        AFTER UPDATE OF framework_slug ON control_items
        FOR EACH ROW
        EXECUTE FUNCTION keen_framework_event_stats_from_control_item();
        """)

    op.execute("TRUNCATE framework_event_stats")
    op.execute("TRUNCATE global_event_stats")
    op.execute("""
        INSERT INTO global_event_stats (stats_key, total_events, updated_at)
        SELECT 'events', COUNT(*)::integer, NOW()
        FROM events
        """)
    op.execute("""
        INSERT INTO framework_event_stats (
            framework_slug,
            mapped_event_count,
            updated_at
        )
        SELECT
            c.framework_slug,
            COUNT(DISTINCT m.event_id)::integer AS mapped_event_count,
            NOW() AS updated_at
        FROM mappings m
        JOIN control_items c ON c.id = m.control_item_id
        GROUP BY c.framework_slug
        """)


def downgrade() -> None:
    # Keep the repaired trigger implementation in place on downgrade. Reverting
    # to an older trigger body would knowingly reintroduce the counter drift.
    pass
