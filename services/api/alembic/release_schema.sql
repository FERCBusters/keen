--
-- PostgreSQL database dump
--



-- Dumped from database version 16.15 (Debian 16.15-1.pgdg13+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg13+2)












--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: keen_control_evidence_stats_from_event_timestamp(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.keen_control_evidence_stats_from_event_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
        $$;


--
-- Name: keen_control_evidence_stats_from_mapping(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.keen_control_evidence_stats_from_mapping() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
        $$;


--
-- Name: keen_hold_sampled_events(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.keen_hold_sampled_events() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: keen_hold_sampled_metric_event(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.keen_hold_sampled_metric_event() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: keen_refresh_control_evidence_stats(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.keen_refresh_control_evidence_stats(p_control_item_id uuid) RETURNS void
    LANGUAGE plpgsql
    AS $$
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
        $$;






--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--




--
-- Name: artifacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artifacts (
    id uuid NOT NULL,
    event_id uuid NOT NULL,
    kind character varying(64) NOT NULL,
    storage_uri character varying(512) NOT NULL,
    sha256 character varying(64) NOT NULL,
    content_type character varying(128) DEFAULT 'application/octet-stream'::character varying NOT NULL,
    size_bytes integer DEFAULT 0 NOT NULL,
    captured_at timestamp without time zone NOT NULL,
    captured_by character varying(128),
    redaction_status character varying(32) DEFAULT 'unknown'::character varying NOT NULL,
    pii_flags jsonb DEFAULT '{}'::jsonb NOT NULL,
    retention_class character varying(32) DEFAULT 'default'::character varying NOT NULL,
    parent_artifact_id uuid,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: audit_attendees; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_attendees (
    id uuid NOT NULL,
    audit_id uuid NOT NULL,
    name character varying(256) NOT NULL,
    email character varying(256),
    role character varying(128),
    created_at timestamp without time zone NOT NULL,
    user_id uuid,
    person_id uuid
);


--
-- Name: audit_event_retention_holds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_event_retention_holds (
    audit_id uuid NOT NULL,
    event_id uuid NOT NULL
);


--
-- Name: audit_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_evidence (
    id uuid NOT NULL,
    audit_id uuid NOT NULL,
    event_id uuid,
    evidence_url character varying(2048),
    title character varying(512),
    notes text DEFAULT ''::text NOT NULL,
    added_by_user_id uuid,
    added_at timestamp without time zone NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    entity_type character varying(64),
    entity_id uuid
);


--
-- Name: audit_findings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_findings (
    id uuid NOT NULL,
    audit_id uuid NOT NULL,
    kind character varying(32) NOT NULL,
    title character varying(256) NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    control_item_id uuid,
    status character varying(32) DEFAULT 'open'::character varying NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    CONSTRAINT ck_audit_findings_kind CHECK (((kind)::text = ANY ((ARRAY['major_nc'::character varying, 'minor_nc'::character varying, 'ofi'::character varying, 'best_practice'::character varying])::text[]))),
    CONSTRAINT ck_audit_findings_status CHECK (((status)::text = ANY ((ARRAY['open'::character varying, 'closed'::character varying, 'accepted'::character varying])::text[])))
);


--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id uuid NOT NULL,
    ts timestamp without time zone NOT NULL,
    username character varying(128),
    method character varying(16) NOT NULL,
    path character varying(256) NOT NULL,
    query_string text,
    status_code integer NOT NULL,
    duration_ms integer NOT NULL,
    client_ip character varying(64),
    user_agent character varying(256),
    referer character varying(512)
);


--
-- Name: audit_scoped_clauses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_scoped_clauses (
    audit_id uuid NOT NULL,
    clause_id uuid NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_scoped_controls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_scoped_controls (
    audit_id uuid NOT NULL,
    control_item_id uuid NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: audit_scoped_isms_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_scoped_isms_documents (
    audit_id uuid NOT NULL,
    document_id uuid NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: audits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audits (
    id uuid NOT NULL,
    title character varying(256) NOT NULL,
    framework_slug character varying(64) NOT NULL,
    status character varying(32) DEFAULT 'open'::character varying NOT NULL,
    start_date date,
    end_date date,
    created_by_user_id uuid,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    final_report_storage_uri character varying(512),
    final_report_filename character varying(255),
    final_report_content_type character varying(128),
    final_report_sha256 character varying(64),
    final_report_size_bytes integer,
    final_report_uploaded_at timestamp without time zone,
    final_report_uploaded_by_user_id uuid,
    notes text DEFAULT ''::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    executive_summary text DEFAULT ''::text NOT NULL,
    audit_type character varying(16) DEFAULT 'internal'::character varying NOT NULL,
    schedule_recurrence character varying(32) DEFAULT 'once'::character varying NOT NULL,
    schedule_interval integer DEFAULT 1 NOT NULL,
    schedule_next_run_date date,
    schedule_until_date date,
    schedule_last_run_date date,
    schedule_last_created_audit_id uuid,
    schedule_date_rule character varying(32) NOT NULL,
    schedule_anchor_month integer,
    CONSTRAINT ck_audits_audit_type CHECK (((audit_type)::text = ANY ((ARRAY['internal'::character varying, 'external'::character varying])::text[]))),
    CONSTRAINT ck_audits_schedule_anchor_month CHECK (((schedule_anchor_month IS NULL) OR ((schedule_anchor_month >= 1) AND (schedule_anchor_month <= 12)))),
    CONSTRAINT ck_audits_schedule_date_rule CHECK (((schedule_date_rule)::text = ANY ((ARRAY['exact'::character varying, 'first_weekday_of_month'::character varying, 'first_monday_of_month'::character varying, 'first_tuesday_of_month'::character varying, 'first_wednesday_of_month'::character varying, 'first_thursday_of_month'::character varying, 'first_friday_of_month'::character varying])::text[]))),
    CONSTRAINT ck_audits_schedule_interval CHECK ((schedule_interval >= 1)),
    CONSTRAINT ck_audits_schedule_recurrence CHECK (((schedule_recurrence)::text = ANY ((ARRAY['once'::character varying, 'weekly'::character varying, 'monthly'::character varying, 'quarterly'::character varying, 'yearly'::character varying])::text[]))),
    CONSTRAINT ck_audits_status CHECK (((status)::text = ANY ((ARRAY['open'::character varying, 'in_progress'::character varying, 'completed'::character varying, 'archived'::character varying, 'template'::character varying])::text[])))
);


--
-- Name: bookstack_section_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bookstack_section_evidence (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    target_control_id uuid,
    target_clause_id uuid,
    page_id integer NOT NULL,
    anchor character varying(256) NOT NULL,
    permalink character varying(2048) NOT NULL,
    page_title character varying(256) NOT NULL,
    revision_count integer,
    page_updated_at character varying(128) NOT NULL,
    storage_uri character varying(512) NOT NULL,
    sha256 character varying(64) NOT NULL,
    archived boolean DEFAULT false NOT NULL,
    captured_at timestamp without time zone NOT NULL,
    captured_by_user_id uuid,
    CONSTRAINT ck_bookstack_section_target CHECK (((target_control_id IS NOT NULL) OR (target_clause_id IS NOT NULL)))
);


--
-- Name: control_clause_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.control_clause_links (
    control_item_id uuid NOT NULL,
    clause_id uuid NOT NULL,
    applicability character varying(32) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_control_clause_links_applicability CHECK (((applicability)::text = ANY ((ARRAY['applicable'::character varying, 'partially_applicable'::character varying])::text[])))
);


--
-- Name: control_evidence_stats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.control_evidence_stats (
    control_item_id uuid NOT NULL,
    evidence_count integer DEFAULT 0 NOT NULL,
    last_evidence timestamp without time zone,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_control_evidence_stats_count_nonnegative CHECK ((evidence_count >= 0))
);


--
-- Name: control_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.control_items (
    id uuid NOT NULL,
    framework_slug character varying(64) NOT NULL,
    type character varying(32) NOT NULL,
    ref character varying(64) NOT NULL,
    title character varying(256),
    in_scope boolean DEFAULT true NOT NULL,
    tags jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: cross_framework_control_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cross_framework_control_links (
    id uuid NOT NULL,
    source_control_id uuid NOT NULL,
    target_control_id uuid NOT NULL,
    rationale text DEFAULT ''::text NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone NOT NULL,
    CONSTRAINT ck_cross_framework_distinct CHECK ((source_control_id <> target_control_id))
);


--
-- Name: entity_changelogs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entity_changelogs (
    id uuid NOT NULL,
    entity_type character varying(32) NOT NULL,
    entity_id uuid NOT NULL,
    entity_ref character varying(128),
    entity_title character varying(512),
    action character varying(32) NOT NULL,
    summary text NOT NULL,
    before_state jsonb,
    after_state jsonb,
    changes jsonb NOT NULL,
    changed_by_user_id uuid,
    changed_by_username character varying(128),
    changed_at timestamp without time zone NOT NULL,
    request_method character varying(16),
    request_path character varying(256)
);


--
-- Name: event_incidents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_incidents (
    id uuid NOT NULL,
    event_id uuid NOT NULL,
    title character varying(256) NOT NULL,
    text text DEFAULT ''::text NOT NULL,
    event_url character varying(2048) NOT NULL,
    created_by_user_id uuid,
    webhook_status_code integer,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: event_question_post_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_question_post_attachments (
    id uuid NOT NULL,
    post_id uuid NOT NULL,
    artifact_id uuid NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: event_question_posts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_question_posts (
    id uuid NOT NULL,
    thread_id uuid NOT NULL,
    author_user_id uuid,
    body text NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: event_question_threads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_question_threads (
    id uuid NOT NULL,
    event_id uuid,
    created_by_user_id uuid,
    status character varying(32) DEFAULT 'unanswered'::character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    last_admin_reply_at timestamp without time zone,
    author_last_seen_at timestamp without time zone,
    target_type character varying(32) NOT NULL,
    target_id uuid,
    target_ref character varying(128),
    target_title character varying(512),
    CONSTRAINT ck_event_question_thread_status CHECK (((status)::text = ANY ((ARRAY['unanswered'::character varying, 'reviewing'::character varying, 'answered'::character varying])::text[])))
);


--
-- Name: events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.events (
    id uuid NOT NULL,
    "timestamp" timestamp without time zone NOT NULL,
    source character varying(64) NOT NULL,
    system character varying(128),
    actor character varying(128),
    action character varying(128),
    outcome character varying(64),
    severity integer,
    summary text NOT NULL,
    raw_pointer jsonb DEFAULT '{}'::jsonb NOT NULL,
    normalized_payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    external_id character varying(128) NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: evidence_object_cleanup; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evidence_object_cleanup (
    id bigint NOT NULL,
    storage_uri text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    next_attempt_at timestamp without time zone DEFAULT (now() AT TIME ZONE 'UTC'::text) NOT NULL,
    last_error text,
    created_at timestamp without time zone DEFAULT (now() AT TIME ZONE 'UTC'::text) NOT NULL
);


--
-- Name: evidence_object_cleanup_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.evidence_object_cleanup_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: evidence_object_cleanup_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.evidence_object_cleanup_id_seq OWNED BY public.evidence_object_cleanup.id;


--
-- Name: evidence_purge_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evidence_purge_jobs (
    id uuid NOT NULL,
    mode text NOT NULL,
    value bigint,
    automatic boolean NOT NULL,
    cutoff timestamp without time zone NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    deleted bigint DEFAULT 0 NOT NULL,
    requested_by text,
    created_at timestamp without time zone DEFAULT (now() AT TIME ZONE 'UTC'::text) NOT NULL,
    updated_at timestamp without time zone DEFAULT (now() AT TIME ZONE 'UTC'::text) NOT NULL,
    last_error text,
    CONSTRAINT evidence_purge_jobs_mode_check CHECK ((mode = ANY (ARRAY['age'::text, 'count'::text, 'all'::text])))
);


--
-- Name: evidence_retention_policy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evidence_retention_policy (
    id integer NOT NULL,
    mode text DEFAULT 'disabled'::text NOT NULL,
    value bigint,
    updated_at timestamp without time zone DEFAULT (now() AT TIME ZONE 'UTC'::text) NOT NULL,
    updated_by text,
    CONSTRAINT evidence_retention_policy_check CHECK ((((mode = 'disabled'::text) AND (value IS NULL)) OR ((mode <> 'disabled'::text) AND (value IS NOT NULL)))),
    CONSTRAINT evidence_retention_policy_id_check CHECK ((id = 1)),
    CONSTRAINT evidence_retention_policy_mode_check CHECK ((mode = ANY (ARRAY['disabled'::text, 'age'::text, 'count'::text]))),
    CONSTRAINT evidence_retention_policy_value_check CHECK ((value > 0))
);


--
-- Name: framework_clauses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.framework_clauses (
    id uuid NOT NULL,
    framework_slug character varying(64) NOT NULL,
    ref character varying(64) NOT NULL,
    title character varying(256) DEFAULT ''::character varying NOT NULL,
    parent_clause_id uuid,
    sort_order integer DEFAULT 0 NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: framework_event_stats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.framework_event_stats (
    framework_slug character varying(64) NOT NULL,
    mapped_event_count integer DEFAULT 0 NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_framework_event_stats_count_nonnegative CHECK ((mapped_event_count >= 0))
);


--
-- Name: frameworks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.frameworks (
    id uuid NOT NULL,
    slug character varying(64) NOT NULL,
    created_at timestamp without time zone NOT NULL,
    name character varying(256),
    version character varying(128),
    description text,
    upstream_url text
);


--
-- Name: global_event_stats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.global_event_stats (
    stats_key character varying(32) DEFAULT 'events'::character varying NOT NULL,
    total_events integer DEFAULT 0 NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_global_event_stats_total_nonnegative CHECK ((total_events >= 0))
);


--
-- Name: group_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.group_permissions (
    group_id uuid NOT NULL,
    permission_id uuid NOT NULL
);


--
-- Name: groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.groups (
    id uuid NOT NULL,
    name character varying(64) NOT NULL,
    description text,
    created_at timestamp without time zone NOT NULL,
    role character varying(32)
);


--
-- Name: ingestion_cursors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ingestion_cursors (
    id uuid NOT NULL,
    name character varying(128) NOT NULL,
    last_ts timestamp without time zone,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: integration_collectors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.integration_collectors (
    id character varying(36) NOT NULL,
    name character varying(128) NOT NULL,
    connection_id character varying(36) NOT NULL,
    draft jsonb NOT NULL,
    version integer NOT NULL,
    live_revision integer,
    enabled boolean NOT NULL,
    failures integer NOT NULL,
    cursor character varying(64),
    next_run timestamp without time zone,
    last_success timestamp without time zone
);


--
-- Name: integration_connections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.integration_connections (
    id character varying(36) NOT NULL,
    name character varying(128) NOT NULL,
    base_url character varying(2048) NOT NULL,
    auth_kind character varying(16) NOT NULL,
    auth_name character varying(128) NOT NULL,
    username character varying(256) NOT NULL,
    auth_options jsonb NOT NULL,
    encrypted_secret text NOT NULL,
    version integer NOT NULL
);


--
-- Name: integration_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.integration_revisions (
    collector_id character varying(36) NOT NULL,
    revision integer NOT NULL,
    definition jsonb NOT NULL,
    connection_id character varying(36) NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: integration_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.integration_runs (
    id character varying(36) NOT NULL,
    collector_id character varying(36) NOT NULL,
    revision integer NOT NULL,
    status character varying(24) NOT NULL,
    created_at timestamp without time zone NOT NULL,
    started_at timestamp without time zone,
    finished_at timestamp without time zone,
    new_records integer NOT NULL,
    duplicates integer NOT NULL,
    message text NOT NULL,
    preview boolean NOT NULL,
    definition jsonb NOT NULL,
    connection_id character varying(36) NOT NULL,
    result jsonb NOT NULL
);


--
-- Name: interested_parties; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interested_parties (
    id uuid NOT NULL,
    framework_slug character varying(64) NOT NULL,
    name_id uuid NOT NULL,
    nature_id uuid NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    note text DEFAULT ''::text NOT NULL
);


--
-- Name: interested_party_communications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interested_party_communications (
    id uuid NOT NULL,
    interested_party_id uuid NOT NULL,
    event character varying(64) NOT NULL,
    "when" character varying(64) NOT NULL,
    with_whom character varying(128) NOT NULL,
    methods jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_interested_party_communications_event CHECK (((event)::text = ANY ((ARRAY['Business as Usual'::character varying, 'Incident'::character varying, 'Alert'::character varying, 'Notifiable Event'::character varying])::text[]))),
    CONSTRAINT ck_interested_party_communications_when CHECK ((("when")::text = ANY ((ARRAY['As Required'::character varying, 'Upon Identification'::character varying, 'Routine'::character varying, 'Within 24 hours'::character varying, 'Within 48 hours'::character varying, 'Within 72 hours'::character varying, 'Within a week'::character varying, 'Within a month'::character varying])::text[]))),
    CONSTRAINT ck_interested_party_communications_with_whom CHECK (((with_whom)::text = ANY ((ARRAY['Individual member'::character varying, 'Individual staff member'::character varying, 'Senior Supplier Point of Contact'::character varying, 'Supplier Point of Contact'::character varying, 'NCSC'::character varying, 'ICO'::character varying, 'Sender'::character varying])::text[])))
);


--
-- Name: interested_party_control_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interested_party_control_links (
    interested_party_id uuid NOT NULL,
    framework_slug character varying(64) NOT NULL,
    control_item_id uuid NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: interested_party_names; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interested_party_names (
    id uuid NOT NULL,
    name character varying(256) NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: interested_party_natures; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interested_party_natures (
    id uuid NOT NULL,
    name character varying(256) NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: isms_access_control_matrix_aws_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_access_control_matrix_aws_accounts (
    entry_id uuid NOT NULL,
    aws_account_id uuid NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: isms_access_control_matrix_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_access_control_matrix_entries (
    id uuid NOT NULL,
    task_action text DEFAULT ''::text NOT NULL,
    service_asset_id uuid,
    status character varying(32) DEFAULT 'Pending Approval'::character varying NOT NULL,
    approved_by_user_id uuid,
    notes text DEFAULT ''::text NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_isms_access_control_matrix_status CHECK (((status)::text = ANY ((ARRAY['Pending Approval'::character varying, 'Approved'::character varying])::text[])))
);


--
-- Name: isms_access_control_matrix_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_access_control_matrix_roles (
    entry_id uuid NOT NULL,
    org_node_id uuid NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: isms_application_configuration_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_application_configuration_entries (
    id uuid NOT NULL,
    source_type character varying(32) NOT NULL,
    document_id uuid,
    user_id uuid,
    asset_id uuid,
    org_node_id uuid,
    business_process_id uuid NOT NULL,
    value character varying(16) DEFAULT 'Low'::character varying NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_isms_app_config_source_type CHECK (((source_type)::text = ANY ((ARRAY['document'::character varying, 'person'::character varying, 'asset'::character varying, 'org_node'::character varying])::text[]))),
    CONSTRAINT ck_isms_app_config_value CHECK (((value)::text = ANY ((ARRAY['Low'::character varying, 'Medium'::character varying, 'High'::character varying])::text[])))
);


--
-- Name: isms_aws_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_aws_accounts (
    id uuid NOT NULL,
    name character varying(256) NOT NULL,
    account_id character varying(32),
    notes text DEFAULT ''::text NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: isms_business_processes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_business_processes (
    id uuid NOT NULL,
    name character varying(256) NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    is_default boolean DEFAULT false NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: isms_document_comments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_document_comments (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    body text NOT NULL,
    author_user_id uuid,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: isms_document_folders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_document_folders (
    id uuid NOT NULL,
    name character varying(256) NOT NULL,
    parent_id uuid
);


--
-- Name: isms_document_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_document_revisions (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    version integer NOT NULL,
    content_html text NOT NULL,
    sha256 character varying(64) NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: isms_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_documents (
    id uuid NOT NULL,
    title character varying(256) NOT NULL,
    document_type character varying(32) DEFAULT 'policy'::character varying NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    external_url character varying(2048),
    storage_uri character varying(512),
    filename character varying(255),
    content_type character varying(128),
    sha256 character varying(64),
    size_bytes integer,
    uploaded_at timestamp without time zone,
    uploaded_by_user_id uuid,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    folder_id uuid,
    tags jsonb DEFAULT '[]'::jsonb NOT NULL,
    content_html text DEFAULT ''::text NOT NULL,
    content_version integer DEFAULT 0 NOT NULL
);


--
-- Name: isms_effectiveness_measures; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_effectiveness_measures (
    id uuid NOT NULL,
    framework_slug character varying(64) NOT NULL,
    summary text DEFAULT ''::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    effectiveness_measure text DEFAULT ''::text NOT NULL,
    metric text DEFAULT ''::text NOT NULL,
    metric_key character varying(128),
    target_value double precision,
    target_unit character varying(64) DEFAULT ''::character varying NOT NULL,
    threshold_operator character varying(16) DEFAULT ''::character varying NOT NULL,
    owner_user_id uuid,
    frequency character varying(64) DEFAULT ''::character varying NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_isms_effectiveness_threshold_operator CHECK (((threshold_operator)::text = ANY ((ARRAY[''::character varying, 'lt'::character varying, 'lte'::character varying, 'eq'::character varying, 'gte'::character varying, 'gt'::character varying])::text[])))
);


--
-- Name: isms_effectiveness_metric_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_effectiveness_metric_entries (
    id uuid NOT NULL,
    measure_id uuid NOT NULL,
    recorded_at timestamp without time zone DEFAULT now() NOT NULL,
    period_start date,
    period_end date,
    metric_value double precision,
    metric_unit character varying(64) DEFAULT ''::character varying NOT NULL,
    qualitative_value text DEFAULT ''::text NOT NULL,
    source_type character varying(64) DEFAULT 'other'::character varying NOT NULL,
    source_title character varying(256) DEFAULT ''::character varying NOT NULL,
    source_url character varying(2048),
    source_reference character varying(256) DEFAULT ''::character varying NOT NULL,
    source_event_id uuid,
    notes text DEFAULT ''::text NOT NULL,
    raw_payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_isms_effectiveness_metric_entry_has_value CHECK (((metric_value IS NOT NULL) OR (qualitative_value <> ''::text)))
);


--
-- Name: isms_entity_clause_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_entity_clause_links (
    entity_type character varying(64) NOT NULL,
    entity_id uuid NOT NULL,
    framework_slug character varying(64) NOT NULL,
    clause_id uuid NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: isms_entity_control_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_entity_control_links (
    entity_type character varying(64) NOT NULL,
    entity_id uuid NOT NULL,
    framework_slug character varying(64) NOT NULL,
    control_item_id uuid NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: isms_licenses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_licenses (
    id uuid NOT NULL,
    name character varying(256) NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: isms_meeting_attendees; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_meeting_attendees (
    meeting_id uuid NOT NULL,
    user_id uuid NOT NULL,
    attendance_type character varying(16) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_isms_meeting_attendees_type CHECK (((attendance_type)::text = ANY ((ARRAY['attendee'::character varying, 'apology'::character varying])::text[])))
);


--
-- Name: isms_meeting_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_meeting_links (
    id uuid NOT NULL,
    meeting_id uuid NOT NULL,
    link_type character varying(32) DEFAULT 'external_url'::character varying NOT NULL,
    document_id uuid,
    title character varying(256) DEFAULT ''::character varying NOT NULL,
    url character varying(2048),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: isms_meeting_people; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_meeting_people (
    id uuid NOT NULL,
    meeting_id uuid NOT NULL,
    person_id uuid,
    attendance_type character varying(16) NOT NULL,
    name character varying(256) NOT NULL,
    email character varying(256) DEFAULT ''::character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    CONSTRAINT ck_isms_meeting_people_type CHECK (((attendance_type)::text = ANY ((ARRAY['attendee'::character varying, 'apology'::character varying])::text[])))
);


--
-- Name: isms_meetings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_meetings (
    id uuid NOT NULL,
    title character varying(256) DEFAULT 'ISMS Meeting'::character varying NOT NULL,
    date date NOT NULL,
    start_time time without time zone,
    end_time time without time zone,
    agenda_minutes_notes text DEFAULT ''::text NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: isms_objective_resource_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_objective_resource_users (
    objective_id uuid NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: isms_objectives; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_objectives (
    id uuid NOT NULL,
    requirement text DEFAULT ''::text NOT NULL,
    goal text DEFAULT ''::text NOT NULL,
    metric text DEFAULT ''::text NOT NULL,
    completion_method text DEFAULT ''::text NOT NULL,
    resource_requirements_text text DEFAULT ''::text NOT NULL,
    owner_user_id uuid,
    completion_target_date character varying(64),
    evaluation_method text DEFAULT ''::text NOT NULL,
    status character varying(32) DEFAULT 'not_started'::character varying NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_isms_objectives_status CHECK (((status)::text = ANY ((ARRAY['not_started'::character varying, 'in_progress'::character varying, 'completed'::character varying, 'deferred'::character varying, 'superseded'::character varying])::text[])))
);


--
-- Name: isms_org_node_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_org_node_users (
    org_node_id uuid NOT NULL,
    user_id uuid NOT NULL,
    relationship_type character varying(32) DEFAULT 'member'::character varying NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: isms_org_nodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_org_nodes (
    id uuid NOT NULL,
    parent_id uuid,
    name character varying(256) NOT NULL,
    node_type character varying(64) DEFAULT 'role'::character varying NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: isms_people; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_people (
    id uuid NOT NULL,
    name character varying(256) NOT NULL,
    email character varying(256) DEFAULT ''::character varying NOT NULL,
    "position" character varying(256) DEFAULT ''::character varying NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    user_id uuid,
    org_node_id uuid,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: isms_person_assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_person_assets (
    person_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    relationship_type character varying(32) DEFAULT 'uses'::character varying NOT NULL
);


--
-- Name: isms_person_assurances; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_person_assurances (
    id uuid NOT NULL,
    person_id uuid NOT NULL,
    category character varying(64) NOT NULL,
    name character varying(256) NOT NULL,
    status character varying(32) DEFAULT 'pending'::character varying NOT NULL,
    source_system character varying(128) DEFAULT ''::character varying NOT NULL,
    evidence_url character varying(2048) DEFAULT ''::character varying NOT NULL,
    completed_at date,
    expires_at date,
    notes text DEFAULT ''::text NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: isms_vendors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.isms_vendors (
    id uuid NOT NULL,
    name character varying(256) NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    website character varying(2048) DEFAULT ''::character varying NOT NULL,
    contact character varying(256) DEFAULT ''::character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: keen_agents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.keen_agents (
    id character varying(36) NOT NULL,
    name character varying(128) NOT NULL,
    token_hash character varying(64) NOT NULL,
    enabled boolean NOT NULL,
    created_at timestamp without time zone NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    last_seen timestamp without time zone,
    health jsonb NOT NULL
);


--
-- Name: managed_configuration_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.managed_configuration_revisions (
    id uuid NOT NULL,
    name character varying(64) NOT NULL,
    version integer NOT NULL,
    document jsonb NOT NULL,
    updated_by uuid,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: managed_configurations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.managed_configurations (
    name character varying(64) NOT NULL,
    document jsonb NOT NULL,
    version integer NOT NULL,
    updated_by uuid,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: mappings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mappings (
    id uuid NOT NULL,
    event_id uuid NOT NULL,
    control_item_id uuid NOT NULL,
    confidence double precision DEFAULT 0.5 NOT NULL,
    method character varying(32) NOT NULL,
    rationale text DEFAULT ''::text NOT NULL,
    mapped_by character varying(128),
    mapped_at timestamp without time zone NOT NULL
);


--
-- Name: mfa_challenges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mfa_challenges (
    token_hash character varying(64) NOT NULL,
    user_id uuid NOT NULL,
    purpose character varying(16) NOT NULL,
    stage character varying(16) NOT NULL,
    version integer NOT NULL,
    password_digest character varying(64) NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    data jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: mfa_credentials; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mfa_credentials (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    credential_id text NOT NULL,
    public_key text NOT NULL,
    sign_count bigint DEFAULT 0 NOT NULL,
    name character varying(100) NOT NULL,
    created_at timestamp without time zone NOT NULL,
    last_used_at timestamp without time zone
);


--
-- Name: mfa_recovery_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mfa_recovery_codes (
    user_id uuid NOT NULL,
    code_hash character varying(64) NOT NULL
);


--
-- Name: oidc_login_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.oidc_login_states (
    id integer NOT NULL,
    state character varying(255) NOT NULL,
    nonce character varying(255) NOT NULL,
    code_verifier text NOT NULL,
    next_url text,
    expires_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: oidc_login_states_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.oidc_login_states_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: oidc_login_states_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.oidc_login_states_id_seq OWNED BY public.oidc_login_states.id;


--
-- Name: osa_control_mappings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.osa_control_mappings (
    control_id uuid NOT NULL,
    nist_ref character varying(64) NOT NULL
);


--
-- Name: permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.permissions (
    id uuid NOT NULL,
    code character varying(128) NOT NULL,
    description text,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: pestle_business_process_relevance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pestle_business_process_relevance (
    pestle_item_id uuid NOT NULL,
    business_process_id uuid NOT NULL,
    relevance_id uuid NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: pestle_business_processes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pestle_business_processes (
    id uuid NOT NULL,
    name character varying(256) NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: pestle_clause_relevance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pestle_clause_relevance (
    pestle_item_id uuid NOT NULL,
    clause_id uuid NOT NULL,
    relevance_id uuid NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: pestle_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pestle_items (
    id uuid NOT NULL,
    framework_slug character varying(64) NOT NULL,
    type character varying(32) NOT NULL,
    lens character varying(16) NOT NULL,
    item text DEFAULT ''::text NOT NULL,
    overall_relevance_id uuid NOT NULL,
    rationale text DEFAULT ''::text NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_pestle_items_lens CHECK (((lens)::text = ANY ((ARRAY['Internal'::character varying, 'External'::character varying])::text[]))),
    CONSTRAINT ck_pestle_items_type CHECK (((type)::text = ANY ((ARRAY['Political'::character varying, 'Economical'::character varying, 'Social'::character varying, 'Technological'::character varying, 'Legal'::character varying, 'Environmental'::character varying, 'Ethical'::character varying])::text[])))
);


--
-- Name: pestle_relevance_levels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pestle_relevance_levels (
    id uuid NOT NULL,
    code character varying(16) NOT NULL,
    label character varying(32) NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: risk_asset_subcategories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.risk_asset_subcategories (
    id uuid NOT NULL,
    category_id uuid NOT NULL,
    name character varying(128) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: risk_assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.risk_assets (
    id uuid NOT NULL,
    name character varying(256) NOT NULL,
    category_id uuid NOT NULL,
    subcategory_id uuid NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    license character varying(256) DEFAULT ''::character varying NOT NULL,
    owner_org_node_id uuid,
    register_held_by_org_node_id uuid,
    description text DEFAULT ''::text NOT NULL,
    created_by_user_id uuid,
    license_id uuid,
    vendor_id uuid
);


--
-- Name: risk_categories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.risk_categories (
    id uuid NOT NULL,
    name character varying(128) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: risk_control_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.risk_control_links (
    risk_id uuid NOT NULL,
    framework_slug character varying(64) NOT NULL,
    control_item_id uuid NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: risk_library_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.risk_library_entries (
    id uuid NOT NULL,
    name character varying(256) NOT NULL,
    threat_summary text NOT NULL,
    risk_types jsonb NOT NULL,
    treatment_guidance text NOT NULL,
    created_at timestamp without time zone NOT NULL,
    suggested_assessment jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: risk_register_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.risk_register_settings (
    id integer NOT NULL,
    low_max integer NOT NULL,
    moderate_max integer NOT NULL,
    high_max integer NOT NULL
);


--
-- Name: risk_register_settings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.risk_register_settings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: risk_register_settings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.risk_register_settings_id_seq OWNED BY public.risk_register_settings.id;


--
-- Name: risks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.risks (
    id uuid NOT NULL,
    risk_types jsonb DEFAULT '[]'::jsonb NOT NULL,
    risk_owner_user_id uuid,
    threat_summary text DEFAULT ''::text NOT NULL,
    threat_score integer DEFAULT 1 NOT NULL,
    vulnerability_score integer DEFAULT 1 NOT NULL,
    impact_score integer DEFAULT 1 NOT NULL,
    risk_score integer DEFAULT 1 NOT NULL,
    residual_vulnerability_score integer DEFAULT 1 NOT NULL,
    residual_impact_score integer DEFAULT 1 NOT NULL,
    residual_risk_score integer DEFAULT 1 NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    created_by_user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    asset_id uuid NOT NULL,
    mitigator_context jsonb DEFAULT '{}'::jsonb NOT NULL,
    register_likelihood integer,
    register_impact integer,
    register_residual_likelihood integer,
    register_residual_impact integer,
    treatment_strategy character varying(32) DEFAULT ''::character varying NOT NULL,
    treatment_status character varying(32) DEFAULT 'open'::character varying NOT NULL,
    treatment_plan text DEFAULT ''::text NOT NULL,
    treatment_due_at date,
    risk_owner_role_id uuid,
    CONSTRAINT ck_risks_impact_score CHECK (((impact_score >= 0) AND (impact_score <= 5))),
    CONSTRAINT ck_risks_one_owner CHECK (((risk_owner_user_id IS NULL) OR (risk_owner_role_id IS NULL))),
    CONSTRAINT ck_risks_register_impact CHECK (((register_impact >= 1) AND (register_impact <= 5))),
    CONSTRAINT ck_risks_register_likelihood CHECK (((register_likelihood >= 1) AND (register_likelihood <= 5))),
    CONSTRAINT ck_risks_register_residual_impact CHECK (((register_residual_impact >= 1) AND (register_residual_impact <= 5))),
    CONSTRAINT ck_risks_register_residual_likelihood CHECK (((register_residual_likelihood >= 1) AND (register_residual_likelihood <= 5))),
    CONSTRAINT ck_risks_residual_impact_score CHECK (((residual_impact_score >= 0) AND (residual_impact_score <= 5))),
    CONSTRAINT ck_risks_residual_vulnerability_score CHECK (((residual_vulnerability_score >= 0) AND (residual_vulnerability_score <= 5))),
    CONSTRAINT ck_risks_threat_score CHECK (((threat_score >= 0) AND (threat_score <= 5))),
    CONSTRAINT ck_risks_vulnerability_score CHECK (((vulnerability_score >= 0) AND (vulnerability_score <= 5)))
);


--
-- Name: rule_backfill_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rule_backfill_jobs (
    id uuid NOT NULL,
    rule_id character varying(96) NOT NULL,
    source character varying(128) NOT NULL,
    rule_document jsonb NOT NULL,
    rules_version integer NOT NULL,
    total_estimate integer NOT NULL,
    status character varying(24) NOT NULL,
    cutoff timestamp without time zone NOT NULL,
    cursor_timestamp timestamp without time zone,
    cursor_id uuid,
    examined integer NOT NULL,
    matched integer NOT NULL,
    created_mappings integer NOT NULL,
    error text,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: saved_searches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.saved_searches (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    name character varying(128) NOT NULL,
    url text NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: security_notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.security_notifications (
    id uuid NOT NULL,
    user_id uuid,
    recipient text NOT NULL,
    subject text NOT NULL,
    body text NOT NULL,
    status character varying(16) NOT NULL,
    attempts integer NOT NULL,
    created_at timestamp without time zone NOT NULL,
    next_attempt_at timestamp without time zone NOT NULL,
    sent_at timestamp without time zone,
    last_error character varying(100)
);


--
-- Name: source_ingestion_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_ingestion_states (
    source character varying(64) NOT NULL,
    paused boolean NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    updated_by character varying(256)
);


--
-- Name: sso_email_challenges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sso_email_challenges (
    token_hash character varying(64) NOT NULL,
    user_id uuid NOT NULL,
    email character varying(256) NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    mfa_version integer NOT NULL,
    password_digest character varying(64) NOT NULL
);


--
-- Name: user_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_groups (
    user_id uuid NOT NULL,
    group_id uuid NOT NULL
);


--
-- Name: user_identities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_identities (
    id integer NOT NULL,
    user_id uuid NOT NULL,
    provider character varying(40) DEFAULT 'oidc'::character varying NOT NULL,
    issuer character varying(512) NOT NULL,
    subject character varying(512) NOT NULL,
    email character varying(320),
    preferred_username character varying(255),
    display_name character varying(255),
    claims jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: user_identities_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_identities_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_identities_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_identities_id_seq OWNED BY public.user_identities.id;


--
-- Name: user_login_ips; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_login_ips (
    user_id uuid NOT NULL,
    address character varying(45) NOT NULL,
    first_seen_at timestamp without time zone NOT NULL
);


--
-- Name: user_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_permissions (
    user_id uuid NOT NULL,
    permission_id uuid NOT NULL
);


--
-- Name: user_sso_emails; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_sso_emails (
    email character varying(256) NOT NULL,
    user_id uuid NOT NULL,
    verified_at timestamp without time zone NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid NOT NULL,
    username character varying(128) NOT NULL,
    password_hash character varying(255) NOT NULL,
    role character varying(32) DEFAULT 'normal'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    pref_use_local_timezone boolean DEFAULT false NOT NULL,
    pref_timezone character varying(64),
    pref_theme character varying(32) DEFAULT 'purple'::character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    last_login_at timestamp without time zone,
    pref_auto_apply_filters boolean NOT NULL,
    pref_source_colors jsonb NOT NULL,
    pref_viz_mode character varying(32) NOT NULL,
    pref_landing_page character varying(2048) NOT NULL,
    pref_default_visualisation jsonb,
    pref_default_framework character varying(64),
    pref_date_format character varying(16) NOT NULL,
    email character varying(256),
    authz_version integer NOT NULL,
    mfa_enabled boolean DEFAULT false NOT NULL,
    mfa_version integer DEFAULT 0 NOT NULL,
    mfa_totp_secret text,
    mfa_totp_last_step integer DEFAULT '-1'::integer NOT NULL,
    auth_backend character varying(16) DEFAULT 'local'::character varying NOT NULL,
    CONSTRAINT ck_users_pref_date_format CHECK (((pref_date_format)::text = ANY ((ARRAY['default'::character varying, 'ymd'::character varying, 'dmy'::character varying])::text[])))
);


--
-- Name: evidence_object_cleanup id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence_object_cleanup ALTER COLUMN id SET DEFAULT nextval('public.evidence_object_cleanup_id_seq'::regclass);


--
-- Name: oidc_login_states id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oidc_login_states ALTER COLUMN id SET DEFAULT nextval('public.oidc_login_states_id_seq'::regclass);


--
-- Name: risk_register_settings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_register_settings ALTER COLUMN id SET DEFAULT nextval('public.risk_register_settings_id_seq'::regclass);


--
-- Name: user_identities id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_identities ALTER COLUMN id SET DEFAULT nextval('public.user_identities_id_seq'::regclass);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--




--
-- Name: artifacts artifacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifacts
    ADD CONSTRAINT artifacts_pkey PRIMARY KEY (id);


--
-- Name: audit_attendees audit_attendees_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_attendees
    ADD CONSTRAINT audit_attendees_pkey PRIMARY KEY (id);


--
-- Name: audit_event_retention_holds audit_event_retention_holds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_event_retention_holds
    ADD CONSTRAINT audit_event_retention_holds_pkey PRIMARY KEY (audit_id, event_id);


--
-- Name: audit_evidence audit_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_evidence
    ADD CONSTRAINT audit_evidence_pkey PRIMARY KEY (id);


--
-- Name: audit_findings audit_findings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_findings
    ADD CONSTRAINT audit_findings_pkey PRIMARY KEY (id);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);


--
-- Name: audit_scoped_clauses audit_scoped_clauses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_scoped_clauses
    ADD CONSTRAINT audit_scoped_clauses_pkey PRIMARY KEY (audit_id, clause_id);


--
-- Name: audit_scoped_controls audit_scoped_controls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_scoped_controls
    ADD CONSTRAINT audit_scoped_controls_pkey PRIMARY KEY (audit_id, control_item_id);


--
-- Name: audit_scoped_isms_documents audit_scoped_isms_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_scoped_isms_documents
    ADD CONSTRAINT audit_scoped_isms_documents_pkey PRIMARY KEY (audit_id, document_id);


--
-- Name: audits audits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audits
    ADD CONSTRAINT audits_pkey PRIMARY KEY (id);


--
-- Name: bookstack_section_evidence bookstack_section_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookstack_section_evidence
    ADD CONSTRAINT bookstack_section_evidence_pkey PRIMARY KEY (id);


--
-- Name: control_clause_links control_clause_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.control_clause_links
    ADD CONSTRAINT control_clause_links_pkey PRIMARY KEY (control_item_id, clause_id);


--
-- Name: control_evidence_stats control_evidence_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.control_evidence_stats
    ADD CONSTRAINT control_evidence_stats_pkey PRIMARY KEY (control_item_id);


--
-- Name: control_items control_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.control_items
    ADD CONSTRAINT control_items_pkey PRIMARY KEY (id);


--
-- Name: cross_framework_control_links cross_framework_control_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cross_framework_control_links
    ADD CONSTRAINT cross_framework_control_links_pkey PRIMARY KEY (id);


--
-- Name: entity_changelogs entity_changelogs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_changelogs
    ADD CONSTRAINT entity_changelogs_pkey PRIMARY KEY (id);


--
-- Name: event_incidents event_incidents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_incidents
    ADD CONSTRAINT event_incidents_pkey PRIMARY KEY (id);


--
-- Name: event_question_post_attachments event_question_post_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_question_post_attachments
    ADD CONSTRAINT event_question_post_attachments_pkey PRIMARY KEY (id);


--
-- Name: event_question_posts event_question_posts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_question_posts
    ADD CONSTRAINT event_question_posts_pkey PRIMARY KEY (id);


--
-- Name: event_question_threads event_question_threads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_question_threads
    ADD CONSTRAINT event_question_threads_pkey PRIMARY KEY (id);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: evidence_object_cleanup evidence_object_cleanup_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence_object_cleanup
    ADD CONSTRAINT evidence_object_cleanup_pkey PRIMARY KEY (id);


--
-- Name: evidence_object_cleanup evidence_object_cleanup_storage_uri_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence_object_cleanup
    ADD CONSTRAINT evidence_object_cleanup_storage_uri_key UNIQUE (storage_uri);


--
-- Name: evidence_purge_jobs evidence_purge_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence_purge_jobs
    ADD CONSTRAINT evidence_purge_jobs_pkey PRIMARY KEY (id);


--
-- Name: evidence_retention_policy evidence_retention_policy_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence_retention_policy
    ADD CONSTRAINT evidence_retention_policy_pkey PRIMARY KEY (id);


--
-- Name: framework_clauses framework_clauses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.framework_clauses
    ADD CONSTRAINT framework_clauses_pkey PRIMARY KEY (id);


--
-- Name: framework_event_stats framework_event_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.framework_event_stats
    ADD CONSTRAINT framework_event_stats_pkey PRIMARY KEY (framework_slug);


--
-- Name: frameworks frameworks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.frameworks
    ADD CONSTRAINT frameworks_pkey PRIMARY KEY (id);


--
-- Name: frameworks frameworks_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.frameworks
    ADD CONSTRAINT frameworks_slug_key UNIQUE (slug);


--
-- Name: global_event_stats global_event_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.global_event_stats
    ADD CONSTRAINT global_event_stats_pkey PRIMARY KEY (stats_key);


--
-- Name: group_permissions group_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_permissions
    ADD CONSTRAINT group_permissions_pkey PRIMARY KEY (group_id, permission_id);


--
-- Name: groups groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_pkey PRIMARY KEY (id);


--
-- Name: ingestion_cursors ingestion_cursors_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingestion_cursors
    ADD CONSTRAINT ingestion_cursors_name_key UNIQUE (name);


--
-- Name: ingestion_cursors ingestion_cursors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingestion_cursors
    ADD CONSTRAINT ingestion_cursors_pkey PRIMARY KEY (id);


--
-- Name: integration_collectors integration_collectors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integration_collectors
    ADD CONSTRAINT integration_collectors_pkey PRIMARY KEY (id);


--
-- Name: integration_connections integration_connections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integration_connections
    ADD CONSTRAINT integration_connections_pkey PRIMARY KEY (id);


--
-- Name: integration_revisions integration_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integration_revisions
    ADD CONSTRAINT integration_revisions_pkey PRIMARY KEY (collector_id, revision);


--
-- Name: integration_runs integration_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integration_runs
    ADD CONSTRAINT integration_runs_pkey PRIMARY KEY (id);


--
-- Name: interested_parties interested_parties_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_parties
    ADD CONSTRAINT interested_parties_pkey PRIMARY KEY (id);


--
-- Name: interested_party_communications interested_party_communications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_party_communications
    ADD CONSTRAINT interested_party_communications_pkey PRIMARY KEY (id);


--
-- Name: interested_party_control_links interested_party_control_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_party_control_links
    ADD CONSTRAINT interested_party_control_links_pkey PRIMARY KEY (interested_party_id, framework_slug, control_item_id);


--
-- Name: interested_party_names interested_party_names_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_party_names
    ADD CONSTRAINT interested_party_names_pkey PRIMARY KEY (id);


--
-- Name: interested_party_natures interested_party_natures_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_party_natures
    ADD CONSTRAINT interested_party_natures_pkey PRIMARY KEY (id);


--
-- Name: isms_access_control_matrix_aws_accounts isms_access_control_matrix_aws_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_access_control_matrix_aws_accounts
    ADD CONSTRAINT isms_access_control_matrix_aws_accounts_pkey PRIMARY KEY (entry_id, aws_account_id);


--
-- Name: isms_access_control_matrix_entries isms_access_control_matrix_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_access_control_matrix_entries
    ADD CONSTRAINT isms_access_control_matrix_entries_pkey PRIMARY KEY (id);


--
-- Name: isms_access_control_matrix_roles isms_access_control_matrix_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_access_control_matrix_roles
    ADD CONSTRAINT isms_access_control_matrix_roles_pkey PRIMARY KEY (entry_id, org_node_id);


--
-- Name: isms_application_configuration_entries isms_application_configuration_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_application_configuration_entries
    ADD CONSTRAINT isms_application_configuration_entries_pkey PRIMARY KEY (id);


--
-- Name: isms_aws_accounts isms_aws_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_aws_accounts
    ADD CONSTRAINT isms_aws_accounts_pkey PRIMARY KEY (id);


--
-- Name: isms_business_processes isms_business_processes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_business_processes
    ADD CONSTRAINT isms_business_processes_pkey PRIMARY KEY (id);


--
-- Name: isms_document_comments isms_document_comments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_document_comments
    ADD CONSTRAINT isms_document_comments_pkey PRIMARY KEY (id);


--
-- Name: isms_document_folders isms_document_folders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_document_folders
    ADD CONSTRAINT isms_document_folders_pkey PRIMARY KEY (id);


--
-- Name: isms_document_revisions isms_document_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_document_revisions
    ADD CONSTRAINT isms_document_revisions_pkey PRIMARY KEY (id);


--
-- Name: isms_documents isms_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_documents
    ADD CONSTRAINT isms_documents_pkey PRIMARY KEY (id);


--
-- Name: isms_effectiveness_measures isms_effectiveness_measures_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_effectiveness_measures
    ADD CONSTRAINT isms_effectiveness_measures_pkey PRIMARY KEY (id);


--
-- Name: isms_effectiveness_metric_entries isms_effectiveness_metric_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_effectiveness_metric_entries
    ADD CONSTRAINT isms_effectiveness_metric_entries_pkey PRIMARY KEY (id);


--
-- Name: isms_entity_clause_links isms_entity_clause_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_entity_clause_links
    ADD CONSTRAINT isms_entity_clause_links_pkey PRIMARY KEY (entity_type, entity_id, framework_slug, clause_id);


--
-- Name: isms_entity_control_links isms_entity_control_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_entity_control_links
    ADD CONSTRAINT isms_entity_control_links_pkey PRIMARY KEY (entity_type, entity_id, framework_slug, control_item_id);


--
-- Name: isms_licenses isms_licenses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_licenses
    ADD CONSTRAINT isms_licenses_pkey PRIMARY KEY (id);


--
-- Name: isms_meeting_attendees isms_meeting_attendees_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_meeting_attendees
    ADD CONSTRAINT isms_meeting_attendees_pkey PRIMARY KEY (meeting_id, user_id, attendance_type);


--
-- Name: isms_meeting_links isms_meeting_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_meeting_links
    ADD CONSTRAINT isms_meeting_links_pkey PRIMARY KEY (id);


--
-- Name: isms_meeting_people isms_meeting_people_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_meeting_people
    ADD CONSTRAINT isms_meeting_people_pkey PRIMARY KEY (id);


--
-- Name: isms_meetings isms_meetings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_meetings
    ADD CONSTRAINT isms_meetings_pkey PRIMARY KEY (id);


--
-- Name: isms_objective_resource_users isms_objective_resource_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_objective_resource_users
    ADD CONSTRAINT isms_objective_resource_users_pkey PRIMARY KEY (objective_id, user_id);


--
-- Name: isms_objectives isms_objectives_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_objectives
    ADD CONSTRAINT isms_objectives_pkey PRIMARY KEY (id);


--
-- Name: isms_org_node_users isms_org_node_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_org_node_users
    ADD CONSTRAINT isms_org_node_users_pkey PRIMARY KEY (org_node_id, user_id, relationship_type);


--
-- Name: isms_org_nodes isms_org_nodes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_org_nodes
    ADD CONSTRAINT isms_org_nodes_pkey PRIMARY KEY (id);


--
-- Name: isms_people isms_people_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_people
    ADD CONSTRAINT isms_people_pkey PRIMARY KEY (id);


--
-- Name: isms_people isms_people_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_people
    ADD CONSTRAINT isms_people_user_id_key UNIQUE (user_id);


--
-- Name: isms_person_assets isms_person_assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_person_assets
    ADD CONSTRAINT isms_person_assets_pkey PRIMARY KEY (person_id, asset_id);


--
-- Name: isms_person_assurances isms_person_assurances_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_person_assurances
    ADD CONSTRAINT isms_person_assurances_pkey PRIMARY KEY (id);


--
-- Name: isms_vendors isms_vendors_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_vendors
    ADD CONSTRAINT isms_vendors_name_key UNIQUE (name);


--
-- Name: isms_vendors isms_vendors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_vendors
    ADD CONSTRAINT isms_vendors_pkey PRIMARY KEY (id);


--
-- Name: keen_agents keen_agents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.keen_agents
    ADD CONSTRAINT keen_agents_pkey PRIMARY KEY (id);


--
-- Name: managed_configuration_revisions managed_configuration_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.managed_configuration_revisions
    ADD CONSTRAINT managed_configuration_revisions_pkey PRIMARY KEY (id);


--
-- Name: managed_configurations managed_configurations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.managed_configurations
    ADD CONSTRAINT managed_configurations_pkey PRIMARY KEY (name);


--
-- Name: mappings mappings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mappings
    ADD CONSTRAINT mappings_pkey PRIMARY KEY (id);


--
-- Name: mfa_challenges mfa_challenges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_challenges
    ADD CONSTRAINT mfa_challenges_pkey PRIMARY KEY (token_hash);


--
-- Name: mfa_credentials mfa_credentials_credential_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_credentials
    ADD CONSTRAINT mfa_credentials_credential_id_key UNIQUE (credential_id);


--
-- Name: mfa_credentials mfa_credentials_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_credentials
    ADD CONSTRAINT mfa_credentials_pkey PRIMARY KEY (id);


--
-- Name: mfa_recovery_codes mfa_recovery_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_recovery_codes
    ADD CONSTRAINT mfa_recovery_codes_pkey PRIMARY KEY (user_id, code_hash);


--
-- Name: oidc_login_states oidc_login_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oidc_login_states
    ADD CONSTRAINT oidc_login_states_pkey PRIMARY KEY (id);


--
-- Name: osa_control_mappings osa_control_mappings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.osa_control_mappings
    ADD CONSTRAINT osa_control_mappings_pkey PRIMARY KEY (control_id, nist_ref);


--
-- Name: permissions permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permissions
    ADD CONSTRAINT permissions_pkey PRIMARY KEY (id);


--
-- Name: pestle_business_process_relevance pestle_business_process_relevance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_business_process_relevance
    ADD CONSTRAINT pestle_business_process_relevance_pkey PRIMARY KEY (pestle_item_id, business_process_id);


--
-- Name: pestle_business_processes pestle_business_processes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_business_processes
    ADD CONSTRAINT pestle_business_processes_pkey PRIMARY KEY (id);


--
-- Name: pestle_clause_relevance pestle_clause_relevance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_clause_relevance
    ADD CONSTRAINT pestle_clause_relevance_pkey PRIMARY KEY (pestle_item_id, clause_id);


--
-- Name: pestle_items pestle_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_items
    ADD CONSTRAINT pestle_items_pkey PRIMARY KEY (id);


--
-- Name: pestle_relevance_levels pestle_relevance_levels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_relevance_levels
    ADD CONSTRAINT pestle_relevance_levels_pkey PRIMARY KEY (id);


--
-- Name: risk_asset_subcategories risk_asset_subcategories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_asset_subcategories
    ADD CONSTRAINT risk_asset_subcategories_pkey PRIMARY KEY (id);


--
-- Name: risk_assets risk_assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_assets
    ADD CONSTRAINT risk_assets_pkey PRIMARY KEY (id);


--
-- Name: risk_categories risk_categories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_categories
    ADD CONSTRAINT risk_categories_pkey PRIMARY KEY (id);


--
-- Name: risk_control_links risk_control_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_control_links
    ADD CONSTRAINT risk_control_links_pkey PRIMARY KEY (risk_id, framework_slug, control_item_id);


--
-- Name: risk_library_entries risk_library_entries_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_library_entries
    ADD CONSTRAINT risk_library_entries_name_key UNIQUE (name);


--
-- Name: risk_library_entries risk_library_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_library_entries
    ADD CONSTRAINT risk_library_entries_pkey PRIMARY KEY (id);


--
-- Name: risk_register_settings risk_register_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_register_settings
    ADD CONSTRAINT risk_register_settings_pkey PRIMARY KEY (id);


--
-- Name: risks risks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risks
    ADD CONSTRAINT risks_pkey PRIMARY KEY (id);


--
-- Name: rule_backfill_jobs rule_backfill_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_backfill_jobs
    ADD CONSTRAINT rule_backfill_jobs_pkey PRIMARY KEY (id);


--
-- Name: saved_searches saved_searches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_searches
    ADD CONSTRAINT saved_searches_pkey PRIMARY KEY (id);


--
-- Name: security_notifications security_notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_notifications
    ADD CONSTRAINT security_notifications_pkey PRIMARY KEY (id);


--
-- Name: source_ingestion_states source_ingestion_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_ingestion_states
    ADD CONSTRAINT source_ingestion_states_pkey PRIMARY KEY (source);


--
-- Name: sso_email_challenges sso_email_challenges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sso_email_challenges
    ADD CONSTRAINT sso_email_challenges_pkey PRIMARY KEY (token_hash);


--
-- Name: audit_evidence uq_audit_evidence_entity; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_evidence
    ADD CONSTRAINT uq_audit_evidence_entity UNIQUE (audit_id, entity_type, entity_id);


--
-- Name: audit_evidence uq_audit_evidence_event; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_evidence
    ADD CONSTRAINT uq_audit_evidence_event UNIQUE (audit_id, event_id);


--
-- Name: control_items uq_controlitem_ref; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.control_items
    ADD CONSTRAINT uq_controlitem_ref UNIQUE (framework_slug, type, ref);


--
-- Name: cross_framework_control_links uq_cross_framework_link; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cross_framework_control_links
    ADD CONSTRAINT uq_cross_framework_link UNIQUE (source_control_id, target_control_id);


--
-- Name: events uq_event_source_external; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT uq_event_source_external UNIQUE (source, external_id);


--
-- Name: framework_clauses uq_framework_clause_ref; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.framework_clauses
    ADD CONSTRAINT uq_framework_clause_ref UNIQUE (framework_slug, ref);


--
-- Name: groups uq_groups_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.groups
    ADD CONSTRAINT uq_groups_name UNIQUE (name);


--
-- Name: interested_parties uq_interested_parties_framework_name_nature; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_parties
    ADD CONSTRAINT uq_interested_parties_framework_name_nature UNIQUE (framework_slug, name_id, nature_id);


--
-- Name: interested_party_names uq_interested_party_names_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_party_names
    ADD CONSTRAINT uq_interested_party_names_name UNIQUE (name);


--
-- Name: interested_party_natures uq_interested_party_natures_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_party_natures
    ADD CONSTRAINT uq_interested_party_natures_name UNIQUE (name);


--
-- Name: isms_aws_accounts uq_isms_aws_accounts_account_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_aws_accounts
    ADD CONSTRAINT uq_isms_aws_accounts_account_id UNIQUE (account_id);


--
-- Name: isms_business_processes uq_isms_business_processes_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_business_processes
    ADD CONSTRAINT uq_isms_business_processes_name UNIQUE (name);


--
-- Name: isms_document_revisions uq_isms_document_revision; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_document_revisions
    ADD CONSTRAINT uq_isms_document_revision UNIQUE (document_id, version);


--
-- Name: isms_effectiveness_measures uq_isms_effectiveness_measures_metric_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_effectiveness_measures
    ADD CONSTRAINT uq_isms_effectiveness_measures_metric_key UNIQUE (metric_key);


--
-- Name: isms_licenses uq_isms_licenses_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_licenses
    ADD CONSTRAINT uq_isms_licenses_name UNIQUE (name);


--
-- Name: mappings uq_mapping_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mappings
    ADD CONSTRAINT uq_mapping_unique UNIQUE (event_id, control_item_id);


--
-- Name: oidc_login_states uq_oidc_login_states_state; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oidc_login_states
    ADD CONSTRAINT uq_oidc_login_states_state UNIQUE (state);


--
-- Name: permissions uq_permissions_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permissions
    ADD CONSTRAINT uq_permissions_code UNIQUE (code);


--
-- Name: pestle_business_processes uq_pestle_business_processes_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_business_processes
    ADD CONSTRAINT uq_pestle_business_processes_name UNIQUE (name);


--
-- Name: pestle_relevance_levels uq_pestle_relevance_levels_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_relevance_levels
    ADD CONSTRAINT uq_pestle_relevance_levels_code UNIQUE (code);


--
-- Name: risk_asset_subcategories uq_risk_asset_subcategories_category_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_asset_subcategories
    ADD CONSTRAINT uq_risk_asset_subcategories_category_name UNIQUE (category_id, name);


--
-- Name: risk_assets uq_risk_assets_name_category_subcategory; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_assets
    ADD CONSTRAINT uq_risk_assets_name_category_subcategory UNIQUE (name, category_id, subcategory_id);


--
-- Name: risk_categories uq_risk_categories_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_categories
    ADD CONSTRAINT uq_risk_categories_name UNIQUE (name);


--
-- Name: saved_searches uq_saved_search_user_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_searches
    ADD CONSTRAINT uq_saved_search_user_name UNIQUE (user_id, name);


--
-- Name: user_identities uq_user_identities_issuer_subject; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_identities
    ADD CONSTRAINT uq_user_identities_issuer_subject UNIQUE (issuer, subject);


--
-- Name: user_groups user_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_groups
    ADD CONSTRAINT user_groups_pkey PRIMARY KEY (user_id, group_id);


--
-- Name: user_identities user_identities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_identities
    ADD CONSTRAINT user_identities_pkey PRIMARY KEY (id);


--
-- Name: user_login_ips user_login_ips_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_login_ips
    ADD CONSTRAINT user_login_ips_pkey PRIMARY KEY (user_id, address);


--
-- Name: user_permissions user_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_permissions
    ADD CONSTRAINT user_permissions_pkey PRIMARY KEY (user_id, permission_id);


--
-- Name: user_sso_emails user_sso_emails_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sso_emails
    ADD CONSTRAINT user_sso_emails_pkey PRIMARY KEY (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: audit_event_retention_holds_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX audit_event_retention_holds_event ON public.audit_event_retention_holds USING btree (event_id);


--
-- Name: evidence_object_cleanup_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX evidence_object_cleanup_due ON public.evidence_object_cleanup USING btree (next_attempt_at, id);


--
-- Name: evidence_purge_one_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX evidence_purge_one_active ON public.evidence_purge_jobs USING btree ((true)) WHERE (status = ANY (ARRAY['queued'::text, 'running'::text]));


--
-- Name: ix_artifacts_event_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_artifacts_event_id ON public.artifacts USING btree (event_id);


--
-- Name: ix_artifacts_parent_artifact_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_artifacts_parent_artifact_id ON public.artifacts USING btree (parent_artifact_id);


--
-- Name: ix_artifacts_storage_uri; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_artifacts_storage_uri ON public.artifacts USING btree (storage_uri);


--
-- Name: ix_audit_attendees_audit_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_attendees_audit_id ON public.audit_attendees USING btree (audit_id);


--
-- Name: ix_audit_attendees_person_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_attendees_person_id ON public.audit_attendees USING btree (person_id);


--
-- Name: ix_audit_attendees_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_attendees_user_id ON public.audit_attendees USING btree (user_id);


--
-- Name: ix_audit_evidence_added_by_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_evidence_added_by_user_id ON public.audit_evidence USING btree (added_by_user_id);


--
-- Name: ix_audit_evidence_audit_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_evidence_audit_id ON public.audit_evidence USING btree (audit_id);


--
-- Name: ix_audit_evidence_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_evidence_entity_id ON public.audit_evidence USING btree (entity_id);


--
-- Name: ix_audit_evidence_entity_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_evidence_entity_type ON public.audit_evidence USING btree (entity_type);


--
-- Name: ix_audit_evidence_event_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_evidence_event_id ON public.audit_evidence USING btree (event_id);


--
-- Name: ix_audit_findings_audit_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_findings_audit_id ON public.audit_findings USING btree (audit_id);


--
-- Name: ix_audit_findings_control_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_findings_control_item_id ON public.audit_findings USING btree (control_item_id);


--
-- Name: ix_audit_findings_created_by_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_findings_created_by_user_id ON public.audit_findings USING btree (created_by_user_id);


--
-- Name: ix_audit_findings_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_findings_kind ON public.audit_findings USING btree (kind);


--
-- Name: ix_audit_logs_method; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_method ON public.audit_logs USING btree (method);


--
-- Name: ix_audit_logs_path; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_path ON public.audit_logs USING btree (path);


--
-- Name: ix_audit_logs_status_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_status_code ON public.audit_logs USING btree (status_code);


--
-- Name: ix_audit_logs_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_ts ON public.audit_logs USING btree (ts);


--
-- Name: ix_audit_logs_username; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_username ON public.audit_logs USING btree (username);


--
-- Name: ix_audit_scoped_clauses_audit_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_scoped_clauses_audit_id ON public.audit_scoped_clauses USING btree (audit_id);


--
-- Name: ix_audit_scoped_clauses_clause_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_scoped_clauses_clause_id ON public.audit_scoped_clauses USING btree (clause_id);


--
-- Name: ix_audit_scoped_controls_audit_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_scoped_controls_audit_id ON public.audit_scoped_controls USING btree (audit_id);


--
-- Name: ix_audit_scoped_controls_control_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_scoped_controls_control_item_id ON public.audit_scoped_controls USING btree (control_item_id);


--
-- Name: ix_audit_scoped_isms_documents_audit_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_scoped_isms_documents_audit_id ON public.audit_scoped_isms_documents USING btree (audit_id);


--
-- Name: ix_audit_scoped_isms_documents_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_scoped_isms_documents_document_id ON public.audit_scoped_isms_documents USING btree (document_id);


--
-- Name: ix_audits_created_by_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audits_created_by_user_id ON public.audits USING btree (created_by_user_id);


--
-- Name: ix_audits_framework_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audits_framework_slug ON public.audits USING btree (framework_slug);


--
-- Name: ix_audits_report_storage_uri; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audits_report_storage_uri ON public.audits USING btree (final_report_storage_uri);


--
-- Name: ix_audits_status_schedule_next_run_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audits_status_schedule_next_run_date ON public.audits USING btree (status, schedule_next_run_date);


--
-- Name: ix_audits_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audits_updated_at ON public.audits USING btree (updated_at);


--
-- Name: ix_bookstack_section_evidence_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bookstack_section_evidence_document_id ON public.bookstack_section_evidence USING btree (document_id);


--
-- Name: ix_bookstack_section_storage_uri; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bookstack_section_storage_uri ON public.bookstack_section_evidence USING btree (storage_uri);


--
-- Name: ix_control_clause_links_clause_control; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_control_clause_links_clause_control ON public.control_clause_links USING btree (clause_id, control_item_id);


--
-- Name: ix_control_clause_links_clause_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_control_clause_links_clause_id ON public.control_clause_links USING btree (clause_id);


--
-- Name: ix_control_clause_links_control_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_control_clause_links_control_item_id ON public.control_clause_links USING btree (control_item_id);


--
-- Name: ix_control_evidence_stats_last_evidence; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_control_evidence_stats_last_evidence ON public.control_evidence_stats USING btree (last_evidence);


--
-- Name: ix_control_items_framework_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_control_items_framework_id ON public.control_items USING btree (framework_slug, id);


--
-- Name: ix_control_items_framework_ref; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_control_items_framework_ref ON public.control_items USING btree (framework_slug, ref);


--
-- Name: ix_control_items_framework_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_control_items_framework_scope ON public.control_items USING btree (framework_slug, in_scope);


--
-- Name: ix_control_items_framework_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_control_items_framework_slug ON public.control_items USING btree (framework_slug);


--
-- Name: ix_control_items_mitigator_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_control_items_mitigator_fts ON public.control_items USING gin (to_tsvector('simple'::regconfig, (((((((((COALESCE(ref, ''::character varying))::text || ' '::text) || (COALESCE(title, ''::character varying))::text) || ' '::text) || (COALESCE(type, ''::character varying))::text) || ' '::text) || COALESCE((tags)::text, ''::text)) || ' '::text) || COALESCE((metadata)::text, ''::text))));


--
-- Name: ix_control_items_ref; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_control_items_ref ON public.control_items USING btree (ref);


--
-- Name: ix_control_items_title_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_control_items_title_trgm ON public.control_items USING gin (title public.gin_trgm_ops);


--
-- Name: ix_cross_framework_control_links_source_control_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cross_framework_control_links_source_control_id ON public.cross_framework_control_links USING btree (source_control_id);


--
-- Name: ix_cross_framework_control_links_target_control_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cross_framework_control_links_target_control_id ON public.cross_framework_control_links USING btree (target_control_id);


--
-- Name: ix_entity_changelogs_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_changelogs_action ON public.entity_changelogs USING btree (action);


--
-- Name: ix_entity_changelogs_changed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_changelogs_changed_at ON public.entity_changelogs USING btree (changed_at);


--
-- Name: ix_entity_changelogs_changed_by_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_changelogs_changed_by_user_id ON public.entity_changelogs USING btree (changed_by_user_id);


--
-- Name: ix_entity_changelogs_changed_by_username; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_changelogs_changed_by_username ON public.entity_changelogs USING btree (changed_by_username);


--
-- Name: ix_entity_changelogs_entity_changed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_changelogs_entity_changed_at ON public.entity_changelogs USING btree (entity_type, entity_id, changed_at);


--
-- Name: ix_entity_changelogs_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_changelogs_entity_id ON public.entity_changelogs USING btree (entity_id);


--
-- Name: ix_entity_changelogs_entity_ref; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_changelogs_entity_ref ON public.entity_changelogs USING btree (entity_ref);


--
-- Name: ix_entity_changelogs_entity_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_changelogs_entity_type ON public.entity_changelogs USING btree (entity_type);


--
-- Name: ix_entity_changelogs_request_path; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entity_changelogs_request_path ON public.entity_changelogs USING btree (request_path);


--
-- Name: ix_event_incidents_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_incidents_created_at ON public.event_incidents USING btree (created_at);


--
-- Name: ix_event_incidents_created_by_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_incidents_created_by_user_id ON public.event_incidents USING btree (created_by_user_id);


--
-- Name: ix_event_incidents_event_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_incidents_event_id ON public.event_incidents USING btree (event_id);


--
-- Name: ix_event_question_post_attachments_post_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_question_post_attachments_post_id ON public.event_question_post_attachments USING btree (post_id);


--
-- Name: ix_event_question_posts_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_question_posts_created_at ON public.event_question_posts USING btree (created_at);


--
-- Name: ix_event_question_posts_thread_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_question_posts_thread_id ON public.event_question_posts USING btree (thread_id);


--
-- Name: ix_event_question_threads_author_last_seen_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_question_threads_author_last_seen_at ON public.event_question_threads USING btree (author_last_seen_at);


--
-- Name: ix_event_question_threads_created_by_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_question_threads_created_by_user_id ON public.event_question_threads USING btree (created_by_user_id);


--
-- Name: ix_event_question_threads_event_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_question_threads_event_id ON public.event_question_threads USING btree (event_id);


--
-- Name: ix_event_question_threads_last_admin_reply_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_question_threads_last_admin_reply_at ON public.event_question_threads USING btree (last_admin_reply_at);


--
-- Name: ix_event_question_threads_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_question_threads_status ON public.event_question_threads USING btree (status);


--
-- Name: ix_event_question_threads_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_question_threads_target ON public.event_question_threads USING btree (target_type, target_id);


--
-- Name: ix_event_question_threads_target_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_question_threads_target_type ON public.event_question_threads USING btree (target_type);


--
-- Name: ix_event_question_threads_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_question_threads_updated_at ON public.event_question_threads USING btree (updated_at);


--
-- Name: ix_events_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_action ON public.events USING btree (action);


--
-- Name: ix_events_action_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_action_trgm ON public.events USING gin (action public.gin_trgm_ops);


--
-- Name: ix_events_actor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_actor ON public.events USING btree (actor);


--
-- Name: ix_events_actor_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_actor_trgm ON public.events USING gin (actor public.gin_trgm_ops);


--
-- Name: ix_events_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_created_at ON public.events USING btree (created_at);


--
-- Name: ix_events_external_id_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_external_id_trgm ON public.events USING gin (external_id public.gin_trgm_ops);


--
-- Name: ix_events_outcome; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_outcome ON public.events USING btree (outcome);


--
-- Name: ix_events_outcome_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_outcome_trgm ON public.events USING gin (outcome public.gin_trgm_ops);


--
-- Name: ix_events_search_document_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_search_document_fts ON public.events USING gin (to_tsvector('simple'::regconfig, ((((((((((((COALESCE(summary, ''::text) || ' '::text) || (COALESCE(action, ''::character varying))::text) || ' '::text) || (COALESCE(system, ''::character varying))::text) || ' '::text) || (COALESCE(actor, ''::character varying))::text) || ' '::text) || (COALESCE(source, ''::character varying))::text) || ' '::text) || (COALESCE(outcome, ''::character varying))::text) || ' '::text) || (COALESCE(external_id, ''::character varying))::text)));


--
-- Name: ix_events_severity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_severity ON public.events USING btree (severity);


--
-- Name: ix_events_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_source ON public.events USING btree (source);


--
-- Name: ix_events_source_timestamp_desc_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_source_timestamp_desc_id ON public.events USING btree (source, "timestamp" DESC, id);


--
-- Name: ix_events_source_timestamp_id_backfill; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_source_timestamp_id_backfill ON public.events USING btree (source, "timestamp", id);


--
-- Name: ix_events_source_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_source_trgm ON public.events USING gin (source public.gin_trgm_ops);


--
-- Name: ix_events_summary_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_summary_trgm ON public.events USING gin (summary public.gin_trgm_ops);


--
-- Name: ix_events_system; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_system ON public.events USING btree (system);


--
-- Name: ix_events_system_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_system_trgm ON public.events USING gin (system public.gin_trgm_ops);


--
-- Name: ix_events_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_timestamp ON public.events USING btree ("timestamp");


--
-- Name: ix_events_timestamp_desc_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_timestamp_desc_id ON public.events USING btree ("timestamp" DESC, id);


--
-- Name: ix_framework_clauses_framework_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_framework_clauses_framework_parent ON public.framework_clauses USING btree (framework_slug, parent_clause_id);


--
-- Name: ix_framework_clauses_framework_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_framework_clauses_framework_slug ON public.framework_clauses USING btree (framework_slug);


--
-- Name: ix_framework_clauses_parent_clause_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_framework_clauses_parent_clause_id ON public.framework_clauses USING btree (parent_clause_id);


--
-- Name: ix_framework_clauses_ref; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_framework_clauses_ref ON public.framework_clauses USING btree (ref);


--
-- Name: ix_group_permissions_group_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_group_permissions_group_id ON public.group_permissions USING btree (group_id);


--
-- Name: ix_group_permissions_permission_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_group_permissions_permission_id ON public.group_permissions USING btree (permission_id);


--
-- Name: ix_groups_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_groups_name ON public.groups USING btree (name);


--
-- Name: ix_integration_runs_collector_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_integration_runs_collector_id ON public.integration_runs USING btree (collector_id);


--
-- Name: ix_interested_parties_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interested_parties_created_at ON public.interested_parties USING btree (created_at);


--
-- Name: ix_interested_parties_framework_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interested_parties_framework_slug ON public.interested_parties USING btree (framework_slug);


--
-- Name: ix_interested_parties_name_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interested_parties_name_id ON public.interested_parties USING btree (name_id);


--
-- Name: ix_interested_parties_nature_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interested_parties_nature_id ON public.interested_parties USING btree (nature_id);


--
-- Name: ix_interested_party_communications_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interested_party_communications_created_at ON public.interested_party_communications USING btree (created_at);


--
-- Name: ix_interested_party_communications_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interested_party_communications_event ON public.interested_party_communications USING btree (event);


--
-- Name: ix_interested_party_communications_interested_party_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interested_party_communications_interested_party_id ON public.interested_party_communications USING btree (interested_party_id);


--
-- Name: ix_interested_party_communications_when; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interested_party_communications_when ON public.interested_party_communications USING btree ("when");


--
-- Name: ix_interested_party_communications_with_whom; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interested_party_communications_with_whom ON public.interested_party_communications USING btree (with_whom);


--
-- Name: ix_interested_party_control_links_framework_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interested_party_control_links_framework_slug ON public.interested_party_control_links USING btree (framework_slug);


--
-- Name: ix_interested_party_names_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interested_party_names_created_at ON public.interested_party_names USING btree (created_at);


--
-- Name: ix_interested_party_names_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interested_party_names_name ON public.interested_party_names USING btree (name);


--
-- Name: ix_interested_party_natures_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interested_party_natures_created_at ON public.interested_party_natures USING btree (created_at);


--
-- Name: ix_interested_party_natures_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interested_party_natures_name ON public.interested_party_natures USING btree (name);


--
-- Name: ix_isms_access_control_matrix_aws_accounts_aws_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_access_control_matrix_aws_accounts_aws_account_id ON public.isms_access_control_matrix_aws_accounts USING btree (aws_account_id);


--
-- Name: ix_isms_access_control_matrix_aws_accounts_entry_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_access_control_matrix_aws_accounts_entry_id ON public.isms_access_control_matrix_aws_accounts USING btree (entry_id);


--
-- Name: ix_isms_access_control_matrix_entries_approved_by_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_access_control_matrix_entries_approved_by_user_id ON public.isms_access_control_matrix_entries USING btree (approved_by_user_id);


--
-- Name: ix_isms_access_control_matrix_entries_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_access_control_matrix_entries_created_at ON public.isms_access_control_matrix_entries USING btree (created_at);


--
-- Name: ix_isms_access_control_matrix_entries_service_asset_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_access_control_matrix_entries_service_asset_id ON public.isms_access_control_matrix_entries USING btree (service_asset_id);


--
-- Name: ix_isms_access_control_matrix_entries_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_access_control_matrix_entries_status ON public.isms_access_control_matrix_entries USING btree (status);


--
-- Name: ix_isms_access_control_matrix_roles_entry_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_access_control_matrix_roles_entry_id ON public.isms_access_control_matrix_roles USING btree (entry_id);


--
-- Name: ix_isms_access_control_matrix_roles_org_node_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_access_control_matrix_roles_org_node_id ON public.isms_access_control_matrix_roles USING btree (org_node_id);


--
-- Name: ix_isms_application_configuration_entries_asset_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_application_configuration_entries_asset_id ON public.isms_application_configuration_entries USING btree (asset_id);


--
-- Name: ix_isms_application_configuration_entries_business_process_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_application_configuration_entries_business_process_id ON public.isms_application_configuration_entries USING btree (business_process_id);


--
-- Name: ix_isms_application_configuration_entries_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_application_configuration_entries_created_at ON public.isms_application_configuration_entries USING btree (created_at);


--
-- Name: ix_isms_application_configuration_entries_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_application_configuration_entries_document_id ON public.isms_application_configuration_entries USING btree (document_id);


--
-- Name: ix_isms_application_configuration_entries_org_node_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_application_configuration_entries_org_node_id ON public.isms_application_configuration_entries USING btree (org_node_id);


--
-- Name: ix_isms_application_configuration_entries_source_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_application_configuration_entries_source_type ON public.isms_application_configuration_entries USING btree (source_type);


--
-- Name: ix_isms_application_configuration_entries_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_application_configuration_entries_user_id ON public.isms_application_configuration_entries USING btree (user_id);


--
-- Name: ix_isms_application_configuration_entries_value; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_application_configuration_entries_value ON public.isms_application_configuration_entries USING btree (value);


--
-- Name: ix_isms_aws_accounts_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_aws_accounts_created_at ON public.isms_aws_accounts USING btree (created_at);


--
-- Name: ix_isms_aws_accounts_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_aws_accounts_name ON public.isms_aws_accounts USING btree (name);


--
-- Name: ix_isms_business_processes_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_business_processes_name ON public.isms_business_processes USING btree (name);


--
-- Name: ix_isms_document_comments_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_document_comments_document_id ON public.isms_document_comments USING btree (document_id);


--
-- Name: ix_isms_document_revisions_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_document_revisions_document_id ON public.isms_document_revisions USING btree (document_id);


--
-- Name: ix_isms_documents_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_documents_created_at ON public.isms_documents USING btree (created_at);


--
-- Name: ix_isms_documents_document_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_documents_document_type ON public.isms_documents USING btree (document_type);


--
-- Name: ix_isms_documents_folder_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_documents_folder_id ON public.isms_documents USING btree (folder_id);


--
-- Name: ix_isms_documents_storage_uri; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_documents_storage_uri ON public.isms_documents USING btree (storage_uri);


--
-- Name: ix_isms_documents_title; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_documents_title ON public.isms_documents USING btree (title);


--
-- Name: ix_isms_effectiveness_measures_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_effectiveness_measures_created_at ON public.isms_effectiveness_measures USING btree (created_at);


--
-- Name: ix_isms_effectiveness_measures_framework_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_effectiveness_measures_framework_slug ON public.isms_effectiveness_measures USING btree (framework_slug);


--
-- Name: ix_isms_effectiveness_measures_metric_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_effectiveness_measures_metric_key ON public.isms_effectiveness_measures USING btree (metric_key);


--
-- Name: ix_isms_effectiveness_measures_owner_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_effectiveness_measures_owner_user_id ON public.isms_effectiveness_measures USING btree (owner_user_id);


--
-- Name: ix_isms_effectiveness_measures_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_effectiveness_measures_updated_at ON public.isms_effectiveness_measures USING btree (updated_at);


--
-- Name: ix_isms_effectiveness_metric_entries_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_effectiveness_metric_entries_created_at ON public.isms_effectiveness_metric_entries USING btree (created_at);


--
-- Name: ix_isms_effectiveness_metric_entries_measure_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_effectiveness_metric_entries_measure_id ON public.isms_effectiveness_metric_entries USING btree (measure_id);


--
-- Name: ix_isms_effectiveness_metric_entries_period_end; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_effectiveness_metric_entries_period_end ON public.isms_effectiveness_metric_entries USING btree (period_end);


--
-- Name: ix_isms_effectiveness_metric_entries_period_start; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_effectiveness_metric_entries_period_start ON public.isms_effectiveness_metric_entries USING btree (period_start);


--
-- Name: ix_isms_effectiveness_metric_entries_recorded_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_effectiveness_metric_entries_recorded_at ON public.isms_effectiveness_metric_entries USING btree (recorded_at);


--
-- Name: ix_isms_effectiveness_metric_entries_source_event_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_effectiveness_metric_entries_source_event_id ON public.isms_effectiveness_metric_entries USING btree (source_event_id);


--
-- Name: ix_isms_effectiveness_metric_entries_source_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_effectiveness_metric_entries_source_type ON public.isms_effectiveness_metric_entries USING btree (source_type);


--
-- Name: ix_isms_entity_clause_links_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_entity_clause_links_entity ON public.isms_entity_clause_links USING btree (entity_type, entity_id);


--
-- Name: ix_isms_entity_clause_links_framework_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_entity_clause_links_framework_slug ON public.isms_entity_clause_links USING btree (framework_slug);


--
-- Name: ix_isms_entity_control_links_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_entity_control_links_entity ON public.isms_entity_control_links USING btree (entity_type, entity_id);


--
-- Name: ix_isms_entity_control_links_framework_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_entity_control_links_framework_slug ON public.isms_entity_control_links USING btree (framework_slug);


--
-- Name: ix_isms_licenses_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_licenses_created_at ON public.isms_licenses USING btree (created_at);


--
-- Name: ix_isms_licenses_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_licenses_name ON public.isms_licenses USING btree (name);


--
-- Name: ix_isms_meeting_links_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_meeting_links_document_id ON public.isms_meeting_links USING btree (document_id);


--
-- Name: ix_isms_meeting_links_link_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_meeting_links_link_type ON public.isms_meeting_links USING btree (link_type);


--
-- Name: ix_isms_meeting_links_meeting_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_meeting_links_meeting_id ON public.isms_meeting_links USING btree (meeting_id);


--
-- Name: ix_isms_meeting_people_meeting_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_meeting_people_meeting_id ON public.isms_meeting_people USING btree (meeting_id);


--
-- Name: ix_isms_meeting_people_person_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_meeting_people_person_id ON public.isms_meeting_people USING btree (person_id);


--
-- Name: ix_isms_meetings_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_meetings_created_at ON public.isms_meetings USING btree (created_at);


--
-- Name: ix_isms_meetings_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_meetings_date ON public.isms_meetings USING btree (date);


--
-- Name: ix_isms_meetings_title; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_meetings_title ON public.isms_meetings USING btree (title);


--
-- Name: ix_isms_objectives_completion_target_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_objectives_completion_target_date ON public.isms_objectives USING btree (completion_target_date);


--
-- Name: ix_isms_objectives_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_objectives_created_at ON public.isms_objectives USING btree (created_at);


--
-- Name: ix_isms_objectives_owner_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_objectives_owner_user_id ON public.isms_objectives USING btree (owner_user_id);


--
-- Name: ix_isms_objectives_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_objectives_status ON public.isms_objectives USING btree (status);


--
-- Name: ix_isms_org_nodes_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_org_nodes_created_at ON public.isms_org_nodes USING btree (created_at);


--
-- Name: ix_isms_org_nodes_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_org_nodes_name ON public.isms_org_nodes USING btree (name);


--
-- Name: ix_isms_org_nodes_node_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_org_nodes_node_type ON public.isms_org_nodes USING btree (node_type);


--
-- Name: ix_isms_org_nodes_parent_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_org_nodes_parent_id ON public.isms_org_nodes USING btree (parent_id);


--
-- Name: ix_isms_people_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_people_name ON public.isms_people USING btree (name);


--
-- Name: ix_isms_person_assurances_person_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_isms_person_assurances_person_id ON public.isms_person_assurances USING btree (person_id);


--
-- Name: ix_managed_configuration_revisions_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_managed_configuration_revisions_name ON public.managed_configuration_revisions USING btree (name);


--
-- Name: ix_mappings_control_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mappings_control_event ON public.mappings USING btree (control_item_id, event_id);


--
-- Name: ix_mappings_control_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mappings_control_item_id ON public.mappings USING btree (control_item_id);


--
-- Name: ix_mappings_control_mapped_at_desc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mappings_control_mapped_at_desc ON public.mappings USING btree (control_item_id, mapped_at DESC);


--
-- Name: ix_mappings_event_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mappings_event_id ON public.mappings USING btree (event_id);


--
-- Name: ix_mfa_challenges_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mfa_challenges_expires_at ON public.mfa_challenges USING btree (expires_at);


--
-- Name: ix_mfa_challenges_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mfa_challenges_user_id ON public.mfa_challenges USING btree (user_id);


--
-- Name: ix_mfa_credentials_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mfa_credentials_user_id ON public.mfa_credentials USING btree (user_id);


--
-- Name: ix_oidc_login_states_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_oidc_login_states_state ON public.oidc_login_states USING btree (state);


--
-- Name: ix_osa_control_mappings_nist_ref; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_osa_control_mappings_nist_ref ON public.osa_control_mappings USING btree (nist_ref);


--
-- Name: ix_permissions_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_permissions_code ON public.permissions USING btree (code);


--
-- Name: ix_pestle_business_process_relevance_relevance_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pestle_business_process_relevance_relevance_id ON public.pestle_business_process_relevance USING btree (relevance_id);


--
-- Name: ix_pestle_business_processes_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pestle_business_processes_created_at ON public.pestle_business_processes USING btree (created_at);


--
-- Name: ix_pestle_business_processes_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pestle_business_processes_name ON public.pestle_business_processes USING btree (name);


--
-- Name: ix_pestle_clause_relevance_clause_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pestle_clause_relevance_clause_id ON public.pestle_clause_relevance USING btree (clause_id);


--
-- Name: ix_pestle_clause_relevance_relevance_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pestle_clause_relevance_relevance_id ON public.pestle_clause_relevance USING btree (relevance_id);


--
-- Name: ix_pestle_items_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pestle_items_created_at ON public.pestle_items USING btree (created_at);


--
-- Name: ix_pestle_items_framework_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pestle_items_framework_slug ON public.pestle_items USING btree (framework_slug);


--
-- Name: ix_pestle_items_lens; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pestle_items_lens ON public.pestle_items USING btree (lens);


--
-- Name: ix_pestle_items_overall_relevance_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pestle_items_overall_relevance_id ON public.pestle_items USING btree (overall_relevance_id);


--
-- Name: ix_pestle_items_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pestle_items_type ON public.pestle_items USING btree (type);


--
-- Name: ix_pestle_relevance_levels_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pestle_relevance_levels_code ON public.pestle_relevance_levels USING btree (code);


--
-- Name: ix_question_attachments_artifact; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_question_attachments_artifact ON public.event_question_post_attachments USING btree (artifact_id);


--
-- Name: ix_risk_asset_subcategories_category_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risk_asset_subcategories_category_id ON public.risk_asset_subcategories USING btree (category_id);


--
-- Name: ix_risk_asset_subcategories_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risk_asset_subcategories_name ON public.risk_asset_subcategories USING btree (name);


--
-- Name: ix_risk_assets_category_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risk_assets_category_id ON public.risk_assets USING btree (category_id);


--
-- Name: ix_risk_assets_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risk_assets_created_at ON public.risk_assets USING btree (created_at);


--
-- Name: ix_risk_assets_license_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risk_assets_license_id ON public.risk_assets USING btree (license_id);


--
-- Name: ix_risk_assets_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risk_assets_name ON public.risk_assets USING btree (name);


--
-- Name: ix_risk_assets_owner_org_node_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risk_assets_owner_org_node_id ON public.risk_assets USING btree (owner_org_node_id);


--
-- Name: ix_risk_assets_register_held_by_org_node_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risk_assets_register_held_by_org_node_id ON public.risk_assets USING btree (register_held_by_org_node_id);


--
-- Name: ix_risk_assets_subcategory_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risk_assets_subcategory_id ON public.risk_assets USING btree (subcategory_id);


--
-- Name: ix_risk_assets_vendor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risk_assets_vendor_id ON public.risk_assets USING btree (vendor_id);


--
-- Name: ix_risk_categories_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risk_categories_name ON public.risk_categories USING btree (name);


--
-- Name: ix_risk_control_links_framework_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risk_control_links_framework_slug ON public.risk_control_links USING btree (framework_slug);


--
-- Name: ix_risks_asset_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risks_asset_id ON public.risks USING btree (asset_id);


--
-- Name: ix_risks_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risks_created_at ON public.risks USING btree (created_at);


--
-- Name: ix_risks_risk_owner_role_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risks_risk_owner_role_id ON public.risks USING btree (risk_owner_role_id);


--
-- Name: ix_risks_risk_owner_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_risks_risk_owner_user_id ON public.risks USING btree (risk_owner_user_id);


--
-- Name: ix_rule_backfill_jobs_rule_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_backfill_jobs_rule_id ON public.rule_backfill_jobs USING btree (rule_id);


--
-- Name: ix_rule_backfill_jobs_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rule_backfill_jobs_source ON public.rule_backfill_jobs USING btree (source);


--
-- Name: ix_saved_searches_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_saved_searches_user_id ON public.saved_searches USING btree (user_id);


--
-- Name: ix_security_notifications_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_security_notifications_pending ON public.security_notifications USING btree (status, next_attempt_at);


--
-- Name: ix_sso_email_challenges_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sso_email_challenges_user_id ON public.sso_email_challenges USING btree (user_id);


--
-- Name: ix_user_groups_group_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_groups_group_id ON public.user_groups USING btree (group_id);


--
-- Name: ix_user_groups_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_groups_user_id ON public.user_groups USING btree (user_id);


--
-- Name: ix_user_identities_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_identities_user_id ON public.user_identities USING btree (user_id);


--
-- Name: ix_user_permissions_permission_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_permissions_permission_id ON public.user_permissions USING btree (permission_id);


--
-- Name: ix_user_permissions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_permissions_user_id ON public.user_permissions USING btree (user_id);


--
-- Name: ix_user_sso_emails_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_sso_emails_user_id ON public.user_sso_emails USING btree (user_id);


--
-- Name: ix_users_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_is_active ON public.users USING btree (is_active);


--
-- Name: ix_users_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_role ON public.users USING btree (role);


--
-- Name: ix_users_username; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_username ON public.users USING btree (username);


--
-- Name: audit_evidence keen_hold_sampled_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER keen_hold_sampled_events AFTER INSERT OR UPDATE ON public.audit_evidence FOR EACH ROW EXECUTE FUNCTION public.keen_hold_sampled_events();


--
-- Name: isms_effectiveness_metric_entries keen_hold_sampled_metric_event; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER keen_hold_sampled_metric_event AFTER INSERT OR UPDATE ON public.isms_effectiveness_metric_entries FOR EACH ROW EXECUTE FUNCTION public.keen_hold_sampled_metric_event();


--
-- Name: events trg_control_evidence_stats_events_timestamp; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_control_evidence_stats_events_timestamp AFTER UPDATE OF "timestamp" ON public.events FOR EACH ROW EXECUTE FUNCTION public.keen_control_evidence_stats_from_event_timestamp();


--
-- Name: mappings trg_control_evidence_stats_mappings_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_control_evidence_stats_mappings_delete AFTER DELETE ON public.mappings FOR EACH ROW EXECUTE FUNCTION public.keen_control_evidence_stats_from_mapping();


--
-- Name: mappings trg_control_evidence_stats_mappings_insert; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_control_evidence_stats_mappings_insert AFTER INSERT ON public.mappings FOR EACH ROW EXECUTE FUNCTION public.keen_control_evidence_stats_from_mapping();


--
-- Name: mappings trg_control_evidence_stats_mappings_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_control_evidence_stats_mappings_update AFTER UPDATE OF event_id, control_item_id ON public.mappings FOR EACH ROW EXECUTE FUNCTION public.keen_control_evidence_stats_from_mapping();


--
-- Name: artifacts artifacts_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifacts
    ADD CONSTRAINT artifacts_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id);


--
-- Name: audit_attendees audit_attendees_audit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_attendees
    ADD CONSTRAINT audit_attendees_audit_id_fkey FOREIGN KEY (audit_id) REFERENCES public.audits(id) ON DELETE CASCADE;


--
-- Name: audit_attendees audit_attendees_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_attendees
    ADD CONSTRAINT audit_attendees_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.isms_people(id) ON DELETE SET NULL;


--
-- Name: audit_event_retention_holds audit_event_retention_holds_audit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_event_retention_holds
    ADD CONSTRAINT audit_event_retention_holds_audit_id_fkey FOREIGN KEY (audit_id) REFERENCES public.audits(id) ON DELETE CASCADE;


--
-- Name: audit_event_retention_holds audit_event_retention_holds_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_event_retention_holds
    ADD CONSTRAINT audit_event_retention_holds_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id) ON DELETE RESTRICT;


--
-- Name: audit_evidence audit_evidence_added_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_evidence
    ADD CONSTRAINT audit_evidence_added_by_user_id_fkey FOREIGN KEY (added_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: audit_evidence audit_evidence_audit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_evidence
    ADD CONSTRAINT audit_evidence_audit_id_fkey FOREIGN KEY (audit_id) REFERENCES public.audits(id) ON DELETE CASCADE;


--
-- Name: audit_evidence audit_evidence_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_evidence
    ADD CONSTRAINT audit_evidence_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id) ON DELETE SET NULL;


--
-- Name: audit_findings audit_findings_audit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_findings
    ADD CONSTRAINT audit_findings_audit_id_fkey FOREIGN KEY (audit_id) REFERENCES public.audits(id) ON DELETE CASCADE;


--
-- Name: audit_findings audit_findings_control_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_findings
    ADD CONSTRAINT audit_findings_control_item_id_fkey FOREIGN KEY (control_item_id) REFERENCES public.control_items(id) ON DELETE SET NULL;


--
-- Name: audit_findings audit_findings_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_findings
    ADD CONSTRAINT audit_findings_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: audit_scoped_clauses audit_scoped_clauses_audit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_scoped_clauses
    ADD CONSTRAINT audit_scoped_clauses_audit_id_fkey FOREIGN KEY (audit_id) REFERENCES public.audits(id) ON DELETE CASCADE;


--
-- Name: audit_scoped_clauses audit_scoped_clauses_clause_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_scoped_clauses
    ADD CONSTRAINT audit_scoped_clauses_clause_id_fkey FOREIGN KEY (clause_id) REFERENCES public.framework_clauses(id) ON DELETE CASCADE;


--
-- Name: audit_scoped_controls audit_scoped_controls_audit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_scoped_controls
    ADD CONSTRAINT audit_scoped_controls_audit_id_fkey FOREIGN KEY (audit_id) REFERENCES public.audits(id) ON DELETE CASCADE;


--
-- Name: audit_scoped_controls audit_scoped_controls_control_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_scoped_controls
    ADD CONSTRAINT audit_scoped_controls_control_item_id_fkey FOREIGN KEY (control_item_id) REFERENCES public.control_items(id) ON DELETE CASCADE;


--
-- Name: audit_scoped_isms_documents audit_scoped_isms_documents_audit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_scoped_isms_documents
    ADD CONSTRAINT audit_scoped_isms_documents_audit_id_fkey FOREIGN KEY (audit_id) REFERENCES public.audits(id) ON DELETE CASCADE;


--
-- Name: audit_scoped_isms_documents audit_scoped_isms_documents_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_scoped_isms_documents
    ADD CONSTRAINT audit_scoped_isms_documents_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.isms_documents(id) ON DELETE CASCADE;


--
-- Name: audits audits_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audits
    ADD CONSTRAINT audits_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: audits audits_final_report_uploaded_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audits
    ADD CONSTRAINT audits_final_report_uploaded_by_user_id_fkey FOREIGN KEY (final_report_uploaded_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: bookstack_section_evidence bookstack_section_evidence_captured_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookstack_section_evidence
    ADD CONSTRAINT bookstack_section_evidence_captured_by_user_id_fkey FOREIGN KEY (captured_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: bookstack_section_evidence bookstack_section_evidence_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookstack_section_evidence
    ADD CONSTRAINT bookstack_section_evidence_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.isms_documents(id) ON DELETE RESTRICT;


--
-- Name: bookstack_section_evidence bookstack_section_evidence_target_clause_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookstack_section_evidence
    ADD CONSTRAINT bookstack_section_evidence_target_clause_id_fkey FOREIGN KEY (target_clause_id) REFERENCES public.framework_clauses(id) ON DELETE SET NULL;


--
-- Name: bookstack_section_evidence bookstack_section_evidence_target_control_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookstack_section_evidence
    ADD CONSTRAINT bookstack_section_evidence_target_control_id_fkey FOREIGN KEY (target_control_id) REFERENCES public.control_items(id) ON DELETE SET NULL;


--
-- Name: control_clause_links control_clause_links_clause_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.control_clause_links
    ADD CONSTRAINT control_clause_links_clause_id_fkey FOREIGN KEY (clause_id) REFERENCES public.framework_clauses(id) ON DELETE CASCADE;


--
-- Name: control_clause_links control_clause_links_control_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.control_clause_links
    ADD CONSTRAINT control_clause_links_control_item_id_fkey FOREIGN KEY (control_item_id) REFERENCES public.control_items(id) ON DELETE CASCADE;


--
-- Name: control_evidence_stats control_evidence_stats_control_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.control_evidence_stats
    ADD CONSTRAINT control_evidence_stats_control_item_id_fkey FOREIGN KEY (control_item_id) REFERENCES public.control_items(id) ON DELETE CASCADE;


--
-- Name: cross_framework_control_links cross_framework_control_links_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cross_framework_control_links
    ADD CONSTRAINT cross_framework_control_links_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: cross_framework_control_links cross_framework_control_links_source_control_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cross_framework_control_links
    ADD CONSTRAINT cross_framework_control_links_source_control_id_fkey FOREIGN KEY (source_control_id) REFERENCES public.control_items(id) ON DELETE CASCADE;


--
-- Name: cross_framework_control_links cross_framework_control_links_target_control_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cross_framework_control_links
    ADD CONSTRAINT cross_framework_control_links_target_control_id_fkey FOREIGN KEY (target_control_id) REFERENCES public.control_items(id) ON DELETE CASCADE;


--
-- Name: entity_changelogs entity_changelogs_changed_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_changelogs
    ADD CONSTRAINT entity_changelogs_changed_by_user_id_fkey FOREIGN KEY (changed_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: event_incidents event_incidents_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_incidents
    ADD CONSTRAINT event_incidents_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: event_incidents event_incidents_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_incidents
    ADD CONSTRAINT event_incidents_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id) ON DELETE CASCADE;


--
-- Name: event_question_post_attachments event_question_post_attachments_artifact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_question_post_attachments
    ADD CONSTRAINT event_question_post_attachments_artifact_id_fkey FOREIGN KEY (artifact_id) REFERENCES public.artifacts(id) ON DELETE CASCADE;


--
-- Name: event_question_post_attachments event_question_post_attachments_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_question_post_attachments
    ADD CONSTRAINT event_question_post_attachments_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.event_question_posts(id) ON DELETE CASCADE;


--
-- Name: event_question_posts event_question_posts_author_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_question_posts
    ADD CONSTRAINT event_question_posts_author_user_id_fkey FOREIGN KEY (author_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: event_question_posts event_question_posts_thread_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_question_posts
    ADD CONSTRAINT event_question_posts_thread_id_fkey FOREIGN KEY (thread_id) REFERENCES public.event_question_threads(id) ON DELETE CASCADE;


--
-- Name: event_question_threads event_question_threads_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_question_threads
    ADD CONSTRAINT event_question_threads_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: event_question_threads event_question_threads_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_question_threads
    ADD CONSTRAINT event_question_threads_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id) ON DELETE CASCADE;


--
-- Name: audit_attendees fk_audit_attendees_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_attendees
    ADD CONSTRAINT fk_audit_attendees_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: audits fk_audits_schedule_last_created_audit_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audits
    ADD CONSTRAINT fk_audits_schedule_last_created_audit_id FOREIGN KEY (schedule_last_created_audit_id) REFERENCES public.audits(id) ON DELETE SET NULL;


--
-- Name: isms_application_configuration_entries fk_isms_app_config_asset_id_risk_assets; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_application_configuration_entries
    ADD CONSTRAINT fk_isms_app_config_asset_id_risk_assets FOREIGN KEY (asset_id) REFERENCES public.risk_assets(id) ON DELETE CASCADE;


--
-- Name: risk_assets fk_risk_assets_created_by_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_assets
    ADD CONSTRAINT fk_risk_assets_created_by_user_id_users FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: risk_assets fk_risk_assets_license_id_isms_licenses; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_assets
    ADD CONSTRAINT fk_risk_assets_license_id_isms_licenses FOREIGN KEY (license_id) REFERENCES public.isms_licenses(id) ON DELETE SET NULL;


--
-- Name: risk_assets fk_risk_assets_owner_org_node_id_isms_org_nodes; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_assets
    ADD CONSTRAINT fk_risk_assets_owner_org_node_id_isms_org_nodes FOREIGN KEY (owner_org_node_id) REFERENCES public.isms_org_nodes(id) ON DELETE SET NULL;


--
-- Name: risk_assets fk_risk_assets_register_held_by_org_node_id_isms_org_nodes; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_assets
    ADD CONSTRAINT fk_risk_assets_register_held_by_org_node_id_isms_org_nodes FOREIGN KEY (register_held_by_org_node_id) REFERENCES public.isms_org_nodes(id) ON DELETE SET NULL;


--
-- Name: risks fk_risks_asset_id_risk_assets; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risks
    ADD CONSTRAINT fk_risks_asset_id_risk_assets FOREIGN KEY (asset_id) REFERENCES public.risk_assets(id);


--
-- Name: risks fk_risks_owner_role; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risks
    ADD CONSTRAINT fk_risks_owner_role FOREIGN KEY (risk_owner_role_id) REFERENCES public.isms_org_nodes(id) ON DELETE SET NULL;


--
-- Name: framework_clauses framework_clauses_parent_clause_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.framework_clauses
    ADD CONSTRAINT framework_clauses_parent_clause_id_fkey FOREIGN KEY (parent_clause_id) REFERENCES public.framework_clauses(id) ON DELETE CASCADE;


--
-- Name: group_permissions group_permissions_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_permissions
    ADD CONSTRAINT group_permissions_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.groups(id) ON DELETE CASCADE;


--
-- Name: group_permissions group_permissions_permission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_permissions
    ADD CONSTRAINT group_permissions_permission_id_fkey FOREIGN KEY (permission_id) REFERENCES public.permissions(id) ON DELETE CASCADE;


--
-- Name: integration_collectors integration_collectors_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integration_collectors
    ADD CONSTRAINT integration_collectors_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES public.integration_connections(id);


--
-- Name: integration_revisions integration_revisions_collector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integration_revisions
    ADD CONSTRAINT integration_revisions_collector_id_fkey FOREIGN KEY (collector_id) REFERENCES public.integration_collectors(id);


--
-- Name: integration_revisions integration_revisions_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integration_revisions
    ADD CONSTRAINT integration_revisions_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES public.integration_connections(id);


--
-- Name: integration_runs integration_runs_collector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integration_runs
    ADD CONSTRAINT integration_runs_collector_id_fkey FOREIGN KEY (collector_id) REFERENCES public.integration_collectors(id);


--
-- Name: integration_runs integration_runs_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integration_runs
    ADD CONSTRAINT integration_runs_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES public.integration_connections(id);


--
-- Name: interested_parties interested_parties_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_parties
    ADD CONSTRAINT interested_parties_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: interested_parties interested_parties_name_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_parties
    ADD CONSTRAINT interested_parties_name_id_fkey FOREIGN KEY (name_id) REFERENCES public.interested_party_names(id);


--
-- Name: interested_parties interested_parties_nature_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_parties
    ADD CONSTRAINT interested_parties_nature_id_fkey FOREIGN KEY (nature_id) REFERENCES public.interested_party_natures(id);


--
-- Name: interested_party_communications interested_party_communications_interested_party_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_party_communications
    ADD CONSTRAINT interested_party_communications_interested_party_id_fkey FOREIGN KEY (interested_party_id) REFERENCES public.interested_parties(id) ON DELETE CASCADE;


--
-- Name: interested_party_control_links interested_party_control_links_control_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_party_control_links
    ADD CONSTRAINT interested_party_control_links_control_item_id_fkey FOREIGN KEY (control_item_id) REFERENCES public.control_items(id) ON DELETE CASCADE;


--
-- Name: interested_party_control_links interested_party_control_links_interested_party_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_party_control_links
    ADD CONSTRAINT interested_party_control_links_interested_party_id_fkey FOREIGN KEY (interested_party_id) REFERENCES public.interested_parties(id) ON DELETE CASCADE;


--
-- Name: interested_party_names interested_party_names_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_party_names
    ADD CONSTRAINT interested_party_names_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: interested_party_natures interested_party_natures_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interested_party_natures
    ADD CONSTRAINT interested_party_natures_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_access_control_matrix_aws_accounts isms_access_control_matrix_aws_accounts_aws_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_access_control_matrix_aws_accounts
    ADD CONSTRAINT isms_access_control_matrix_aws_accounts_aws_account_id_fkey FOREIGN KEY (aws_account_id) REFERENCES public.isms_aws_accounts(id) ON DELETE CASCADE;


--
-- Name: isms_access_control_matrix_aws_accounts isms_access_control_matrix_aws_accounts_entry_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_access_control_matrix_aws_accounts
    ADD CONSTRAINT isms_access_control_matrix_aws_accounts_entry_id_fkey FOREIGN KEY (entry_id) REFERENCES public.isms_access_control_matrix_entries(id) ON DELETE CASCADE;


--
-- Name: isms_access_control_matrix_entries isms_access_control_matrix_entries_approved_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_access_control_matrix_entries
    ADD CONSTRAINT isms_access_control_matrix_entries_approved_by_user_id_fkey FOREIGN KEY (approved_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_access_control_matrix_entries isms_access_control_matrix_entries_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_access_control_matrix_entries
    ADD CONSTRAINT isms_access_control_matrix_entries_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_access_control_matrix_entries isms_access_control_matrix_entries_service_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_access_control_matrix_entries
    ADD CONSTRAINT isms_access_control_matrix_entries_service_asset_id_fkey FOREIGN KEY (service_asset_id) REFERENCES public.risk_assets(id) ON DELETE SET NULL;


--
-- Name: isms_access_control_matrix_roles isms_access_control_matrix_roles_entry_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_access_control_matrix_roles
    ADD CONSTRAINT isms_access_control_matrix_roles_entry_id_fkey FOREIGN KEY (entry_id) REFERENCES public.isms_access_control_matrix_entries(id) ON DELETE CASCADE;


--
-- Name: isms_access_control_matrix_roles isms_access_control_matrix_roles_org_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_access_control_matrix_roles
    ADD CONSTRAINT isms_access_control_matrix_roles_org_node_id_fkey FOREIGN KEY (org_node_id) REFERENCES public.isms_org_nodes(id) ON DELETE CASCADE;


--
-- Name: isms_application_configuration_entries isms_application_configuration_entries_business_process_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_application_configuration_entries
    ADD CONSTRAINT isms_application_configuration_entries_business_process_id_fkey FOREIGN KEY (business_process_id) REFERENCES public.isms_business_processes(id) ON DELETE CASCADE;


--
-- Name: isms_application_configuration_entries isms_application_configuration_entries_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_application_configuration_entries
    ADD CONSTRAINT isms_application_configuration_entries_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_application_configuration_entries isms_application_configuration_entries_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_application_configuration_entries
    ADD CONSTRAINT isms_application_configuration_entries_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.isms_documents(id) ON DELETE CASCADE;


--
-- Name: isms_application_configuration_entries isms_application_configuration_entries_org_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_application_configuration_entries
    ADD CONSTRAINT isms_application_configuration_entries_org_node_id_fkey FOREIGN KEY (org_node_id) REFERENCES public.isms_org_nodes(id) ON DELETE CASCADE;


--
-- Name: isms_application_configuration_entries isms_application_configuration_entries_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_application_configuration_entries
    ADD CONSTRAINT isms_application_configuration_entries_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: isms_aws_accounts isms_aws_accounts_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_aws_accounts
    ADD CONSTRAINT isms_aws_accounts_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_document_comments isms_document_comments_author_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_document_comments
    ADD CONSTRAINT isms_document_comments_author_user_id_fkey FOREIGN KEY (author_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_document_comments isms_document_comments_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_document_comments
    ADD CONSTRAINT isms_document_comments_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.isms_documents(id) ON DELETE CASCADE;


--
-- Name: isms_document_folders isms_document_folders_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_document_folders
    ADD CONSTRAINT isms_document_folders_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.isms_document_folders(id) ON DELETE SET NULL;


--
-- Name: isms_document_revisions isms_document_revisions_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_document_revisions
    ADD CONSTRAINT isms_document_revisions_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_document_revisions isms_document_revisions_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_document_revisions
    ADD CONSTRAINT isms_document_revisions_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.isms_documents(id) ON DELETE CASCADE;


--
-- Name: isms_documents isms_documents_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_documents
    ADD CONSTRAINT isms_documents_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_documents isms_documents_folder_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_documents
    ADD CONSTRAINT isms_documents_folder_id_fkey FOREIGN KEY (folder_id) REFERENCES public.isms_document_folders(id) ON DELETE SET NULL;


--
-- Name: isms_documents isms_documents_uploaded_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_documents
    ADD CONSTRAINT isms_documents_uploaded_by_user_id_fkey FOREIGN KEY (uploaded_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_effectiveness_measures isms_effectiveness_measures_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_effectiveness_measures
    ADD CONSTRAINT isms_effectiveness_measures_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_effectiveness_measures isms_effectiveness_measures_owner_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_effectiveness_measures
    ADD CONSTRAINT isms_effectiveness_measures_owner_user_id_fkey FOREIGN KEY (owner_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_effectiveness_metric_entries isms_effectiveness_metric_entries_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_effectiveness_metric_entries
    ADD CONSTRAINT isms_effectiveness_metric_entries_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_effectiveness_metric_entries isms_effectiveness_metric_entries_measure_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_effectiveness_metric_entries
    ADD CONSTRAINT isms_effectiveness_metric_entries_measure_id_fkey FOREIGN KEY (measure_id) REFERENCES public.isms_effectiveness_measures(id) ON DELETE CASCADE;


--
-- Name: isms_effectiveness_metric_entries isms_effectiveness_metric_entries_source_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_effectiveness_metric_entries
    ADD CONSTRAINT isms_effectiveness_metric_entries_source_event_id_fkey FOREIGN KEY (source_event_id) REFERENCES public.events(id) ON DELETE SET NULL;


--
-- Name: isms_entity_clause_links isms_entity_clause_links_clause_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_entity_clause_links
    ADD CONSTRAINT isms_entity_clause_links_clause_id_fkey FOREIGN KEY (clause_id) REFERENCES public.framework_clauses(id) ON DELETE CASCADE;


--
-- Name: isms_entity_clause_links isms_entity_clause_links_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_entity_clause_links
    ADD CONSTRAINT isms_entity_clause_links_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_entity_control_links isms_entity_control_links_control_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_entity_control_links
    ADD CONSTRAINT isms_entity_control_links_control_item_id_fkey FOREIGN KEY (control_item_id) REFERENCES public.control_items(id) ON DELETE CASCADE;


--
-- Name: isms_entity_control_links isms_entity_control_links_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_entity_control_links
    ADD CONSTRAINT isms_entity_control_links_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_licenses isms_licenses_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_licenses
    ADD CONSTRAINT isms_licenses_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_meeting_attendees isms_meeting_attendees_meeting_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_meeting_attendees
    ADD CONSTRAINT isms_meeting_attendees_meeting_id_fkey FOREIGN KEY (meeting_id) REFERENCES public.isms_meetings(id) ON DELETE CASCADE;


--
-- Name: isms_meeting_attendees isms_meeting_attendees_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_meeting_attendees
    ADD CONSTRAINT isms_meeting_attendees_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: isms_meeting_links isms_meeting_links_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_meeting_links
    ADD CONSTRAINT isms_meeting_links_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.isms_documents(id) ON DELETE CASCADE;


--
-- Name: isms_meeting_links isms_meeting_links_meeting_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_meeting_links
    ADD CONSTRAINT isms_meeting_links_meeting_id_fkey FOREIGN KEY (meeting_id) REFERENCES public.isms_meetings(id) ON DELETE CASCADE;


--
-- Name: isms_meeting_people isms_meeting_people_meeting_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_meeting_people
    ADD CONSTRAINT isms_meeting_people_meeting_id_fkey FOREIGN KEY (meeting_id) REFERENCES public.isms_meetings(id) ON DELETE CASCADE;


--
-- Name: isms_meeting_people isms_meeting_people_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_meeting_people
    ADD CONSTRAINT isms_meeting_people_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.isms_people(id) ON DELETE SET NULL;


--
-- Name: isms_meetings isms_meetings_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_meetings
    ADD CONSTRAINT isms_meetings_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_objective_resource_users isms_objective_resource_users_objective_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_objective_resource_users
    ADD CONSTRAINT isms_objective_resource_users_objective_id_fkey FOREIGN KEY (objective_id) REFERENCES public.isms_objectives(id) ON DELETE CASCADE;


--
-- Name: isms_objective_resource_users isms_objective_resource_users_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_objective_resource_users
    ADD CONSTRAINT isms_objective_resource_users_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: isms_objectives isms_objectives_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_objectives
    ADD CONSTRAINT isms_objectives_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_objectives isms_objectives_owner_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_objectives
    ADD CONSTRAINT isms_objectives_owner_user_id_fkey FOREIGN KEY (owner_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_org_node_users isms_org_node_users_org_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_org_node_users
    ADD CONSTRAINT isms_org_node_users_org_node_id_fkey FOREIGN KEY (org_node_id) REFERENCES public.isms_org_nodes(id) ON DELETE CASCADE;


--
-- Name: isms_org_node_users isms_org_node_users_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_org_node_users
    ADD CONSTRAINT isms_org_node_users_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: isms_org_nodes isms_org_nodes_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_org_nodes
    ADD CONSTRAINT isms_org_nodes_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_org_nodes isms_org_nodes_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_org_nodes
    ADD CONSTRAINT isms_org_nodes_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.isms_org_nodes(id) ON DELETE SET NULL;


--
-- Name: isms_people isms_people_org_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_people
    ADD CONSTRAINT isms_people_org_node_id_fkey FOREIGN KEY (org_node_id) REFERENCES public.isms_org_nodes(id) ON DELETE SET NULL;


--
-- Name: isms_people isms_people_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_people
    ADD CONSTRAINT isms_people_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: isms_person_assets isms_person_assets_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_person_assets
    ADD CONSTRAINT isms_person_assets_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES public.risk_assets(id) ON DELETE CASCADE;


--
-- Name: isms_person_assets isms_person_assets_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_person_assets
    ADD CONSTRAINT isms_person_assets_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.isms_people(id) ON DELETE CASCADE;


--
-- Name: isms_person_assurances isms_person_assurances_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.isms_person_assurances
    ADD CONSTRAINT isms_person_assurances_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.isms_people(id) ON DELETE CASCADE;


--
-- Name: managed_configuration_revisions managed_configuration_revisions_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.managed_configuration_revisions
    ADD CONSTRAINT managed_configuration_revisions_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: managed_configurations managed_configurations_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.managed_configurations
    ADD CONSTRAINT managed_configurations_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: mappings mappings_control_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mappings
    ADD CONSTRAINT mappings_control_item_id_fkey FOREIGN KEY (control_item_id) REFERENCES public.control_items(id);


--
-- Name: mappings mappings_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mappings
    ADD CONSTRAINT mappings_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id);


--
-- Name: mfa_challenges mfa_challenges_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_challenges
    ADD CONSTRAINT mfa_challenges_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: mfa_credentials mfa_credentials_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_credentials
    ADD CONSTRAINT mfa_credentials_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: mfa_recovery_codes mfa_recovery_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_recovery_codes
    ADD CONSTRAINT mfa_recovery_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: osa_control_mappings osa_control_mappings_control_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.osa_control_mappings
    ADD CONSTRAINT osa_control_mappings_control_id_fkey FOREIGN KEY (control_id) REFERENCES public.control_items(id) ON DELETE CASCADE;


--
-- Name: pestle_business_process_relevance pestle_business_process_relevance_business_process_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_business_process_relevance
    ADD CONSTRAINT pestle_business_process_relevance_business_process_id_fkey FOREIGN KEY (business_process_id) REFERENCES public.pestle_business_processes(id) ON DELETE CASCADE;


--
-- Name: pestle_business_process_relevance pestle_business_process_relevance_pestle_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_business_process_relevance
    ADD CONSTRAINT pestle_business_process_relevance_pestle_item_id_fkey FOREIGN KEY (pestle_item_id) REFERENCES public.pestle_items(id) ON DELETE CASCADE;


--
-- Name: pestle_business_process_relevance pestle_business_process_relevance_relevance_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_business_process_relevance
    ADD CONSTRAINT pestle_business_process_relevance_relevance_id_fkey FOREIGN KEY (relevance_id) REFERENCES public.pestle_relevance_levels(id);


--
-- Name: pestle_business_processes pestle_business_processes_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_business_processes
    ADD CONSTRAINT pestle_business_processes_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: pestle_clause_relevance pestle_clause_relevance_clause_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_clause_relevance
    ADD CONSTRAINT pestle_clause_relevance_clause_id_fkey FOREIGN KEY (clause_id) REFERENCES public.framework_clauses(id) ON DELETE CASCADE;


--
-- Name: pestle_clause_relevance pestle_clause_relevance_pestle_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_clause_relevance
    ADD CONSTRAINT pestle_clause_relevance_pestle_item_id_fkey FOREIGN KEY (pestle_item_id) REFERENCES public.pestle_items(id) ON DELETE CASCADE;


--
-- Name: pestle_clause_relevance pestle_clause_relevance_relevance_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_clause_relevance
    ADD CONSTRAINT pestle_clause_relevance_relevance_id_fkey FOREIGN KEY (relevance_id) REFERENCES public.pestle_relevance_levels(id);


--
-- Name: pestle_items pestle_items_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_items
    ADD CONSTRAINT pestle_items_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: pestle_items pestle_items_overall_relevance_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pestle_items
    ADD CONSTRAINT pestle_items_overall_relevance_id_fkey FOREIGN KEY (overall_relevance_id) REFERENCES public.pestle_relevance_levels(id);


--
-- Name: risk_asset_subcategories risk_asset_subcategories_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_asset_subcategories
    ADD CONSTRAINT risk_asset_subcategories_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.risk_categories(id) ON DELETE CASCADE;


--
-- Name: risk_assets risk_assets_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_assets
    ADD CONSTRAINT risk_assets_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.risk_categories(id);


--
-- Name: risk_assets risk_assets_subcategory_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_assets
    ADD CONSTRAINT risk_assets_subcategory_id_fkey FOREIGN KEY (subcategory_id) REFERENCES public.risk_asset_subcategories(id);


--
-- Name: risk_assets risk_assets_vendor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_assets
    ADD CONSTRAINT risk_assets_vendor_id_fkey FOREIGN KEY (vendor_id) REFERENCES public.isms_vendors(id) ON DELETE SET NULL;


--
-- Name: risk_control_links risk_control_links_control_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_control_links
    ADD CONSTRAINT risk_control_links_control_item_id_fkey FOREIGN KEY (control_item_id) REFERENCES public.control_items(id) ON DELETE CASCADE;


--
-- Name: risk_control_links risk_control_links_risk_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_control_links
    ADD CONSTRAINT risk_control_links_risk_id_fkey FOREIGN KEY (risk_id) REFERENCES public.risks(id) ON DELETE CASCADE;


--
-- Name: risks risks_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risks
    ADD CONSTRAINT risks_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: risks risks_risk_owner_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risks
    ADD CONSTRAINT risks_risk_owner_user_id_fkey FOREIGN KEY (risk_owner_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: saved_searches saved_searches_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_searches
    ADD CONSTRAINT saved_searches_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: security_notifications security_notifications_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_notifications
    ADD CONSTRAINT security_notifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: sso_email_challenges sso_email_challenges_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sso_email_challenges
    ADD CONSTRAINT sso_email_challenges_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_groups user_groups_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_groups
    ADD CONSTRAINT user_groups_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.groups(id) ON DELETE CASCADE;


--
-- Name: user_groups user_groups_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_groups
    ADD CONSTRAINT user_groups_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_identities user_identities_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_identities
    ADD CONSTRAINT user_identities_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_login_ips user_login_ips_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_login_ips
    ADD CONSTRAINT user_login_ips_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_permissions user_permissions_permission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_permissions
    ADD CONSTRAINT user_permissions_permission_id_fkey FOREIGN KEY (permission_id) REFERENCES public.permissions(id) ON DELETE CASCADE;


--
-- Name: user_permissions user_permissions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_permissions
    ADD CONSTRAINT user_permissions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_sso_emails user_sso_emails_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sso_emails
    ADD CONSTRAINT user_sso_emails_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--



