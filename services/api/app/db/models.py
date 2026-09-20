from __future__ import annotations

import uuid
from datetime import date, datetime, time as dtime
from sqlalchemy import (
    Table,
    Column,
    String,
    DateTime,
    Date,
    Time,
    Integer,
    Float,
    Boolean,
    ForeignKey,
    UniqueConstraint,
    CheckConstraint,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class Framework(Base):
    __tablename__ = "frameworks"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )  # e.g. ISO27001:2022
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
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


class GlobalEventStats(Base):
    __tablename__ = "global_event_stats"

    stats_key: Mapped[str] = mapped_column(
        String(32), primary_key=True, default="events"
    )
    total_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "total_events >= 0",
            name="ck_global_event_stats_total_nonnegative",
        ),
    )


class FrameworkEventStats(Base):
    __tablename__ = "framework_event_stats"

    framework_slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    mapped_event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "mapped_event_count >= 0",
            name="ck_framework_event_stats_count_nonnegative",
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

    This is intended to support ISO27001:2022 A.8.34 by providing evidence of
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

    # UI theme id (e.g. "purple", "ocean", ...)
    pref_theme: Mapped[str] = mapped_column(
        String(32), default="purple", nullable=False
    )

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

    # Preferred visualisation mode (graph | heatmap | sunburst_source | sunburst_control | histogram)
    pref_viz_mode: Mapped[str] = mapped_column(
        String(32), default="graph", nullable=False
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

    # Preferred default framework slug for this user (e.g. "ISO27001:2022").
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
    owner = relationship("User", foreign_keys=[risk_owner_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    control_links = relationship(
        "RiskControlLink", back_populates="risk", cascade="all, delete-orphan"
    )

    __table_args__ = (
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
# Interested Parties risk/context assessment
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
    __tablename__ = "interested_parties"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    framework_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
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
            "framework_slug",
            "name_id",
            "nature_id",
            name="uq_interested_parties_framework_name_nature",
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
            "status in ('not_started','in_progress','completed','deferred','superseded')",
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
    effectiveness_measure: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metric: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metric_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, index=True
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
        String(256), nullable=False, default="ISMS Meeting", index=True
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


class Audit(Base):
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

    # Scheduled audit templates use status='template'. Their schedule metadata
    # lives on the template row; due runs create ordinary open audits and copy
    # the template scope and attendee rows.
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
        CheckConstraint(
            "schedule_recurrence in ('once','weekly','monthly','quarterly','yearly')",
            name="ck_audits_schedule_recurrence",
        ),
        CheckConstraint(
            "schedule_date_rule in ('exact','first_weekday_of_month','first_monday_of_month','first_tuesday_of_month','first_wednesday_of_month','first_thursday_of_month','first_friday_of_month')",
            name="ck_audits_schedule_date_rule",
        ),
        CheckConstraint(
            "schedule_anchor_month is null or (schedule_anchor_month >= 1 and schedule_anchor_month <= 12)",
            name="ck_audits_schedule_anchor_month",
        ),
        CheckConstraint(
            "schedule_interval >= 1",
            name="ck_audits_schedule_interval",
        ),
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
    # For custom attendees this is the free-text name. For Keen users it is a
    # display snapshot, so exports keep a readable name even if the account is
    # later renamed/deactivated/deleted.
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    audit = relationship("Audit", back_populates="attendees")
    user = relationship("User")


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
