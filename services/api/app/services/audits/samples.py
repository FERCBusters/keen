from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    BookStackSectionEvidence,
    ControlClauseLink,
    ControlItem,
    InterestedParty,
    InterestedPartyControlLink,
    IsmsAccessControlMatrixEntry,
    IsmsApplicationConfigurationEntry,
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    IsmsEntityControlLink,
    IsmsMeeting,
    IsmsObjective,
    IsmsOrgNode,
    IsmsSwot,
    PestleClauseRelevance,
    PestleItem,
    PestleRelevanceLevel,
    Risk,
    RiskAsset,
    RiskControlLink,
    Story,
)
from app.services.audits.references import control_out
from app.services.audits.validation import (
    AUDIT_SAMPLE_ENTITY_LABELS,
    try_uuid,
)
from app.services.errors import DomainError


def short_text(raw: str | None, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", (raw or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def clean_audit_sample_entity_type(raw: str | None) -> str | None:
    """Accept exactly the stored names, which the route feature policy checks."""
    value = (raw or "").strip()
    if not value:
        return None
    if value not in AUDIT_SAMPLE_ENTITY_LABELS:
        raise DomainError(status_code=400, detail="Unsupported sampled entity type")
    return value


def clean_optional_entity_id(raw: uuid.UUID | str | None) -> uuid.UUID | None:
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    return try_uuid(str(raw))


def entity_href(entity_type: str, entity_id: uuid.UUID, framework: str) -> str:
    eid = str(entity_id)
    fw = framework or settings.default_framework_slug or ""
    fwq = f"&framework={fw}" if fw else ""
    fwfirst = f"?framework={fw}" if fw else ""
    if entity_type == "risk":
        return f"/risk.html?id={eid}{fwq}"
    if entity_type == "pestle_item":
        return f"/pestle_item.html?id={eid}{fwq}"
    if entity_type == "interested_party":
        return f"/interested_party.html?id={eid}{fwq}"
    if entity_type == "isms_swot":
        return f"/isms-swot.html?id={eid}{fwq}"
    if entity_type == "story":
        return f"/story.html?id={eid}{fwq}"
    if entity_type == "isms_objective":
        return (
            f"/isms.html{fwfirst}&tab=objectives" if fw else "/isms.html?tab=objectives"
        )
    if entity_type == "isms_document":
        return (
            f"/isms.html{fwfirst}&tab=documents" if fw else "/isms.html?tab=documents"
        )
    if entity_type == "bookstack_section":
        return f"/api/v1/isms/bookstack-sections/{eid}/snapshot"
    if entity_type == "isms_org_node":
        return f"/isms.html{fwfirst}&tab=org" if fw else "/isms.html?tab=org"
    if entity_type == "isms_asset":
        return f"/isms.html{fwfirst}&tab=assets" if fw else "/isms.html?tab=assets"
    if entity_type == "isms_access_control_matrix":
        return f"/isms.html{fwfirst}&tab=access" if fw else "/isms.html?tab=access"
    if entity_type == "isms_application_configuration":
        return (
            f"/isms.html{fwfirst}&tab=appconfig" if fw else "/isms.html?tab=appconfig"
        )
    if entity_type == "isms_effectiveness_measure":
        return f"/isms-effectiveness-measure.html?id={eid}{fwq}"
    if entity_type == "isms_effectiveness_metric":
        return f"/isms-effectiveness-metric.html?id={eid}{fwq}"
    if entity_type == "isms_meeting":
        return f"/isms-meeting.html?id={eid}{fwq}"
    return "/audits.html"


def risk_sample_controls(
    db: Session, risk_id: uuid.UUID, framework: str
) -> list[dict[str, Any]]:
    rows = (
        db.query(ControlItem)
        .join(RiskControlLink, RiskControlLink.control_item_id == ControlItem.id)
        .filter(
            RiskControlLink.risk_id == risk_id,
            RiskControlLink.framework_slug == framework,
            ControlItem.framework_slug == framework,
        )
        .order_by(ControlItem.ref.asc())
        .all()
    )
    return [control_out(c) for c in rows]


def pestle_sample_controls(
    db: Session, item_id: uuid.UUID, framework: str
) -> list[dict[str, Any]]:
    rows = (
        db.query(ControlItem)
        .join(ControlClauseLink, ControlClauseLink.control_item_id == ControlItem.id)
        .join(
            PestleClauseRelevance,
            PestleClauseRelevance.clause_id == ControlClauseLink.clause_id,
        )
        .join(
            PestleRelevanceLevel,
            PestleRelevanceLevel.id == PestleClauseRelevance.relevance_id,
        )
        .filter(
            PestleClauseRelevance.pestle_item_id == item_id,
            ControlItem.framework_slug == framework,
            PestleRelevanceLevel.code != "na",
        )
        .distinct()
        .order_by(ControlItem.ref.asc())
        .all()
    )
    return [control_out(c) for c in rows]


def interested_party_sample_controls(
    db: Session, party_id: uuid.UUID, framework: str
) -> list[dict[str, Any]]:
    rows = (
        db.query(ControlItem)
        .join(
            InterestedPartyControlLink,
            InterestedPartyControlLink.control_item_id == ControlItem.id,
        )
        .filter(
            InterestedPartyControlLink.interested_party_id == party_id,
            InterestedPartyControlLink.framework_slug == framework,
            ControlItem.framework_slug == framework,
        )
        .order_by(ControlItem.ref.asc())
        .all()
    )
    return [control_out(c) for c in rows]


def isms_sample_controls(
    db: Session, entity_type: str, entity_id: uuid.UUID, framework: str
) -> list[dict[str, Any]]:
    isms_type = entity_type.replace("isms_", "", 1)
    if isms_type == "asset":
        isms_type = "asset"
    rows = (
        db.query(ControlItem)
        .join(
            IsmsEntityControlLink,
            IsmsEntityControlLink.control_item_id == ControlItem.id,
        )
        .filter(
            IsmsEntityControlLink.entity_type == isms_type,
            IsmsEntityControlLink.entity_id == entity_id,
            IsmsEntityControlLink.framework_slug == framework,
            ControlItem.framework_slug == framework,
        )
        .order_by(ControlItem.ref.asc())
        .all()
    )
    return [control_out(c) for c in rows]


def audit_sample_entity_reference(
    db: Session,
    *,
    framework: str,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    required: bool = True,
) -> dict[str, Any] | None:
    if not entity_type and not entity_id:
        return None
    if not entity_type or not entity_id:
        raise DomainError(
            status_code=400,
            detail="entity_type and entity_id must be provided together",
        )
    entity_type = clean_audit_sample_entity_type(entity_type)
    if entity_type is None:
        return None
    label = AUDIT_SAMPLE_ENTITY_LABELS.get(entity_type, "Keen item")
    controls: list[dict[str, Any]] = []
    title = label
    subtitle = ""
    row: Any = None

    if entity_type == "risk":
        row = db.query(Risk).filter(Risk.id == entity_id).one_or_none()
        if row:
            asset = getattr(row, "asset", None)
            title = f"CIA Triad risk: {getattr(asset, 'name', '') or 'Asset'}"
            if row.threat_summary:
                subtitle = short_text(row.threat_summary, 220)
            controls = risk_sample_controls(db, row.id, framework)
    elif entity_type == "pestle_item":
        row = (
            db.query(PestleItem)
            .filter(PestleItem.id == entity_id, PestleItem.framework_slug == framework)
            .one_or_none()
        )
        if row:
            title = f"PESTLE(E): {short_text(row.item, 180) or row.type}"
            subtitle = f"{row.type} · {row.lens}".strip(" ·")
            controls = pestle_sample_controls(db, row.id, framework)
    elif entity_type == "interested_party":
        row = db.get(InterestedParty, entity_id)
        if row:
            name = getattr(getattr(row, "name", None), "name", "") or "Interested party"
            nature = (
                getattr(getattr(row, "nature", None), "name", "")
                or "Nature of interest"
            )
            title = f"Interested party: {name} — {nature}"
            subtitle = short_text(row.note, 220)
            controls = interested_party_sample_controls(db, row.id, framework)
    elif entity_type == "isms_swot":
        row = db.get(IsmsSwot, entity_id)
        if row:
            title = f"SWOT {row.kind}: {short_text(row.title, 180)}"
            subtitle = short_text(row.description, 220)
            controls = isms_sample_controls(db, entity_type, row.id, framework)
    elif entity_type == "isms_objective":
        row = (
            db.query(IsmsObjective).filter(IsmsObjective.id == entity_id).one_or_none()
        )
        if row:
            title = f"ISMS objective: {short_text(row.goal or row.requirement, 180)}"
            subtitle = short_text(row.metric or row.requirement, 220)
            controls = isms_sample_controls(db, entity_type, row.id, framework)
    elif entity_type == "isms_document":
        row = db.query(IsmsDocument).filter(IsmsDocument.id == entity_id).one_or_none()
        if row:
            title = f"ISMS document: {row.title or 'Document'}"
            subtitle = row.document_type or ""
            controls = isms_sample_controls(db, entity_type, row.id, framework)
    elif entity_type == "bookstack_section":
        row = (
            db.query(BookStackSectionEvidence)
            .filter(BookStackSectionEvidence.id == entity_id)
            .one_or_none()
        )
        if (
            row
            and row.target_control
            and row.target_control.framework_slug == framework
        ):
            controls = [control_out(row.target_control)]
        elif (
            row and row.target_clause and row.target_clause.framework_slug == framework
        ):
            linked = (
                db.query(ControlItem)
                .join(
                    ControlClauseLink,
                    ControlClauseLink.control_item_id == ControlItem.id,
                )
                .filter(
                    ControlClauseLink.clause_id == row.target_clause_id,
                    ControlItem.framework_slug == framework,
                )
                .all()
            )
            controls = [control_out(c) for c in linked]
        else:
            row = None  # A snapshot may only be sampled under its target framework.
        if row:
            title = f"BookStack policy: {row.page_title}"
            subtitle = (
                f"Revision {row.revision_count or 'unknown'} · SHA-256 {row.sha256}"
            )
    elif entity_type == "isms_org_node":
        row = db.query(IsmsOrgNode).filter(IsmsOrgNode.id == entity_id).one_or_none()
        if row:
            title = f"Organisation chart: {row.name or 'Node'}"
            subtitle = row.node_type or ""
            controls = isms_sample_controls(db, entity_type, row.id, framework)
    elif entity_type == "isms_asset":
        row = db.query(RiskAsset).filter(RiskAsset.id == entity_id).one_or_none()
        if row:
            title = f"ISMS asset: {row.name or 'Asset'}"
            subtitle = " / ".join(
                x
                for x in [
                    getattr(getattr(row, "category", None), "name", ""),
                    getattr(getattr(row, "subcategory", None), "name", ""),
                ]
                if x
            )
            controls = isms_sample_controls(db, entity_type, row.id, framework)
    elif entity_type == "isms_access_control_matrix":
        row = (
            db.query(IsmsAccessControlMatrixEntry)
            .filter(IsmsAccessControlMatrixEntry.id == entity_id)
            .one_or_none()
        )
        if row:
            title = f"Access control matrix: {short_text(row.task_action, 180)}"
            subtitle = row.status or ""
    elif entity_type == "isms_application_configuration":
        row = (
            db.query(IsmsApplicationConfigurationEntry)
            .filter(IsmsApplicationConfigurationEntry.id == entity_id)
            .one_or_none()
        )
        if row:
            process = getattr(getattr(row, "business_process", None), "name", "")
            source = (
                getattr(getattr(row, "document", None), "title", "")
                or getattr(getattr(row, "user", None), "username", "")
                or getattr(getattr(row, "asset", None), "name", "")
                or getattr(getattr(row, "org_node", None), "name", "")
                or row.source_type
            )
            title = f"Application configuration: {source}"
            subtitle = " · ".join(x for x in [process, row.value] if x)
    elif entity_type == "isms_effectiveness_measure":
        row = (
            db.query(IsmsEffectivenessMeasure)
            .filter(
                IsmsEffectivenessMeasure.id == entity_id,
                IsmsEffectivenessMeasure.framework_slug == framework,
            )
            .one_or_none()
        )
        if row:
            title = f"ISMS effectiveness measure: {short_text(row.summary, 180)}"
            subtitle = short_text(row.description, 220)
            controls = isms_sample_controls(db, entity_type, row.id, framework)
    elif entity_type == "isms_effectiveness_metric":
        row = (
            db.query(IsmsEffectivenessMetricEntry)
            .join(
                IsmsEffectivenessMeasure,
                IsmsEffectivenessMeasure.id == IsmsEffectivenessMetricEntry.measure_id,
            )
            .filter(
                IsmsEffectivenessMetricEntry.id == entity_id,
                IsmsEffectivenessMeasure.framework_slug == framework,
            )
            .one_or_none()
        )
        if row:
            measure = row.measure
            title = (
                f"ISMS metric: {short_text(measure.summary if measure else '', 160)}"
            )
            value_parts = []
            if row.metric_value is not None:
                value_parts.append(f"{row.metric_value:g}")
            if row.metric_unit:
                value_parts.append(row.metric_unit)
            if row.qualitative_value:
                value_parts.append(short_text(row.qualitative_value, 120))
            subtitle = (
                " ".join(value_parts).strip() or row.source_title or "Metric entry"
            )
            if measure:
                controls = isms_sample_controls(
                    db, "isms_effectiveness_measure", measure.id, framework
                )
    elif entity_type == "story":
        from app.services.stories import story_controls

        row = db.get(Story, entity_id)
        if row:
            title = f"Story: {short_text(row.title, 180)}"
            subtitle = short_text(row.summary, 220)
            controls = story_controls(db, row.id, framework)
    elif entity_type == "isms_meeting":
        row = db.query(IsmsMeeting).filter(IsmsMeeting.id == entity_id).one_or_none()
        if row:
            title = f"ISMS meeting: {row.title or 'Meeting'}"
            subtitle = row.date.isoformat() if row.date else ""
            controls = isms_sample_controls(db, entity_type, row.id, framework)

    if row is None:
        if required:
            raise DomainError(status_code=404, detail="Sampled entity not found")
        return {
            "type": entity_type,
            "id": str(entity_id),
            "kind": label,
            "title": label,
            "subtitle": "This sampled item no longer exists.",
            "href": entity_href(entity_type, entity_id, framework),
            "controls": [],
            "missing": True,
        }

    return {
        "type": entity_type,
        "id": str(entity_id),
        "kind": label,
        "title": title,
        "subtitle": subtitle,
        "href": entity_href(entity_type, entity_id, framework),
        "controls": controls,
        "missing": False,
    }
