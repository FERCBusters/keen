from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field


class AuditCreatePayload(BaseModel):
    swot_ids: list[uuid.UUID] | None = Field(None, max_length=200)
    title: str = Field(..., min_length=1, max_length=256)
    framework_slug: str | None = Field(default=None, max_length=64)
    audit_type: str | None = Field(default="internal", max_length=16)
    start_date: date | None = None
    end_date: date | None = None
    notes: str | None = Field(default="", max_length=20000)
    report_notes: str | None = Field(default=None, max_length=20000)
    executive_summary: str | None = Field(default="", max_length=20000)
    controls: list[str] = Field(default_factory=list)
    clauses: list[str] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=list)


class AuditUpdatePayload(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    framework_slug: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=32)
    audit_type: str | None = Field(default=None, max_length=16)
    start_date: date | None = None
    end_date: date | None = None
    notes: str | None = Field(default=None, max_length=20000)
    report_notes: str | None = Field(default=None, max_length=20000)
    executive_summary: str | None = Field(default=None, max_length=20000)


class AuditEvidencePayload(BaseModel):
    event_id: uuid.UUID | None = None
    entity_type: str | None = Field(default=None, max_length=64)
    entity_id: uuid.UUID | None = None
    evidence_url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=512)
    notes: str | None = Field(default="", max_length=20000)


class AuditEvidenceUpdatePayload(BaseModel):
    evidence_url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=512)
    notes: str | None = Field(default=None, max_length=20000)


class AuditControlsPayload(BaseModel):
    controls: list[str] = Field(default_factory=list)


class AuditScopePayload(BaseModel):
    swot_ids: list[uuid.UUID] | None = Field(None, max_length=200)
    controls: list[str] = Field(default_factory=list)
    clauses: list[str] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=list)


class AuditAttendeePayload(BaseModel):
    user_id: uuid.UUID | None = None
    person_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=256)
    email: str | None = Field(default=None, max_length=256)
    role: str | None = Field(default=None, max_length=128)


class AuditFindingPayload(BaseModel):
    kind: str = Field(..., max_length=32)
    title: str = Field(..., min_length=1, max_length=256)
    description: str | None = Field(default="", max_length=50000)
    control: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default="open", max_length=32)


class AuditFindingUpdatePayload(BaseModel):
    kind: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=50000)
    control: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=32)


class ScheduledAuditAttendeePayload(BaseModel):
    user_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=256)
    email: str | None = Field(default=None, max_length=256)
    role: str | None = Field(default=None, max_length=128)


class ScheduledAuditPayload(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    framework_slug: str | None = Field(default=None, max_length=64)
    audit_type: str | None = Field(default="internal", max_length=16)
    start_date: date
    end_date: date | None = None
    schedule_recurrence: str | None = Field(default="once", max_length=32)
    schedule_interval: int | None = Field(default=1, ge=1, le=120)
    schedule_date_rule: str | None = Field(default="exact", max_length=32)
    schedule_anchor_month: int | None = Field(default=None, ge=1, le=12)
    schedule_until_date: date | None = None
    report_notes: str | None = Field(default="", max_length=20000)
    executive_summary: str | None = Field(default="", max_length=20000)
    controls: list[str] = Field(default_factory=list)
    clauses: list[str] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=list)
    attendees: list[ScheduledAuditAttendeePayload] = Field(default_factory=list)


class ScheduledAuditUpdatePayload(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    framework_slug: str | None = Field(default=None, max_length=64)
    audit_type: str | None = Field(default=None, max_length=16)
    start_date: date | None = None
    end_date: date | None = None
    schedule_recurrence: str | None = Field(default=None, max_length=32)
    schedule_interval: int | None = Field(default=None, ge=1, le=120)
    schedule_date_rule: str | None = Field(default=None, max_length=32)
    schedule_anchor_month: int | None = Field(default=None, ge=1, le=12)
    schedule_until_date: date | None = None
    report_notes: str | None = Field(default=None, max_length=20000)
    executive_summary: str | None = Field(default=None, max_length=20000)
    controls: list[str] | None = None
    clauses: list[str] | None = None
    documents: list[str] | None = None
    attendees: list[ScheduledAuditAttendeePayload] | None = None
