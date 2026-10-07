"""Opt-in evidence retention, durable object cleanup, and audit retention holds."""
from alembic import op
revision = "0083_evidence_retention"
down_revision = "0082_forgejo_feed_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_artifacts_storage_uri ON artifacts(storage_uri);
    CREATE INDEX IF NOT EXISTS ix_isms_documents_storage_uri ON isms_documents(storage_uri);
    CREATE INDEX IF NOT EXISTS ix_bookstack_section_storage_uri ON bookstack_section_evidence(storage_uri);
    CREATE INDEX IF NOT EXISTS ix_audits_report_storage_uri ON audits(final_report_storage_uri);
    CREATE INDEX IF NOT EXISTS ix_question_attachments_artifact ON event_question_post_attachments(artifact_id);
    CREATE TABLE evidence_retention_policy (
        id integer PRIMARY KEY CHECK (id = 1),
        mode text NOT NULL DEFAULT 'disabled' CHECK (mode IN ('disabled','age','count')),
        value bigint CHECK (value > 0),
        updated_at timestamp NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
        updated_by text,
        CHECK ((mode = 'disabled' AND value IS NULL) OR (mode <> 'disabled' AND value IS NOT NULL))
    );
    INSERT INTO evidence_retention_policy (id) VALUES (1);
    CREATE TABLE evidence_purge_jobs (
        id uuid PRIMARY KEY,
        mode text NOT NULL CHECK (mode IN ('age','count','all')),
        value bigint,
        automatic boolean NOT NULL,
        cutoff timestamp NOT NULL,
        status text NOT NULL DEFAULT 'queued',
        deleted bigint NOT NULL DEFAULT 0,
        requested_by text,
        created_at timestamp NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
        updated_at timestamp NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
        last_error text
    );
    CREATE UNIQUE INDEX evidence_purge_one_active ON evidence_purge_jobs ((true))
        WHERE status IN ('queued','running');
    CREATE TABLE evidence_object_cleanup (
        id bigserial PRIMARY KEY,
        storage_uri text NOT NULL UNIQUE,
        attempts integer NOT NULL DEFAULT 0,
        next_attempt_at timestamp NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
        last_error text,
        created_at timestamp NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
    );
    CREATE INDEX evidence_object_cleanup_due ON evidence_object_cleanup (next_attempt_at, id);
    CREATE TABLE audit_event_retention_holds (
        audit_id uuid NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
        event_id uuid NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
        PRIMARY KEY (audit_id, event_id)
    );
    CREATE INDEX audit_event_retention_holds_event ON audit_event_retention_holds (event_id);

    CREATE FUNCTION keen_hold_sampled_events() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.event_id IS NOT NULL THEN
            INSERT INTO audit_event_retention_holds VALUES (NEW.audit_id, NEW.event_id)
                ON CONFLICT DO NOTHING;
        END IF;
        -- Also preserve the originating event when a measurement is sampled.
        INSERT INTO audit_event_retention_holds (audit_id,event_id)
        SELECT NEW.audit_id, m.source_event_id FROM isms_effectiveness_metric_entries m
        WHERE m.source_event_id IS NOT NULL AND (
            (NEW.entity_type = 'isms_effectiveness_metric' AND m.id = NEW.entity_id) OR
            (NEW.entity_type = 'isms_effectiveness_measure' AND m.measure_id = NEW.entity_id))
        ON CONFLICT DO NOTHING;
        RETURN NEW;
    END $$;
    CREATE TRIGGER keen_hold_sampled_events AFTER INSERT OR UPDATE ON audit_evidence
        FOR EACH ROW EXECUTE FUNCTION keen_hold_sampled_events();
    INSERT INTO audit_event_retention_holds SELECT audit_id,event_id FROM audit_evidence
        WHERE event_id IS NOT NULL ON CONFLICT DO NOTHING;
    INSERT INTO audit_event_retention_holds (audit_id,event_id)
    SELECT a.audit_id,m.source_event_id FROM audit_evidence a
    JOIN isms_effectiveness_metric_entries m ON
        (a.entity_type = 'isms_effectiveness_metric' AND a.entity_id = m.id) OR
        (a.entity_type = 'isms_effectiveness_measure' AND a.entity_id = m.measure_id)
    WHERE m.source_event_id IS NOT NULL ON CONFLICT DO NOTHING;

    CREATE FUNCTION keen_hold_sampled_metric_event() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.source_event_id IS NOT NULL THEN
            INSERT INTO audit_event_retention_holds (audit_id,event_id)
            SELECT a.audit_id,NEW.source_event_id FROM audit_evidence a
            WHERE (a.entity_type = 'isms_effectiveness_metric' AND a.entity_id = NEW.id)
               OR (a.entity_type = 'isms_effectiveness_measure' AND a.entity_id = NEW.measure_id)
            ON CONFLICT DO NOTHING;
        END IF;
        RETURN NEW;
    END $$;
    CREATE TRIGGER keen_hold_sampled_metric_event AFTER INSERT OR UPDATE ON isms_effectiveness_metric_entries
        FOR EACH ROW EXECUTE FUNCTION keen_hold_sampled_metric_event();
    """)


def downgrade():
    # Do not silently discard durable deletion work or audit protections.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM evidence_object_cleanup) OR
           EXISTS (SELECT 1 FROM audit_event_retention_holds) OR
           EXISTS (SELECT 1 FROM evidence_purge_jobs WHERE status IN ('queued','running')) THEN
            RAISE EXCEPTION 'Retention has pending work or audit holds; downgrade requires operator review';
        END IF;
    END $$;""")
    op.execute("""
    DROP TRIGGER keen_hold_sampled_metric_event ON isms_effectiveness_metric_entries;
    DROP FUNCTION keen_hold_sampled_metric_event();
    DROP TRIGGER keen_hold_sampled_events ON audit_evidence;
    DROP FUNCTION keen_hold_sampled_events();
    DROP TABLE audit_event_retention_holds;
    DROP TABLE evidence_object_cleanup;
    DROP TABLE evidence_purge_jobs;
    DROP TABLE evidence_retention_policy;
    """)
