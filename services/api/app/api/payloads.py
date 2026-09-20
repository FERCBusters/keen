from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.config import settings
from app.security.auth import ROLE_NORMAL


class LoginPayload(BaseModel):
    username: str
    password: str


class ChangePasswordPayload(BaseModel):
    current_password: str
    new_password: str


class PreferencesOut(BaseModel):
    use_local_timezone: bool = Field(default=False)
    timezone: str | None = Field(default=None)
    theme: str = Field(default="purple")
    # Visible date input format: default, ymd, or dmy. API values remain YYYY-MM-DD.
    date_format: str = Field(default="default")
    auto_apply_filters: bool = Field(default=False)
    source_colors: dict[str, str] = Field(default_factory=dict)

    # Legacy: visualisation page default mode (mode only)
    viz_mode: str = Field(default="graph")

    # New: default Visualisation page state (mode + date range + sunburst focus).
    # When unset/None, the UI falls back to Graph + last 7 days.
    default_visualisation: dict | None = Field(default=None)

    # Default landing page when opening the app authenticated
    landing_page: str = Field(default="/")

    # Preferred framework slug when opening the app/signing in.
    default_framework: str | None = Field(default=None)


class PreferencesUpdatePayload(BaseModel):
    use_local_timezone: bool | None = None
    timezone: str | None = None
    theme: str | None = None
    date_format: str | None = None
    auto_apply_filters: bool | None = None
    source_colors: dict[str, str] | None = None

    viz_mode: str | None = None
    default_visualisation: dict | None = None
    landing_page: str | None = None
    default_framework: str | None = None


class SavedSearchCreatePayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    url: str = Field(..., min_length=1, max_length=2048)


class SavedSearchOut(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    created_at: datetime
    updated_at: datetime


class IncidentCreatePayload(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    text: str = Field(default="", max_length=5000)


class UserCreatePayload(BaseModel):
    username: str
    password: str
    role: str = ROLE_NORMAL
    email: str | None = None


class UserUpdatePayload(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    email: str | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    username: str
    email: str | None = None
    role: str
    # Resolved role after applying group-role inheritance when role='inherit'.
    effective_role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None


class ControlImportItem(BaseModel):
    type: str = Field(description="clause|annex_control|custom")
    ref: str
    title: str | None = None
    in_scope: bool = True
    tags: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class ControlImportPayload(BaseModel):
    framework: str = settings.default_framework_slug
    framework_name: str | None = None
    framework_version: str | None = None
    framework_url: str | None = None
    framework_source: str | None = None
    items: list[ControlImportItem]


class ControlJustificationPayload(BaseModel):
    justification: str | None = Field(default=None, max_length=128)


class ControlClauseLinkItem(BaseModel):
    clause_id: uuid.UUID | None = None
    ref: str | None = Field(default=None, max_length=64)
    applicability: str = Field(..., max_length=32)


class ControlClauseLinksPayload(BaseModel):
    clauses: list[ControlClauseLinkItem] = Field(default_factory=list)


class ClauseControlLinkItem(BaseModel):
    control_id: uuid.UUID | None = None
    ref: str | None = Field(default=None, max_length=64)
    applicability: str = Field(..., max_length=32)


class ClauseControlLinksPayload(BaseModel):
    controls: list[ClauseControlLinkItem] = Field(default_factory=list)


class UpstreamUrlPayload(BaseModel):
    upstream_url: str | None = Field(default=None, max_length=2048)


class ClauseEvidenceMappingItem(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    url: str | None = Field(default=None, max_length=2048)


class ClauseEvidenceUrlsPayload(BaseModel):
    # New canonical shape: title + URL.
    evidence_mappings: list[ClauseEvidenceMappingItem] | None = None
    # Legacy URL-only shape, retained so older clients keep working.
    evidence_urls: list[str] = Field(default_factory=list)


class FrameworkListItem(BaseModel):
    slug: str
    name: str
    version: str | None = None
    description: str | None = None
    upstream_url: str | None = None
    control_count: int = 0
    is_default: bool = False


class FrameworkUpsert(BaseModel):
    slug: str
    name: str | None = None
    version: str | None = None
    description: str | None = None
    upstream_url: str | None = None


class AdminSetPasswordPayload(BaseModel):
    new_password: str


# -----------------------------------------------------------------------------
# RBAC: Groups + Permissions
# -----------------------------------------------------------------------------


class PermissionCreatePayload(BaseModel):
    code: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2048)


class PermissionOut(BaseModel):
    id: uuid.UUID
    code: str
    description: str | None = None
    created_at: datetime


class GroupCreatePayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=4096)
    # Optional role for this group: admin | normal | null
    role: str | None = Field(default=None, max_length=32)


class GroupUpdatePayload(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=4096)
    role: str | None = Field(default=None, max_length=32)


class GroupOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    role: str | None = None
    created_at: datetime
    member_count: int = 0
    permission_count: int = 0


class GroupDetailOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    role: str | None = None
    created_at: datetime
    user_ids: list[uuid.UUID] = Field(default_factory=list)
    permission_codes: list[str] = Field(default_factory=list)


class GroupMembersPayload(BaseModel):
    user_ids: list[uuid.UUID] = Field(default_factory=list)


class GroupPermissionsPayload(BaseModel):
    permission_codes: list[str] = Field(default_factory=list)


class UserAccessOut(BaseModel):
    user_id: uuid.UUID
    group_ids: list[uuid.UUID] = Field(default_factory=list)
    explicit_permission_codes: list[str] = Field(default_factory=list)
    effective_permission_codes: list[str] = Field(default_factory=list)


class UserAccessUpdatePayload(BaseModel):
    group_ids: list[uuid.UUID] | None = None
    explicit_permission_codes: list[str] | None = None
