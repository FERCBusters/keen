from __future__ import annotations

import uuid
from datetime import date, datetime
from datetime import time as dtime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.datetime_utils import utc_now_naive
from app.db.session import Base


def utcnow() -> datetime:
    return utc_now_naive()


SCHEDULE_RECURRENCE_VALUES = "'once','weekly','monthly','quarterly','yearly'"
SCHEDULE_DATE_RULE_VALUES = (
    "'exact','first_weekday_of_month','first_monday_of_month',"
    "'first_tuesday_of_month','first_wednesday_of_month',"
    "'first_thursday_of_month','first_friday_of_month'"
)


class ScheduleColumns:
    """The recurring schedule a record keeps; app.services.schedules reads it.

    The record's ``start_date`` is the first run; exact monthly, quarterly and
    yearly runs keep its day of the month.
    """

    schedule_recurrence: Mapped[str] = mapped_column(
        String(32), nullable=False, default="once"
    )
    schedule_interval: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    schedule_date_rule: Mapped[str] = mapped_column(
        String(32), nullable=False, default="exact"
    )
    schedule_anchor_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schedule_next_run_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, index=True
    )
    schedule_until_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    schedule_last_run_date: Mapped[date | None] = mapped_column(Date, nullable=True)


def schedule_constraints(table: str) -> tuple[CheckConstraint, ...]:
    """Check constraints for a table with the ScheduleColumns."""
    return (
        CheckConstraint(
            f"schedule_recurrence in ({SCHEDULE_RECURRENCE_VALUES})",
            name=f"ck_{table}_schedule_recurrence",
        ),
        CheckConstraint(
            f"schedule_date_rule in ({SCHEDULE_DATE_RULE_VALUES})",
            name=f"ck_{table}_schedule_date_rule",
        ),
        CheckConstraint(
            "schedule_anchor_month is null or (schedule_anchor_month >= 1 and schedule_anchor_month <= 12)",
            name=f"ck_{table}_schedule_anchor_month",
        ),
        CheckConstraint(
            "schedule_interval >= 1",
            name=f"ck_{table}_schedule_interval",
        ),
    )


class Framework(Base):
    __tablename__ = "frameworks"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )  # e.g. iso_27001_2022
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    upstream_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )


class ManagedConfiguration(Base):
    """Versioned administrator override of a shipped YAML configuration."""

    __tablename__ = "managed_configurations"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class ManagedConfigurationRevision(Base):
    __tablename__ = "managed_configuration_revisions"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )


class RuleBackfillJob(Base):
    __tablename__ = "rule_backfill_jobs"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rule_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    rule_document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rules_version: Mapped[int] = mapped_column(Integer, nullable=False)
    total_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    cutoff: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    cursor_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cursor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    examined: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_mappings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class ControlItem(Base):
    __tablename__ = "control_items"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    framework_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # clause | annex_control | custom
    ref: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )  # org-provided (SoA import)
    in_scope: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tags: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    clause_links = relationship(
        "ControlClauseLink", back_populates="control", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("framework_slug", "type", "ref", name="uq_controlitem_ref"),
    )


class CrossFrameworkControlLink(Base):
    """One-way, one-hop evidence inheritance between framework controls."""

    __tablename__ = "cross_framework_control_links"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_control_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("control_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_control_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("control_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    source = relationship("ControlItem", foreign_keys=[source_control_id])
    target = relationship("ControlItem", foreign_keys=[target_control_id])
    __table_args__ = (
        UniqueConstraint(
            "source_control_id", "target_control_id", name="uq_cross_framework_link"
        ),
        CheckConstraint(
            "source_control_id <> target_control_id", name="ck_cross_framework_distinct"
        ),
    )


class OsaControlMapping(Base):
    """Frozen OSA clause-to-NIST memberships; administrator nodes remain ordinary controls."""

    __tablename__ = "osa_control_mappings"
    control_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("control_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    nist_ref: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)


# Keep the original table writable. All evidence readers use this compact,
# one-hop relation, so an inherited event never propagates transitively.
from sqlalchemy import cast, exists, literal, select
from sqlalchemy.orm import foreign

_osa_source = OsaControlMapping.__table__.alias("osa_source")
_osa_target = OsaControlMapping.__table__.alias("osa_target")
_source_control = ControlItem.__table__.alias("osa_source_control")
_target_control = ControlItem.__table__.alias("osa_target_control")
_explicit = CrossFrameworkControlLink.__table__


def effective_cross_framework_links(
    *, source_ids=None, target_framework=None, enabled=None
):
    """Resolve indexed NIST memberships, never compare every pair of controls.

    Optional restrictions belong INSIDE both UNION arms, before deduplication.
    Event readers pass the distinct source controls from their bounded page.
    The unrestricted form remains available for catalogue readers.
    """
    explicit = select(
        cast(_explicit.c.id, String).label("id"),
        _explicit.c.source_control_id,
        _explicit.c.target_control_id,
        _explicit.c.rationale,
        _explicit.c.created_at,
        literal(False).label("derived"),
    )
    pairs = (
        select(
            _osa_source.c.control_id.label("source_control_id"),
            _osa_target.c.control_id.label("target_control_id"),
        )
        .select_from(
            _osa_source.join(
                _osa_target, _osa_source.c.nist_ref == _osa_target.c.nist_ref
            )
            .join(_source_control, _source_control.c.id == _osa_source.c.control_id)
            .join(_target_control, _target_control.c.id == _osa_target.c.control_id)
        )
        .where(_source_control.c.framework_slug != _target_control.c.framework_slug)
    )
    if source_ids is not None:
        explicit = explicit.where(_explicit.c.source_control_id.in_(source_ids))
        pairs = pairs.where(_osa_source.c.control_id.in_(source_ids))
    if target_framework is not None:
        targets = select(ControlItem.id).where(
            ControlItem.framework_slug == target_framework
        )
        explicit = explicit.where(_explicit.c.target_control_id.in_(targets))
        pairs = pairs.where(_target_control.c.framework_slug == target_framework)
    if enabled is not None:
        allowed = select(ControlItem.id).where(ControlItem.framework_slug.in_(enabled))
        explicit = explicit.where(
            _explicit.c.source_control_id.in_(allowed),
            _explicit.c.target_control_id.in_(allowed),
        )
        pairs = pairs.where(
            _source_control.c.framework_slug.in_(enabled),
            _target_control.c.framework_slug.in_(enabled),
        )
    pairs = pairs.distinct().subquery()
    derived = select(
        (
            cast(pairs.c.source_control_id, String)
            + literal(":")
            + cast(pairs.c.target_control_id, String)
        ).label("id"),
        pairs.c.source_control_id,
        pairs.c.target_control_id,
        literal(
            "Related evidence via shared NIST controls in Open Security Architecture (CC BY-SA 4.0). "
            "Keen-derived crosswalk; review relevance and coverage gaps, as related controls can differ in scope."
        ),
        literal(None, type_=DateTime),
        literal(True),
    ).where(
        ~exists(
            select(_explicit.c.id).where(
                _explicit.c.source_control_id == pairs.c.source_control_id,
                _explicit.c.target_control_id == pairs.c.target_control_id,
            )
        )
    )
    return explicit.union_all(derived).subquery("effective_cross_framework_links")


_effective_links = effective_cross_framework_links()


class EffectiveCrossFrameworkControlLink(Base):
    __table__ = _effective_links
    __mapper_args__ = {"primary_key": [_effective_links.c.id]}
    source = relationship(
        ControlItem,
        primaryjoin=foreign(_effective_links.c.source_control_id) == ControlItem.id,
        viewonly=True,
    )
    target = relationship(
        ControlItem,
        primaryjoin=foreign(_effective_links.c.target_control_id) == ControlItem.id,
        viewonly=True,
    )


class FrameworkClause(Base):
    __tablename__ = "framework_clauses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    framework_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ref: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    parent_clause_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("framework_clauses.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    parent = relationship(
        "FrameworkClause", remote_side=[id], back_populates="children"
    )
    children = relationship(
        "FrameworkClause",
        back_populates="parent",
        cascade="all, delete-orphan",
        order_by="FrameworkClause.sort_order, FrameworkClause.ref",
    )
    control_links = relationship(
        "ControlClauseLink", back_populates="clause", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("framework_slug", "ref", name="uq_framework_clause_ref"),
    )


class ControlClauseLink(Base):
    __tablename__ = "control_clause_links"

    control_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("control_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    clause_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("framework_clauses.id", ondelete="CASCADE"),
        primary_key=True,
    )
    applicability: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    control = relationship("ControlItem", back_populates="clause_links")
    clause = relationship("FrameworkClause", back_populates="control_links")

    __table_args__ = (
        CheckConstraint(
            "applicability in ('applicable','partially_applicable')",
            name="ck_control_clause_links_applicability",
        ),
    )


class Event(Base):
    __tablename__ = "events"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    # Snapshots survive connection deletion and identify the origin of historical evidence.
    connection_id: Mapped[str | None] = mapped_column(
        String(96), nullable=True, index=True
    )
    connection_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False
    )  # loki, webhook:something, etc.
    system: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    action: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    severity: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    raw_pointer: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    normalized_payload: Mapped[dict] = mapped_column(
        JSONB, default=dict, nullable=False
    )

    external_id: Mapped[str] = mapped_column(
        String(128), nullable=False
    )  # stable dedupe key per source
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )

    artifacts = relationship(
        "Artifact", back_populates="event", cascade="all, delete-orphan"
    )
    mappings = relationship(
        "Mapping", back_populates="event", cascade="all, delete-orphan"
    )
    incidents = relationship(
        "EventIncident", back_populates="event", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_event_source_external"),
        # Source pages, source-filtered event lists and per-source insights all
        # read one source over a time range.
        Index("ix_events_source_timestamp", "source", "timestamp"),
    )


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, index=True
    )

    # Optional lineage (see migration 0002_artifact_lineage)
    parent_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=True, index=True
    )

    kind: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # log_line, webhook_payload, json_response, screenshot, ...
    storage_uri: Mapped[str] = mapped_column(
        String(512), nullable=False
    )  # s3://bucket/key
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(128), nullable=False, default="application/octet-stream"
    )
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    captured_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    captured_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    redaction_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown"
    )
    pii_flags: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    retention_class: Mapped[str] = mapped_column(
        String(32), nullable=False, default="default"
    )

    # Free-form metadata (e.g. lineage notes, tool versions)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)

    event = relationship("Event", back_populates="artifacts")
    parent = relationship("Artifact", remote_side=[id], backref="children")


class ControlEvidenceStats(Base):
    __tablename__ = "control_evidence_stats"

    control_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("control_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_evidence: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    control = relationship("ControlItem")

    __table_args__ = (
        CheckConstraint(
            "evidence_count >= 0",
            name="ck_control_evidence_stats_count_nonnegative",
        ),
    )


class Mapping(Base):
    __tablename__ = "mappings"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, index=True
    )
    control_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("control_items.id"), nullable=False, index=True
    )

    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    method: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # rule | manual | import
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    mapped_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mapped_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    event = relationship("Event", back_populates="mappings")

    __table_args__ = (
        UniqueConstraint("event_id", "control_item_id", name="uq_mapping_unique"),
    )


class EventIncident(Base):
    """Local marker that an external incident was created from a Keen event."""

    __tablename__ = "event_incidents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    event_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    webhook_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)

    event = relationship("Event", back_populates="incidents")
    created_by = relationship("User")


class IngestionCursor(Base):
    __tablename__ = "ingestion_cursors"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False
    )  # e.g. loki:ubuntu_auth_failed_ssh
    last_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )


class AuditLog(Base):
    """Record UI -> backend requests as an internal audit trail.

    This is intended to support iso_27001_2022 A.8.34 by providing evidence of
    access/actions taken during audit testing.
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )

    username: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    method: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    path: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    query_string: Mapped[str | None] = mapped_column(Text, nullable=True)

    status_code: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    referer: Mapped[str | None] = mapped_column(String(512), nullable=True)


class EntityChangelog(Base):
    """Immutable semantic changelog for auditable Control, Clause and Risk changes."""

    __tablename__ = "entity_changelogs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    entity_ref: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    entity_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    before_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    changes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    changed_by_username: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    request_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    request_path: Mapped[str | None] = mapped_column(
        String(256), nullable=True, index=True
    )

    changed_by = relationship("User", foreign_keys=[changed_by_user_id])


# -----------------------------------------------------------------------------
# RBAC: Groups + Permissions
# -----------------------------------------------------------------------------

# Many-to-many user <-> group
user_groups = Table(
    "user_groups",
    Base.metadata,
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "group_id",
        UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

# Many-to-many user <-> permission (explicit grants)
user_permissions = Table(
    "user_permissions",
    Base.metadata,
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

# Many-to-many group <-> permission
group_permissions = Table(
    "group_permissions",
    Base.metadata,
    Column(
        "group_id",
        UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional role assigned to this group. When a user has role='inherit',
    # their effective role is resolved from group roles.
    # One of: admin | normal | NULL
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    users = relationship("User", secondary=user_groups, back_populates="groups")
    permissions = relationship(
        "Permission", secondary=group_permissions, back_populates="groups"
    )


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    users = relationship(
        "User", secondary=user_permissions, back_populates="permissions"
    )
    groups = relationship(
        "Group", secondary=group_permissions, back_populates="permissions"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_backend: Mapped[str] = mapped_column(
        String(16), nullable=False, default="local", server_default="local"
    )
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    session_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    mfa_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    mfa_totp_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_totp_last_step: Mapped[int] = mapped_column(
        Integer, nullable=False, default=-1, server_default="-1"
    )
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # One of: admin | normal
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    # Bumped whenever RBAC/role-related state changes for this user.
    # The value is loaded with the user row and compared to the session snapshot,
    # so permission caching remains invalidatable without querying permission joins
    # on every request.
    authz_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # -------------------------------------------------------------------------
    # User preferences (UI display)
    # -------------------------------------------------------------------------
    # If true, the UI will display timestamps in the user's preferred timezone
    # rather than the server/default timezone.
    pref_use_local_timezone: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # IANA timezone name (e.g. "Australia/Melbourne"). Optional.
    pref_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # UI theme id (e.g. "sand", "ink", ...)
    pref_theme: Mapped[str] = mapped_column(String(32), default="sand", nullable=False)

    # Visible date input format preference. "default" means use KEEN_UI_DATE_FORMAT.
    pref_date_format: Mapped[str] = mapped_column(
        String(16), default="default", nullable=False
    )

    # If true, filter UIs auto-apply as you change fields (skip Apply buttons).
    # Defaults to False to preserve the existing explicit-Apply workflow.
    pref_auto_apply_filters: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # Per-user overrides for source badge colors.
    # Keys are event sources (e.g. 'loki', 'webhook:github'), values are
    # normalized hex strings ('#RRGGBB').
    pref_source_colors: Mapped[dict] = mapped_column(
        JSONB, default=dict, nullable=False
    )

    # Default Visualisation page state (mode/date range/sunburst focus).
    # Optional; when unset the UI falls back to Graph + last 7 days.
    pref_default_visualisation: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )

    # Default landing page when opening the app authenticated (e.g. '/', '/events.html')
    # Can include query params (e.g. saved searches), so allow more room.
    pref_landing_page: Mapped[str] = mapped_column(
        String(2048), default="/", nullable=False
    )

    # Preferred default framework slug for this user (e.g. "iso_27001_2022").
    # When unset, the application-level default framework is used.
    pref_default_framework: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    # -------------------------------------------------------------------------
    # RBAC: groups + permissions
    # -------------------------------------------------------------------------
    groups = relationship("Group", secondary=user_groups, back_populates="users")
    permissions = relationship(
        "Permission", secondary=user_permissions, back_populates="users"
    )
    identities = relationship(
        "UserIdentity", back_populates="user", cascade="all, delete-orphan"
    )
    # Saved searches (user shortcuts)
    saved_searches = relationship(
        "SavedSearch", back_populates="user", cascade="all, delete-orphan"
    )


class UserIdentity(Base):
    __tablename__ = "user_identities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="oidc")
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    preferred_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claims: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    user = relationship("User", back_populates="identities")

    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_user_identities_issuer_subject"),
    )


class OidcLoginState(Base):
    __tablename__ = "oidc_login_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    nonce: Mapped[str] = mapped_column(String(255), nullable=False)
    code_verifier: Mapped[str] = mapped_column(Text, nullable=False)
    next_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )


class SavedSearch(Base):
    __tablename__ = "saved_searches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    user = relationship("User", back_populates="saved_searches")

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_saved_search_user_name"),
    )


# -----------------------------------------------------------------------------
# Risks (asset/threat register with framework-specific control mappings)
# -----------------------------------------------------------------------------


class RiskCategory(Base):
    __tablename__ = "risk_categories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # This is now the asset category used by the risk register, e.g. I.T or Data.
    name: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    asset_subcategories = relationship(
        "RiskAssetSubcategory",
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="RiskAssetSubcategory.name",
    )
    assets = relationship("RiskAsset", back_populates="category")


class RiskAssetSubcategory(Base):
    __tablename__ = "risk_asset_subcategories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("risk_categories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    category = relationship("RiskCategory", back_populates="asset_subcategories")
    assets = relationship("RiskAsset", back_populates="subcategory")

    __table_args__ = (
        UniqueConstraint(
            "category_id",
            "name",
            name="uq_risk_asset_subcategories_category_name",
        ),
    )


class RiskAsset(Base):
    __tablename__ = "risk_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("risk_categories.id"), nullable=False, index=True
    )
    subcategory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("risk_asset_subcategories.id"),
        nullable=False,
        index=True,
    )
    license: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    license_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_licenses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_vendors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    owner_org_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_org_nodes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    register_held_by_org_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_org_nodes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    category = relationship("RiskCategory", back_populates="assets")
    subcategory = relationship("RiskAssetSubcategory", back_populates="assets")
    license_entity = relationship("IsmsLicense", back_populates="assets")
    vendor = relationship("IsmsVendor", back_populates="assets")
    owner_org_node = relationship("IsmsOrgNode", foreign_keys=[owner_org_node_id])
    register_held_by_org_node = relationship(
        "IsmsOrgNode", foreign_keys=[register_held_by_org_node_id]
    )
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    risks = relationship("Risk", back_populates="asset")

    __table_args__ = (
        UniqueConstraint(
            "name",
            "category_id",
            "subcategory_id",
            name="uq_risk_assets_name_category_subcategory",
        ),
    )


class Risk(Base):
    __tablename__ = "risks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("risk_assets.id"), nullable=False, index=True
    )
    # A short name for the register; the threat summary carries the detail.
    title: Mapped[str] = mapped_column(
        String(256), nullable=False, default="", server_default=""
    )
    # CIA associations; a general register entry has none.
    risk_types: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    risk_owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    threat_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    threat_score: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    vulnerability_score: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    impact_score: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    residual_vulnerability_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    residual_impact_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    residual_risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # The conventional 5x5 register ratings, from which the risk scores and
    # the three-factor score columns follow.
    register_likelihood: Mapped[int | None] = mapped_column(Integer, nullable=True)
    register_impact: Mapped[int | None] = mapped_column(Integer, nullable=True)
    register_residual_likelihood: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    register_residual_impact: Mapped[int | None] = mapped_column(Integer, nullable=True)
    treatment_strategy: Mapped[str] = mapped_column(
        String(32), nullable=False, default=""
    )
    treatment_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="open"
    )
    treatment_plan: Mapped[str] = mapped_column(Text, nullable=False, default="")
    treatment_due_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    mitigator_context: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    asset = relationship("RiskAsset", back_populates="risks")
    risk_owner_role_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_org_nodes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    owner_role = relationship("IsmsOrgNode", foreign_keys=[risk_owner_role_id])
    owner = relationship("User", foreign_keys=[risk_owner_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    control_links = relationship(
        "RiskControlLink", back_populates="risk", cascade="all, delete-orphan"
    )
    party_links = relationship(
        "RiskInterestedParty",
        back_populates="risk",
        cascade="all, delete-orphan",
        order_by="RiskInterestedParty.created_at",
    )

    __table_args__ = (
        CheckConstraint(
            "risk_owner_user_id IS NULL OR risk_owner_role_id IS NULL",
            name="ck_risks_one_owner",
        ),
        CheckConstraint("threat_score between 0 and 5", name="ck_risks_threat_score"),
        CheckConstraint(
            "vulnerability_score between 0 and 5",
            name="ck_risks_vulnerability_score",
        ),
        CheckConstraint("impact_score between 0 and 5", name="ck_risks_impact_score"),
        CheckConstraint(
            "residual_vulnerability_score between 0 and 5",
            name="ck_risks_residual_vulnerability_score",
        ),
        CheckConstraint(
            "residual_impact_score between 0 and 5",
            name="ck_risks_residual_impact_score",
        ),
        CheckConstraint(
            "register_likelihood between 1 and 5", name="ck_risks_register_likelihood"
        ),
        CheckConstraint(
            "register_impact between 1 and 5", name="ck_risks_register_impact"
        ),
        CheckConstraint(
            "register_residual_likelihood between 1 and 5",
            name="ck_risks_register_residual_likelihood",
        ),
        CheckConstraint(
            "register_residual_impact between 1 and 5",
            name="ck_risks_register_residual_impact",
        ),
    )


class RiskRegisterSettings(Base):
    __tablename__ = "risk_register_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    low_max: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    moderate_max: Mapped[int] = mapped_column(Integer, nullable=False, default=9)
    high_max: Mapped[int] = mapped_column(Integer, nullable=False, default=16)


class RiskInterestedParty(Base):
    """Who a risk affects: an interested party, a person or a vendor.

    Exactly one of the three references is set; the row disappears with the
    risk or with the party it names.
    """

    __tablename__ = "risk_interested_parties"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    risk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("risks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    interested_party_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("interested_parties.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_people.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_vendors.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    risk = relationship("Risk", back_populates="party_links")
    interested_party = relationship("InterestedParty")
    person = relationship("IsmsPerson")
    vendor = relationship("IsmsVendor")

    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN interested_party_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN person_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN vendor_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_risk_interested_parties_one_party",
        ),
    )


class RiskLibraryEntry(Base):
    __tablename__ = "risk_library_entries"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    threat_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # "risk" templates seed register entries (with or without CIA types);
    # "pestle" templates seed PESTLE(E) impact-assessment items.
    method: Mapped[str] = mapped_column(
        String(16), nullable=False, default="risk", server_default="risk"
    )
    pestle_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", server_default=""
    )
    lens: Mapped[str] = mapped_column(
        String(16), nullable=False, default="", server_default=""
    )
    risk_types: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    treatment_guidance: Mapped[str] = mapped_column(Text, nullable=False, default="")
    suggested_assessment: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )


class RiskControlLink(Base):
    __tablename__ = "risk_control_links"

    risk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("risks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    framework_slug: Mapped[str] = mapped_column(
        String(64), primary_key=True, nullable=False, index=True
    )
    control_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("control_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    risk = relationship("Risk", back_populates="control_links")
    control = relationship("ControlItem")


# -----------------------------------------------------------------------------
# PESTLE(E) impact assessment
# -----------------------------------------------------------------------------


class PestleRelevanceLevel(Base):
    __tablename__ = "pestle_relevance_levels"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(
        String(16), unique=True, nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )


class PestleItem(Base):
    __tablename__ = "pestle_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    framework_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    lens: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    item: Mapped[str] = mapped_column(Text, nullable=False, default="")
    overall_relevance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pestle_relevance_levels.id"),
        nullable=False,
        index=True,
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    overall_relevance = relationship("PestleRelevanceLevel")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    business_process_links = relationship(
        "PestleBusinessProcessRelevance",
        back_populates="pestle_item",
        cascade="all, delete-orphan",
    )
    clause_links = relationship(
        "PestleClauseRelevance",
        back_populates="pestle_item",
        cascade="all, delete-orphan",
    )


class PestleBusinessProcess(Base):
    __tablename__ = "pestle_business_processes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(
        String(256), unique=True, nullable=False, index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    created_by = relationship("User", foreign_keys=[created_by_user_id])
    pestle_links = relationship(
        "PestleBusinessProcessRelevance",
        back_populates="business_process",
        cascade="all, delete-orphan",
    )


class PestleBusinessProcessRelevance(Base):
    __tablename__ = "pestle_business_process_relevance"

    pestle_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pestle_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    business_process_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pestle_business_processes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    relevance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pestle_relevance_levels.id"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    pestle_item = relationship("PestleItem", back_populates="business_process_links")
    business_process = relationship(
        "PestleBusinessProcess", back_populates="pestle_links"
    )
    relevance = relationship("PestleRelevanceLevel")


class PestleClauseRelevance(Base):
    __tablename__ = "pestle_clause_relevance"

    pestle_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pestle_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    clause_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("framework_clauses.id", ondelete="CASCADE"),
        primary_key=True,
    )
    relevance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pestle_relevance_levels.id"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    pestle_item = relationship("PestleItem", back_populates="clause_links")
    clause = relationship("FrameworkClause")
    relevance = relationship("PestleRelevanceLevel")


# -----------------------------------------------------------------------------
# Interested parties risk/context assessment
# -----------------------------------------------------------------------------


class InterestedPartyName(Base):
    __tablename__ = "interested_party_names"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(
        String(256), unique=True, nullable=False, index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    created_by = relationship("User", foreign_keys=[created_by_user_id])
    parties = relationship("InterestedParty", back_populates="name")


class InterestedPartyNature(Base):
    __tablename__ = "interested_party_natures"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(
        String(256), unique=True, nullable=False, index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    created_by = relationship("User", foreign_keys=[created_by_user_id])
    parties = relationship("InterestedParty", back_populates="nature")


class InterestedParty(Base):
    """A party with a stake in the organisation, such as customers or a
    regulator. The record is organisation-wide; only its control links belong
    to a framework."""

    __tablename__ = "interested_parties"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("interested_party_names.id"),
        nullable=False,
        index=True,
    )
    nature_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("interested_party_natures.id"),
        nullable=False,
        index=True,
    )
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    name = relationship("InterestedPartyName", back_populates="parties")
    nature = relationship("InterestedPartyNature", back_populates="parties")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    control_links = relationship(
        "InterestedPartyControlLink",
        back_populates="interested_party",
        cascade="all, delete-orphan",
    )
    communications = relationship(
        "InterestedPartyCommunication",
        back_populates="interested_party",
        cascade="all, delete-orphan",
        order_by="InterestedPartyCommunication.created_at, InterestedPartyCommunication.id",
    )

    __table_args__ = (
        UniqueConstraint(
            "name_id", "nature_id", name="uq_interested_parties_name_nature"
        ),
    )


class InterestedPartyControlLink(Base):
    __tablename__ = "interested_party_control_links"

    interested_party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("interested_parties.id", ondelete="CASCADE"),
        primary_key=True,
    )
    framework_slug: Mapped[str] = mapped_column(
        String(64), primary_key=True, nullable=False, index=True
    )
    control_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("control_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    interested_party = relationship("InterestedParty", back_populates="control_links")
    control = relationship("ControlItem")


class InterestedPartyCommunication(Base):
    __tablename__ = "interested_party_communications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    interested_party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("interested_parties.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    when: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    with_whom: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    methods: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    interested_party = relationship("InterestedParty", back_populates="communications")

    __table_args__ = (
        CheckConstraint(
            "event in ('Business as Usual','Incident','Alert','Notifiable Event')",
            name="ck_interested_party_communications_event",
        ),
        CheckConstraint(
            "\"when\" in ('As Required','Upon Identification','Routine','Within 24 hours','Within 48 hours','Within 72 hours','Within a week','Within a month')",
            name="ck_interested_party_communications_when",
        ),
        CheckConstraint(
            "with_whom in ('Individual member','Individual staff member','Senior Supplier Point of Contact','Supplier Point of Contact','NCSC','ICO','Sender')",
            name="ck_interested_party_communications_with_whom",
        ),
    )


# -----------------------------------------------------------------------------
# ISMS module (objectives, documents, org chart, assets, app configuration,
# meetings and shared control/clause relationships)
# -----------------------------------------------------------------------------


class IsmsLicense(Base):
    __tablename__ = "isms_licenses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(
        String(256), nullable=False, unique=True, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    created_by = relationship("User", foreign_keys=[created_by_user_id])
    assets = relationship("RiskAsset", back_populates="license_entity")


class IsmsSwot(Base):
    __tablename__ = "isms_swot"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    response: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    significance: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="defined", index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("isms_swot.id", ondelete="RESTRICT"), index=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    start_date: Mapped[date | None] = mapped_column(Date)
    target_date: Mapped[date | None] = mapped_column(Date, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    __table_args__ = (
        CheckConstraint(
            "kind in ('strength','weakness','opportunity','threat')",
            name="ck_swot_kind",
        ),
        CheckConstraint("significance between 1 and 5", name="ck_swot_significance"),
        CheckConstraint(
            "status in ('defined','planned','in_progress','under_review','completed','deferred','superseded')",
            name="ck_swot_status",
        ),
        CheckConstraint("parent_id <> id", name="ck_swot_parent"),
        CheckConstraint("target_date >= start_date", name="ck_swot_dates"),
    )


class IsmsSwotLink(Base):
    __tablename__ = "isms_swot_links"
    swot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_swot.id", ondelete="CASCADE"),
        primary_key=True,
    )
    target_type: Mapped[str] = mapped_column(String(24), primary_key=True)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    __table_args__ = (
        Index("ix_swot_link_target", "target_type", "target_id"),
        CheckConstraint(
            "target_type in ('objective','document','asset','person','vendor','risk')",
            name="ck_swot_link_type",
        ),
    )


class AuditSwotScope(Base):
    __tablename__ = "audit_swot_scope"
    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE"),
        primary_key=True,
    )
    swot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_swot.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )


class IsmsObjective(Base):
    __tablename__ = "isms_objectives"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    requirement: Mapped[str] = mapped_column(Text, nullable=False, default="")
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metric: Mapped[str] = mapped_column(Text, nullable=False, default="")
    completion_method: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resource_requirements_text: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    start_date: Mapped[date | None] = mapped_column(Date)
    completion_target_date: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    evaluation_method: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_started", index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    owner = relationship("User", foreign_keys=[owner_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    resource_users = relationship(
        "IsmsObjectiveResourceUser",
        back_populates="objective",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "status in ('not_started','defined','planned','in_progress','under_review','completed','deferred','superseded')",
            name="ck_isms_objectives_status",
        ),
    )


class IsmsObjectiveResourceUser(Base):
    __tablename__ = "isms_objective_resource_users"

    objective_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_objectives.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    objective = relationship("IsmsObjective", back_populates="resource_users")
    user = relationship("User")


class IsmsDocument(Base):
    __tablename__ = "isms_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    document_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="policy", index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    external_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_document_folders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    content_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    uploaded_by = relationship("User", foreign_keys=[uploaded_by_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    folder = relationship("IsmsDocumentFolder")
    content_revisions = relationship(
        "IsmsDocumentRevision", cascade="all, delete-orphan"
    )
    comments = relationship("IsmsDocumentComment", cascade="all, delete-orphan")


class IsmsDocumentFolder(Base):
    __tablename__ = "isms_document_folders"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_document_folders.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent = relationship("IsmsDocumentFolder", remote_side=[id])


class IsmsDocumentRevision(Base):
    __tablename__ = "isms_document_revisions"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_html: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_isms_document_revision"),
    )


class IsmsDocumentComment(Base):
    __tablename__ = "isms_document_comments"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    author = relationship("User")


class BookStackSectionEvidence(Base):
    """Immutable snapshot of a page version linked to a policy requirement."""

    __tablename__ = "bookstack_section_evidence"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_documents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    target_control_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("control_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_clause_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("framework_clauses.id", ondelete="SET NULL"),
        nullable=True,
    )
    page_id: Mapped[int] = mapped_column(Integer, nullable=False)
    anchor: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    permalink: Mapped[str] = mapped_column(String(2048), nullable=False)
    page_title: Mapped[str] = mapped_column(String(256), nullable=False)
    revision_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_updated_at: Mapped[str] = mapped_column(
        String(128), nullable=False, default=""
    )
    storage_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    captured_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    document = relationship("IsmsDocument")
    target_control = relationship("ControlItem")
    target_clause = relationship("FrameworkClause")

    __table_args__ = (
        CheckConstraint(
            "target_control_id IS NOT NULL OR target_clause_id IS NOT NULL",
            name="ck_bookstack_section_target",
        ),
    )


class IsmsOrgNode(Base):
    __tablename__ = "isms_org_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_org_nodes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    node_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="role", index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    parent = relationship("IsmsOrgNode", remote_side=[id], back_populates="children")
    children = relationship(
        "IsmsOrgNode",
        back_populates="parent",
        order_by="IsmsOrgNode.sort_order, IsmsOrgNode.name",
    )
    users = relationship(
        "IsmsOrgNodeUser", back_populates="org_node", cascade="all, delete-orphan"
    )
    created_by = relationship("User", foreign_keys=[created_by_user_id])


class IsmsOrgNodeUser(Base):
    __tablename__ = "isms_org_node_users"

    org_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_org_nodes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    relationship_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="member", primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    org_node = relationship("IsmsOrgNode", back_populates="users")
    user = relationship("User")


class IsmsPerson(Base):
    """A person in the assurance register, regardless of Keen login access."""

    __tablename__ = "isms_people"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    position: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    org_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_org_nodes.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    user = relationship("User")
    org_node = relationship("IsmsOrgNode")
    assurances = relationship(
        "IsmsPersonAssurance", back_populates="person", cascade="all, delete-orphan"
    )
    assets = relationship(
        "IsmsPersonAsset", back_populates="person", cascade="all, delete-orphan"
    )


class IsmsVendor(Base):
    __tablename__ = "isms_vendors"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    website: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    contact: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    assets = relationship("RiskAsset", back_populates="vendor")


class IsmsPersonAsset(Base):
    __tablename__ = "isms_person_assets"
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_people.id", ondelete="CASCADE"),
        primary_key=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("risk_assets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    relationship_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="uses"
    )
    person = relationship("IsmsPerson", back_populates="assets")
    asset = relationship("RiskAsset")


class IsmsPersonAssurance(Base):
    __tablename__ = "isms_person_assurances"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_people.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    source_system: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    evidence_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    completed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    person = relationship("IsmsPerson", back_populates="assurances")


class IsmsBusinessProcess(Base):
    __tablename__ = "isms_business_processes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(
        String(256), nullable=False, unique=True, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class IsmsApplicationConfigurationEntry(Base):
    __tablename__ = "isms_application_configuration_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_documents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("risk_assets.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    org_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_org_nodes.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    business_process_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_business_processes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    value: Mapped[str] = mapped_column(
        String(16), nullable=False, default="Low", index=True
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    document = relationship("IsmsDocument")
    user = relationship("User", foreign_keys=[user_id])
    asset = relationship("RiskAsset")
    org_node = relationship("IsmsOrgNode")
    business_process = relationship("IsmsBusinessProcess")
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        CheckConstraint(
            "source_type in ('document','person','asset','org_node')",
            name="ck_isms_app_config_source_type",
        ),
        CheckConstraint(
            "value in ('Low','Medium','High')", name="ck_isms_app_config_value"
        ),
    )


class IsmsAwsAccount(Base):
    __tablename__ = "isms_aws_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    account_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, unique=True
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    created_by = relationship("User", foreign_keys=[created_by_user_id])
    access_matrix_links = relationship(
        "IsmsAccessControlMatrixAwsAccount",
        back_populates="aws_account",
        cascade="all, delete-orphan",
    )


class IsmsAccessControlMatrixEntry(Base):
    __tablename__ = "isms_access_control_matrix_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    service_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("risk_assets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="Pending Approval", index=True
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    service_asset = relationship("RiskAsset", foreign_keys=[service_asset_id])
    approved_by = relationship("User", foreign_keys=[approved_by_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    role_links = relationship(
        "IsmsAccessControlMatrixRole",
        back_populates="entry",
        cascade="all, delete-orphan",
    )
    aws_account_links = relationship(
        "IsmsAccessControlMatrixAwsAccount",
        back_populates="entry",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "status in ('Pending Approval','Approved')",
            name="ck_isms_access_control_matrix_status",
        ),
    )


class IsmsAccessControlMatrixAwsAccount(Base):
    __tablename__ = "isms_access_control_matrix_aws_accounts"

    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_access_control_matrix_entries.id", ondelete="CASCADE"),
        primary_key=True,
    )
    aws_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_aws_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    entry = relationship(
        "IsmsAccessControlMatrixEntry", back_populates="aws_account_links"
    )
    aws_account = relationship("IsmsAwsAccount", back_populates="access_matrix_links")


class IsmsAccessControlMatrixRole(Base):
    __tablename__ = "isms_access_control_matrix_roles"

    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_access_control_matrix_entries.id", ondelete="CASCADE"),
        primary_key=True,
    )
    org_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_org_nodes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    entry = relationship("IsmsAccessControlMatrixEntry", back_populates="role_links")
    org_node = relationship("IsmsOrgNode", foreign_keys=[org_node_id])


class IsmsEffectivenessMeasure(Base):
    __tablename__ = "isms_effectiveness_measures"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    framework_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metric_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, index=True
    )
    # The Keen evidence source expected to supply this measure's values, or
    # "other"; new metric entries default to it.
    source_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="other", server_default="other"
    )
    target_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_unit: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    threshold_operator: Mapped[str] = mapped_column(
        String(16), nullable=False, default=""
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    frequency: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False, index=True
    )

    owner = relationship("User", foreign_keys=[owner_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    metric_entries = relationship(
        "IsmsEffectivenessMetricEntry",
        back_populates="measure",
        cascade="all, delete-orphan",
        order_by="IsmsEffectivenessMetricEntry.recorded_at",
    )

    __table_args__ = (
        CheckConstraint(
            "threshold_operator in ('','lt','lte','eq','gte','gt')",
            name="ck_isms_effectiveness_threshold_operator",
        ),
    )


class IsmsEffectivenessMetricEntry(Base):
    __tablename__ = "isms_effectiveness_metric_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    measure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_effectiveness_measures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    metric_unit: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    qualitative_value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="other", index=True
    )
    source_title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_reference: Mapped[str] = mapped_column(
        String(256), nullable=False, default=""
    )
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    measure = relationship("IsmsEffectivenessMeasure", back_populates="metric_entries")
    source_event = relationship("Event", foreign_keys=[source_event_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        CheckConstraint(
            "metric_value is not null or qualitative_value <> ''",
            name="ck_isms_effectiveness_metric_entry_has_value",
        ),
    )


class IsmsMeeting(Base):
    __tablename__ = "isms_meetings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(
        String(256), nullable=False, default="ISMS meeting", index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[dtime | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[dtime | None] = mapped_column(Time, nullable=True)
    agenda_minutes_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    created_by = relationship("User", foreign_keys=[created_by_user_id])
    attendees = relationship(
        "IsmsMeetingAttendee", back_populates="meeting", cascade="all, delete-orphan"
    )
    links = relationship(
        "IsmsMeetingLink", back_populates="meeting", cascade="all, delete-orphan"
    )


class IsmsMeetingAttendee(Base):
    __tablename__ = "isms_meeting_attendees"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_meetings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    attendance_type: Mapped[str] = mapped_column(
        String(16), nullable=False, primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    meeting = relationship("IsmsMeeting", back_populates="attendees")
    user = relationship("User")

    __table_args__ = (
        CheckConstraint(
            "attendance_type in ('attendee','apology')",
            name="ck_isms_meeting_attendees_type",
        ),
    )


class IsmsMeetingPerson(Base):
    __tablename__ = "isms_meeting_people"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_meetings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_people.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    attendance_type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    email: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    person = relationship("IsmsPerson")


class IsmsMeetingLink(Base):
    __tablename__ = "isms_meeting_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_meetings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    link_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="external_url", index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_documents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    meeting = relationship("IsmsMeeting", back_populates="links")
    document = relationship("IsmsDocument")


class IsmsEntityControlLink(Base):
    __tablename__ = "isms_entity_control_links"

    entity_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    framework_slug: Mapped[str] = mapped_column(
        String(64), primary_key=True, index=True
    )
    control_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("control_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    control = relationship("ControlItem")
    created_by = relationship("User")


class IsmsEntityClauseLink(Base):
    __tablename__ = "isms_entity_clause_links"

    entity_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    framework_slug: Mapped[str] = mapped_column(
        String(64), primary_key=True, index=True
    )
    clause_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("framework_clauses.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    clause = relationship("FrameworkClause")
    created_by = relationship("User")


# -----------------------------------------------------------------------------
# Audit engagements (auditor-facing audits, distinct from internal AuditLog)
# -----------------------------------------------------------------------------


class Audit(ScheduleColumns, Base):
    __tablename__ = "audits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    framework_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    audit_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="internal"
    )

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Scheduled audit templates use status='template'. Their schedule (the
    # ScheduleColumns) lives on the template row; due runs create ordinary open
    # audits and copy the template scope and attendee rows.
    schedule_last_created_audit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    final_report_storage_uri: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )
    final_report_filename: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    final_report_content_type: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    final_report_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    final_report_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_report_uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    final_report_uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)

    created_by = relationship("User", foreign_keys=[created_by_user_id])
    final_report_uploaded_by = relationship(
        "User", foreign_keys=[final_report_uploaded_by_user_id]
    )
    evidence = relationship(
        "AuditEvidence", back_populates="audit", cascade="all, delete-orphan"
    )
    scoped_controls = relationship(
        "AuditScopedControl", back_populates="audit", cascade="all, delete-orphan"
    )
    scoped_clauses = relationship(
        "AuditScopedClause", back_populates="audit", cascade="all, delete-orphan"
    )
    scoped_documents = relationship(
        "AuditScopedIsmsDocument", back_populates="audit", cascade="all, delete-orphan"
    )
    attendees = relationship(
        "AuditAttendee", back_populates="audit", cascade="all, delete-orphan"
    )
    findings = relationship(
        "AuditFinding", back_populates="audit", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status in ('open','in_progress','completed','archived','template')",
            name="ck_audits_status",
        ),
        CheckConstraint(
            "audit_type in ('internal','external')",
            name="ck_audits_audit_type",
        ),
        *schedule_constraints("audits"),
    )


class AuditScopedControl(Base):
    __tablename__ = "audit_scoped_controls"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE"),
        primary_key=True,
    )
    control_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("control_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    audit = relationship("Audit", back_populates="scoped_controls")
    control = relationship("ControlItem")


class AuditScopedClause(Base):
    __tablename__ = "audit_scoped_clauses"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE"),
        primary_key=True,
    )
    clause_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("framework_clauses.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    audit = relationship("Audit", back_populates="scoped_clauses")
    clause = relationship("FrameworkClause")


class AuditScopedIsmsDocument(Base):
    __tablename__ = "audit_scoped_isms_documents"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    audit = relationship("Audit", back_populates="scoped_documents")
    document = relationship("IsmsDocument")


class AuditEvidence(Base):
    __tablename__ = "audit_evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    entity_type: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    evidence_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    added_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)

    audit = relationship("Audit", back_populates="evidence")
    event = relationship("Event")
    added_by = relationship("User")

    __table_args__ = (
        UniqueConstraint("audit_id", "event_id", name="uq_audit_evidence_event"),
        UniqueConstraint(
            "audit_id", "entity_type", "entity_id", name="uq_audit_evidence_entity"
        ),
    )


class AuditAttendee(Base):
    __tablename__ = "audit_attendees"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_people.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # For custom attendees this is the free-text name. For Keen users it is a
    # display snapshot, so exports keep a readable name even if the account is
    # later renamed/deactivated/deleted.
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    # Set when the attendee is reminded that the audit starts soon.
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    audit = relationship("Audit", back_populates="attendees")
    user = relationship("User")
    person = relationship("IsmsPerson")


class AuditFinding(Base):
    __tablename__ = "audit_findings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    control_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("control_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    audit = relationship("Audit", back_populates="findings")
    control = relationship("ControlItem")
    created_by = relationship("User")


# -----------------------------------------------------------------------------
# Event questions (auditor Q&A threads)
# -----------------------------------------------------------------------------


class EventQuestionThread(Base):
    __tablename__ = "event_question_threads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=True
    )
    target_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="event", index=True
    )
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    target_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unanswered"
    )

    # Notification helpers
    last_admin_reply_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    author_last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    event = relationship("Event")
    created_by = relationship("User")
    posts = relationship(
        "EventQuestionPost",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="EventQuestionPost.created_at",
    )


class EventQuestionPost(Base):
    __tablename__ = "event_question_posts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_question_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    thread = relationship("EventQuestionThread", back_populates="posts")
    author = relationship("User")
    attachments = relationship(
        "EventQuestionPostAttachment",
        back_populates="post",
        cascade="all, delete-orphan",
        order_by="EventQuestionPostAttachment.created_at",
    )


class EventQuestionPostAttachment(Base):
    __tablename__ = "event_question_post_attachments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_question_posts.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artifacts.id", ondelete="CASCADE"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    post = relationship("EventQuestionPost", back_populates="attachments")
    artifact = relationship("Artifact")


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    auth_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    auth_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    username: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    auth_options: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    encrypted_secret: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class IntegrationCollector(Base):
    __tablename__ = "integration_collectors"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("integration_connections.id"), nullable=False
    )
    draft: Mapped[dict] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    live_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cursor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IntegrationRevision(Base):
    __tablename__ = "integration_revisions"
    collector_id: Mapped[str] = mapped_column(
        ForeignKey("integration_collectors.id"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("integration_connections.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )


class IntegrationRun(Base):
    __tablename__ = "integration_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    collector_id: Mapped[str] = mapped_column(
        ForeignKey("integration_collectors.id"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    new_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    preview: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("integration_connections.id"), nullable=False
    )
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class AgentEnrollmentProfile(Base):
    __tablename__ = "agent_enrollment_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    token_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    max_enrollments: Mapped[int | None] = mapped_column(Integer)
    enrollment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    allowed_cidrs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    labels: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    subject_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pairwise", server_default="pairwise"
    )
    evidence_mode_ceiling: Mapped[str] = mapped_column(
        String(32), nullable=False, default="events", server_default="events"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (
        CheckConstraint(
            "subject_type IN ('pairwise','public')", name="ck_profile_subject_type"
        ),
        CheckConstraint(
            "evidence_mode_ceiling IN ('events','events_with_lines','all_lines')",
            name="ck_profile_evidence_mode",
        ),
    )


class KeenAgent(Base):
    __tablename__ = "keen_agents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    enrollment_profile_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_enrollment_profiles.id"), index=True
    )
    posture_assigned_at: Mapped[datetime | None] = mapped_column(DateTime)
    agent_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="server", server_default="server"
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_people.id", ondelete="SET NULL"),
        index=True,
    )
    enrollment_nonce_hash: Mapped[str | None] = mapped_column(String(64))
    enrollment_ip: Mapped[str | None] = mapped_column(String(64))
    enrollment_labels: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    previous_token_hash: Mapped[str | None] = mapped_column(String(64))
    renewal_nonce_hash: Mapped[str | None] = mapped_column(String(64))
    renewal_retry_until: Mapped[datetime | None] = mapped_column(DateTime)
    # keen-agent identities hold an Ed25519 key ("key"); OTLP collectors that
    # cannot sign hold a bearer token ("token").
    credential_type: Mapped[str] = mapped_column(
        String(8), nullable=False, default="key", server_default="token"
    )
    enrollment_code_hash: Mapped[str | None] = mapped_column(String(64))
    enrollment_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    validity_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=90, server_default="90"
    )
    subject_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pairwise", server_default="pairwise"
    )
    evidence_mode_ceiling: Mapped[str] = mapped_column(
        String(32), nullable=False, default="events", server_default="events"
    )
    ueu_last_sequence: Mapped[int | None] = mapped_column(BigInteger)
    ueu_last_hash: Mapped[str | None] = mapped_column(String(80))
    __table_args__ = (
        UniqueConstraint(
            "enrollment_profile_id",
            "enrollment_nonce_hash",
            name="uq_agent_enrollment_attempt",
        ),
        CheckConstraint(
            "credential_type IN ('key','token')", name="ck_agent_credential_type"
        ),
        CheckConstraint(
            "subject_type IN ('pairwise','public')", name="ck_agent_subject_type"
        ),
        CheckConstraint(
            "evidence_mode_ceiling IN ('events','events_with_lines','all_lines')",
            name="ck_agent_evidence_mode",
        ),
        CheckConstraint(
            "agent_type IN ('server', 'workstation')", name="ck_agent_type"
        ),
        CheckConstraint(
            "agent_type = 'workstation' OR person_id IS NULL", name="ck_agent_person"
        ),
    )

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)
    health: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class AgentKey(Base):
    """An agent's Ed25519 public key. KEEN never holds agent private keys."""

    __tablename__ = "agent_keys"
    key_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("keen_agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    public_key: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime)
    previous_key_id: Mapped[str | None] = mapped_column(String(64))
    # How the key was bound, and a digest of the single-use credential that
    # bound it (enrolment code, profile enrolment nonce or renewal nonce), so
    # a retried request after a lost response returns the same binding.
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    credential_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        CheckConstraint(
            "state IN ('active','retired','revoked')", name="ck_agent_key_state"
        ),
        CheckConstraint(
            "origin IN ('code','profile','renewal')", name="ck_agent_key_origin"
        ),
        UniqueConstraint("agent_id", "credential_hash", name="uq_agent_key_credential"),
        Index(
            "uq_agent_keys_one_active",
            "agent_id",
            unique=True,
            postgresql_where=sql_text("state = 'active'"),
            sqlite_where=sql_text("state = 'active'"),
        ),
    )


class UeuBatch(Base):
    """A received batch manifest and its signature, kept for re-verification."""

    __tablename__ = "ueu_batches"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("keen_agents.id", ondelete="SET NULL"), index=True
    )
    key_id: Mapped[str | None] = mapped_column(String(64))
    manifest: Mapped[str | None] = mapped_column(Text)
    signature: Mapped[str | None] = mapped_column(String(128))
    verification: Mapped[str] = mapped_column(String(16), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, index=True
    )


class UeuEvidence(Base):
    """Provenance of one stored UEU envelope; deleted with its event."""

    __tablename__ = "ueu_evidence"
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    agent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("keen_agents.id", ondelete="SET NULL"), index=True
    )
    evidence_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ueu_batches.id", ondelete="SET NULL"),
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    key_id: Mapped[str | None] = mapped_column(String(64))
    evidence_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    sequence: Mapped[int | None] = mapped_column(BigInteger)
    verification: Mapped[str] = mapped_column(String(16), nullable=False)
    chain_status: Mapped[str] = mapped_column(String(16), nullable=False)
    schema_validation: Mapped[str] = mapped_column(String(32), nullable=False)
    catalogue: Mapped[str] = mapped_column(String(65), nullable=False)
    custody: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class UeuSettings(Base):
    """Singleton; the public subject key is encrypted with the integration key."""

    __tablename__ = "ueu_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_subject_key: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("id = 1", name="ck_ueu_settings_singleton"),)


class AgentPostureObservation(Base):
    """Latest evidence pointer per agent/provider/scope; deleted with its evidence."""

    __tablename__ = "agent_posture_observations"
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("keen_agents.id", ondelete="CASCADE"), primary_key=True
    )
    stream_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# Retention is managed with explicit SQL so database row locks, the storage
# outbox and deletion are one transaction. Register its tables for metadata and
# migration tooling as well (there are deliberately no ORM cascade relationships).
_evidence_retention_policy = Table(
    "evidence_retention_policy",
    Base.metadata,
    Column("id", Integer, primary_key=True),
    Column("mode", Text, nullable=False, server_default="disabled"),
    Column("value", BigInteger),
    Column(
        "updated_at",
        DateTime,
        nullable=False,
        server_default=sql_text("now() AT TIME ZONE 'UTC'"),
    ),
    Column("updated_by", Text),
    CheckConstraint("id = 1"),
    CheckConstraint("mode IN ('disabled','age','count')"),
    CheckConstraint("value > 0"),
    CheckConstraint(
        "(mode = 'disabled' AND value IS NULL) OR (mode <> 'disabled' AND value IS NOT NULL)"
    ),
)
_evidence_purge_jobs = Table(
    "evidence_purge_jobs",
    Base.metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("mode", Text, nullable=False),
    Column("value", BigInteger),
    Column("automatic", Boolean, nullable=False),
    Column("cutoff", DateTime, nullable=False),
    Column("status", Text, nullable=False, server_default="queued"),
    Column("deleted", BigInteger, nullable=False, server_default="0"),
    Column("requested_by", Text),
    Column(
        "created_at",
        DateTime,
        nullable=False,
        server_default=sql_text("now() AT TIME ZONE 'UTC'"),
    ),
    Column(
        "updated_at",
        DateTime,
        nullable=False,
        server_default=sql_text("now() AT TIME ZONE 'UTC'"),
    ),
    Column("last_error", Text),
    Column("policy_overrides", JSONB),
    CheckConstraint("mode IN ('disabled','age','count','all')"),
)
Index(
    "evidence_purge_one_active",
    sql_text("(true)"),
    unique=True,
    postgresql_where=sql_text("status IN ('queued','running')"),
    _table=_evidence_purge_jobs,
)
# Evidence writers check for manual purge jobs; automatic history stays out.
Index(
    "ix_evidence_purge_jobs_manual_status",
    _evidence_purge_jobs.c.status,
    _evidence_purge_jobs.c.updated_at,
    postgresql_where=sql_text("NOT automatic"),
)
_evidence_object_cleanup = Table(
    "evidence_object_cleanup",
    Base.metadata,
    Column("id", BigInteger, primary_key=True),
    Column("storage_uri", Text, nullable=False, unique=True),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column(
        "next_attempt_at",
        DateTime,
        nullable=False,
        server_default=sql_text("now() AT TIME ZONE 'UTC'"),
    ),
    Column("last_error", Text),
    Column(
        "created_at",
        DateTime,
        nullable=False,
        server_default=sql_text("now() AT TIME ZONE 'UTC'"),
    ),
    Index("evidence_object_cleanup_due", "next_attempt_at", "id"),
)
_audit_event_retention_holds = Table(
    "audit_event_retention_holds",
    Base.metadata,
    Column(
        "audit_id",
        UUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "event_id",
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Index("audit_event_retention_holds_event", "event_id"),
)

Index("ix_artifacts_storage_uri", Artifact.storage_uri)
Index("ix_isms_documents_storage_uri", IsmsDocument.storage_uri)
Index(
    "ix_bookstack_section_storage_uri",
    Base.metadata.tables["bookstack_section_evidence"].c.storage_uri,
)
Index("ix_audits_report_storage_uri", Audit.final_report_storage_uri)
Index("ix_question_attachments_artifact", EventQuestionPostAttachment.artifact_id)


class MfaCredential(Base):
    __tablename__ = "mfa_credentials"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credential_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MfaRecoveryCode(Base):
    __tablename__ = "mfa_recovery_codes"
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)


class MfaChallenge(Base):
    __tablename__ = "mfa_challenges"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    password_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class UserLoginIP(Base):
    __tablename__ = "user_login_ips"
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    address: Mapped[str] = mapped_column(String(45), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )


class SecurityNotification(Base):
    __tablename__ = "security_notifications"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    recipient: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(100), nullable=True)
    __table_args__ = (
        Index("ix_security_notifications_pending", "status", "next_attempt_at"),
    )


class SourceIngestionState(Base):
    __tablename__ = "source_ingestion_states"
    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    updated_by: Mapped[str | None] = mapped_column(String(256), nullable=True)


class UserSsoEmail(Base):
    """Addresses whose ownership Keen has verified for future SSO linking."""

    __tablename__ = "user_sso_emails"
    email: Mapped[str] = mapped_column(String(256), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    verified_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )


class SsoEmailChallenge(Base):
    __tablename__ = "sso_email_challenges"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(256), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    mfa_version: Mapped[int] = mapped_column(Integer, nullable=False)
    password_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class SourceConnection(Base):
    """A named endpoint and encrypted credentials for a built-in source adapter."""

    __tablename__ = "source_connections"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    configuration: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    encrypted_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


_evidence_retention_overrides = Table(
    "evidence_retention_overrides",
    Base.metadata,
    Column("scope", Text, primary_key=True),
    Column("key", Text, primary_key=True),
    Column("mode", Text, nullable=False),
    Column("value", BigInteger),
    CheckConstraint("scope IN ('source','rule')"),
    CheckConstraint("length(key) BETWEEN 1 AND 128"),
    CheckConstraint(
        "(mode='disabled' AND value IS NULL) OR (mode='age' AND value IS NOT NULL AND value BETWEEN 1 AND 36500) OR (mode='count' AND value IS NOT NULL AND value BETWEEN 1 AND 1000000000)"
    ),
)


class Story(Base):
    """A curated set of events, records and notes that explain what happened."""

    __tablename__ = "stories"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    __table_args__ = (
        CheckConstraint("status in ('open','closed')", name="ck_stories_status"),
    )


class StoryItem(Base):
    __tablename__ = "story_items"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    story_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    label: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime)
    x: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    y: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (
        CheckConstraint(
            "kind in ('event','risk','document','asset','objective','swot','person',"
            "'vendor','effectiveness_measure','meeting','note')",
            name="ck_story_item_kind",
        ),
        CheckConstraint(
            "(kind = 'note') = (entity_id IS NULL)", name="ck_story_item_entity"
        ),
        UniqueConstraint("story_id", "kind", "entity_id", name="uq_story_item_entity"),
        Index("ix_story_items_entity", "kind", "entity_id"),
    )


class StoryLink(Base):
    __tablename__ = "story_links"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    story_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stories.id", ondelete="CASCADE"), nullable=False
    )
    from_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("story_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("story_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    __table_args__ = (
        CheckConstraint("from_item_id <> to_item_id", name="ck_story_link_ends"),
        UniqueConstraint(
            "story_id", "from_item_id", "to_item_id", name="uq_story_link"
        ),
    )


class StoryComment(Base):
    __tablename__ = "story_comments"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    story_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stories.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    __table_args__ = (
        Index("ix_story_comments_story_created", "story_id", "created_at"),
    )


# --- Quizzes -----------------------------------------------------------------------
#
# A quiz is a set of multiple-choice and free-text questions linked to controls
# through the effectiveness measure it owns. Each round is a dated sitting for
# the quiz's audience; a person sits one attempt per round. An attempt keeps a
# snapshot of the questions as shown, in its own shuffled order, so later edits
# to the quiz leave its answers readable.

QUIZ_STATUSES = ("draft", "active", "archived")
QUIZ_QUESTION_KINDS = ("choice", "text")
QUIZ_REVEAL_ANSWERS = ("never", "after_close", "after_submit")
QUIZ_ROUND_STATUSES = ("open", "closed")
QUIZ_ATTEMPT_STATUSES = ("in_progress", "submitted", "complete")


def _quoted(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


class Quiz(ScheduleColumns, Base):
    __tablename__ = "quizzes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    framework_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", index=True
    )
    # Percentage needed to pass, also the effectiveness measure's target.
    pass_mark: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Questions drawn for each sitting; None asks every question.
    questions_per_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Questions per page; progress is saved as the student turns each page.
    page_size: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    show_scores: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reveal_answers: Mapped[str] = mapped_column(
        String(16), nullable=False, default="after_close"
    )
    # Days a scheduled round stays open before it falls due.
    due_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    # The first scheduled round; exact recurrences keep its day of the month.
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The measure the quiz's round averages are recorded on; it goes with the
    # quiz and cannot be deleted while the quiz exists.
    measure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_effectiveness_measures.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    measure = relationship("IsmsEffectivenessMeasure")
    questions = relationship(
        "QuizQuestion",
        back_populates="quiz",
        cascade="all, delete-orphan",
        order_by="QuizQuestion.position",
    )
    audience = relationship(
        "QuizAudience", back_populates="quiz", cascade="all, delete-orphan"
    )
    rounds = relationship(
        "QuizRound",
        back_populates="quiz",
        cascade="all, delete-orphan",
        order_by="QuizRound.opens_on.desc()",
    )

    __table_args__ = (
        CheckConstraint(
            f"status in ({_quoted(QUIZ_STATUSES)})", name="ck_quizzes_status"
        ),
        CheckConstraint(
            f"reveal_answers in ({_quoted(QUIZ_REVEAL_ANSWERS)})",
            name="ck_quizzes_reveal_answers",
        ),
        CheckConstraint(
            "pass_mark is null or (pass_mark >= 0 and pass_mark <= 100)",
            name="ck_quizzes_pass_mark",
        ),
        CheckConstraint(
            "questions_per_attempt is null or questions_per_attempt >= 1",
            name="ck_quizzes_questions_per_attempt",
        ),
        CheckConstraint(
            "page_size >= 1 and page_size <= 50", name="ck_quizzes_page_size"
        ),
        CheckConstraint(
            "due_days >= 1 and due_days <= 365", name="ck_quizzes_due_days"
        ),
        *schedule_constraints("quizzes"),
    )


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    quiz_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quizzes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="choice")
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # Shown with the correct answer once the quiz reveals answers.
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    quiz = relationship("Quiz", back_populates="questions")
    options = relationship(
        "QuizOption",
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="QuizOption.position",
    )

    __table_args__ = (
        CheckConstraint(
            f"kind in ({_quoted(QUIZ_QUESTION_KINDS)})", name="ck_quiz_questions_kind"
        ),
    )


class QuizOption(Base):
    __tablename__ = "quiz_options"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quiz_questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    question = relationship("QuizQuestion", back_populates="options")


class QuizAudience(Base):
    """A person or an organisation role a quiz is assigned to."""

    __tablename__ = "quiz_audience"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    quiz_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quizzes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_people.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    org_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_org_nodes.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    quiz = relationship("Quiz", back_populates="audience")
    person = relationship("IsmsPerson")
    org_node = relationship("IsmsOrgNode")

    __table_args__ = (
        CheckConstraint(
            "(person_id is null) <> (org_node_id is null)",
            name="ck_quiz_audience_member",
        ),
        UniqueConstraint("quiz_id", "person_id", name="uq_quiz_audience_person"),
        UniqueConstraint("quiz_id", "org_node_id", name="uq_quiz_audience_org_node"),
    )


class QuizRound(Base):
    """One dated sitting of a quiz for its audience."""

    __tablename__ = "quiz_rounds"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    quiz_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quizzes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    opens_on: Mapped[date] = mapped_column(Date, nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    # True when the quiz's schedule created the round.
    scheduled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    quiz = relationship("Quiz", back_populates="rounds")
    participants = relationship(
        "QuizParticipant", back_populates="round", cascade="all, delete-orphan"
    )
    attempts = relationship(
        "QuizAttempt", back_populates="round", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            f"status in ({_quoted(QUIZ_ROUND_STATUSES)})",
            name="ck_quiz_rounds_status",
        ),
        CheckConstraint("due_on >= opens_on", name="ck_quiz_rounds_dates"),
        Index("ix_quiz_rounds_status_due_on", "status", "due_on"),
    )


class QuizParticipant(Base):
    """A person a round is assigned to, with the emails sent to them."""

    __tablename__ = "quiz_participants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    round_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quiz_rounds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_people.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    # Set when the person is told the round is open, and when reminded it is due.
    invited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    round = relationship("QuizRound", back_populates="participants")
    person = relationship("IsmsPerson")

    __table_args__ = (
        UniqueConstraint("round_id", "person_id", name="uq_quiz_participant"),
    )


class QuizAttempt(Base):
    """One person's sitting of a round, with the questions as shown to them."""

    __tablename__ = "quiz_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    quiz_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quizzes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    round_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quiz_rounds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("isms_people.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="in_progress"
    )
    # The questions in the order shown, each with its options in shown order:
    # [{"question_id", "kind", "prompt", "explanation",
    #   "options": [{"id", "text", "correct"}]}]. Never sent to the student as is.
    layout: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    page_size: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    # Pages whose answers are saved; the student resumes on the next one.
    pages_saved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    saved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Percentage, once every answer is marked.
    score: Mapped[float | None] = mapped_column(Float, nullable=True)

    round = relationship("QuizRound", back_populates="attempts")
    person = relationship("IsmsPerson")
    answers = relationship(
        "QuizAnswer",
        back_populates="attempt",
        cascade="all, delete-orphan",
        order_by="QuizAnswer.position",
    )

    __table_args__ = (
        CheckConstraint(
            f"status in ({_quoted(QUIZ_ATTEMPT_STATUSES)})",
            name="ck_quiz_attempts_status",
        ),
        CheckConstraint(
            "score is null or (score >= 0 and score <= 100)",
            name="ck_quiz_attempts_score",
        ),
        UniqueConstraint("round_id", "person_id", name="uq_quiz_attempt_person"),
    )


class QuizAnswer(Base):
    __tablename__ = "quiz_answers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quiz_attempts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The question's place in the attempt's layout.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quiz_questions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="choice")
    # A chosen option from the layout; options can change after an attempt.
    option_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    text_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # A free-text grade from 1 to 5 in half points.
    grade: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedback: Mapped[str] = mapped_column(Text, nullable=False, default="")
    graded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    graded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The answer's share of one question's mark, from 0 to 1, once marked.
    points: Mapped[float | None] = mapped_column(Float, nullable=True)

    attempt = relationship("QuizAttempt", back_populates="answers")

    __table_args__ = (
        CheckConstraint(
            f"kind in ({_quoted(QUIZ_QUESTION_KINDS)})", name="ck_quiz_answers_kind"
        ),
        CheckConstraint(
            "grade is null or (grade >= 1 and grade <= 5 and grade * 2 = round(grade * 2))",
            name="ck_quiz_answers_grade",
        ),
        CheckConstraint(
            "points is null or (points >= 0 and points <= 1)",
            name="ck_quiz_answers_points",
        ),
        UniqueConstraint("attempt_id", "position", name="uq_quiz_answer_position"),
    )
