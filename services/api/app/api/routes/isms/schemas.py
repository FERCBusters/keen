"""ISMS schemas; see docs/maintainability-review.md for module boundaries."""

from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import datetime
from datetime import time as time_type
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.security.urls import external_url as validate_external_url


class IsmsLinksPayload(BaseModel):
    controls: list[str] | None = None
    clauses: list[str] | None = None


class ObjectivePayload(IsmsLinksPayload):
    requirement: str | None = Field(default=None, max_length=20000)
    goal: str | None = Field(default=None, max_length=20000)
    metric: str | None = Field(default=None, max_length=20000)
    completion_method: str | None = Field(default=None, max_length=12000)
    resource_requirements_text: str | None = Field(default=None, max_length=12000)
    resource_user_ids: list[uuid.UUID] | None = None
    owner_user_id: uuid.UUID | None = None
    completion_target_date: str | None = Field(default=None, max_length=64)
    evaluation_method: str | None = Field(default=None, max_length=12000)
    status: str | None = Field(default=None, max_length=32)


class DocumentPayload(IsmsLinksPayload):
    title: str | None = Field(default=None, max_length=256)
    document_type: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=20000)
    external_url: str | None = Field(default=None, max_length=2048)
    folder_id: uuid.UUID | None = None
    tags: list[str] | None = None
    content_html: str | None = Field(default=None, max_length=200000)
    expected_content_version: int | None = None

    _validate_url = field_validator("external_url")(validate_external_url)


class OrgNodePayload(IsmsLinksPayload):
    parent_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=256)
    node_type: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=20000)
    sort_order: int | None = None
    user_ids: list[uuid.UUID] | None = None
    relationship_type: str | None = Field(default="member", max_length=32)


class AssetPayload(IsmsLinksPayload):
    # ``asset`` is retained as the ISMS-facing field name. ``name`` and
    # ``asset_name`` keep compatibility with the CIA Triad risk asset API.
    asset: str | None = Field(default=None, max_length=256)
    name: str | None = Field(default=None, max_length=256)
    asset_name: str | None = Field(default=None, max_length=256)
    category_id: uuid.UUID | None = None
    category_name: str | None = Field(default=None, max_length=128)
    subcategory_id: uuid.UUID | None = None
    subcategory_name: str | None = Field(default=None, max_length=128)
    license_id: uuid.UUID | None = None
    license_name: str | None = Field(default=None, max_length=256)
    # Compatibility for older clients. New UI/API clients should send license_id.
    license: str | None = Field(default=None, max_length=256)
    owner_org_node_id: uuid.UUID | None = None
    register_held_by_org_node_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=20000)


class AssetCategoryPayload(BaseModel):
    name: str | None = Field(default=None, max_length=128)


class AssetSubcategoryPayload(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    category_id: uuid.UUID | None = None
    category_name: str | None = Field(default=None, max_length=128)


class LicensePayload(BaseModel):
    name: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=12000)


class BusinessProcessPayload(BaseModel):
    name: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=20000)
    sort_order: int | None = None


class AwsAccountPayload(BaseModel):
    name: str | None = Field(default=None, max_length=256)
    account_id: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=12000)


class AccessControlMatrixPayload(BaseModel):
    task_action: str | None = Field(default=None, max_length=20000)
    service_asset_id: uuid.UUID | None = None
    aws_account_ids: list[uuid.UUID] | None = None
    status: str | None = Field(default=None, max_length=32)
    approved_by_user_id: uuid.UUID | None = None
    # New clients send role_org_node_ids because one access row can apply to several
    # organisation-chart roles. role_org_node_id is accepted for older clients.
    role_org_node_ids: list[uuid.UUID] | None = None
    role_org_node_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=12000)


class EffectivenessMeasurePayload(IsmsLinksPayload):
    summary: str | None = Field(default=None, max_length=20000)
    description: str | None = Field(default=None, max_length=40000)
    effectiveness_measure: str | None = Field(default=None, max_length=20000)
    metric: str | None = Field(default=None, max_length=20000)
    metric_key: str | None = Field(default=None, max_length=128)
    target_value: float | None = None
    target_unit: str | None = Field(default=None, max_length=64)
    threshold_operator: str | None = Field(default=None, max_length=16)
    owner_user_id: uuid.UUID | None = None
    frequency: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=12000)


class EffectivenessMetricEntryPayload(BaseModel):
    recorded_at: datetime | None = None
    period_start: date_type | None = None
    period_end: date_type | None = None
    metric_value: float | None = None
    metric_unit: str | None = Field(default=None, max_length=64)
    qualitative_value: str | None = Field(default=None, max_length=20000)
    source_type: str | None = Field(default=None, max_length=64)
    source_title: str | None = Field(default=None, max_length=256)
    source_url: str | None = Field(default=None, max_length=2048)
    source_reference: str | None = Field(default=None, max_length=256)
    source_event_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=12000)
    raw_payload: dict[str, Any] | None = None

    _validate_url = field_validator("source_url")(validate_external_url)


class MeetingLinkPayload(BaseModel):
    link_type: str = Field(default="external_url", max_length=32)
    document_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=256)
    url: str | None = Field(default=None, max_length=2048)

    _validate_url = field_validator("url")(validate_external_url)


class MeetingPayload(IsmsLinksPayload):
    title: str | None = Field(default=None, max_length=256)
    date: date_type | None = None
    start_time: time_type | None = None
    end_time: time_type | None = None
    attendee_user_ids: list[uuid.UUID] | None = None
    apology_user_ids: list[uuid.UUID] | None = None
    attendee_person_ids: list[uuid.UUID] | None = None
    apology_person_ids: list[uuid.UUID] | None = None
    links: list[MeetingLinkPayload] | None = None
    agenda_minutes_notes: str | None = Field(default=None, max_length=50000)


class DocumentFolderPayload(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    parent_id: uuid.UUID | None = None


class DocumentCommentPayload(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
