"""Risk register application operations."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    ControlItem,
    Risk,
    RiskControlLink,
    RiskLibraryEntry,
    RiskRegisterSettings,
)
from app.security.csv import spreadsheet_cell
from app.services.entity_changelog import (
    record_entity_changelog,
    risk_changelog_state,
)
from app.services.errors import DomainError
from app.services.isms.link_diary import sync_entity_link_diary
from app.services.pestle import _clean_lens, _clean_type
from app.services.risks import (
    RiskUpsertPayload,
    _apply_payload,
    _clean_framework,
    _clean_risk_types,
    _replace_control_links,
    _risk_or_404,
    _risk_out,
)

_CSV_COLUMNS = [
    "asset",
    "category",
    "subcategory",
    "threat_summary",
    "risk_types",
    "owner_username",
    "owner_role_id",
    "register_likelihood",
    "register_impact",
    "register_residual_likelihood",
    "register_residual_impact",
    "treatment_strategy",
    "treatment_status",
    "treatment_plan",
    "treatment_due_at",
    "controls",
]


class RegisterRating(BaseModel):
    register_likelihood: int | None = Field(default=None, ge=1, le=5)
    register_impact: int | None = Field(default=None, ge=1, le=5)
    register_residual_likelihood: int | None = Field(default=None, ge=1, le=5)
    register_residual_impact: int | None = Field(default=None, ge=1, le=5)
    treatment_strategy: str = Field(default="")
    treatment_status: str = Field(default="open")
    treatment_plan: str = Field(default="", max_length=20000)
    treatment_due_at: date | None = None


class RegisterThresholds(BaseModel):
    low_max: int = Field(ge=1, le=24)
    moderate_max: int = Field(ge=2, le=24)
    high_max: int = Field(ge=3, le=24)


LIBRARY_METHODS = {"risk", "pestle"}


class LibraryInput(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    threat_summary: str = Field(default="", max_length=12000)
    # "risk" templates seed register entries; CIA associations are optional.
    # "pestle" templates seed PESTLE(E) items and carry a type and lens.
    method: str = Field(default="risk", max_length=16)
    pestle_type: str = Field(default="", max_length=32)
    lens: str = Field(default="", max_length=16)
    risk_types: list[str] = Field(default_factory=list)
    treatment_guidance: str = Field(default="", max_length=20000)
    suggested_assessment: dict[str, Any] = Field(default_factory=dict)
    source_risk_id: uuid.UUID | None = None


def _settings(db):
    row = db.get(RiskRegisterSettings, 1)
    return (
        {
            "low_max": row.low_max,
            "moderate_max": row.moderate_max,
            "high_max": row.high_max,
        }
        if row
        else {"low_max": 4, "moderate_max": 9, "high_max": 16}
    )


def _score(likelihood, impact):
    return (
        int(likelihood) * int(impact)
        if likelihood is not None and impact is not None
        else None
    )


def _register_out(db, row, framework):
    item = _risk_out(db, row, framework=framework, include_controls=True)
    for field in (
        "register_likelihood",
        "register_impact",
        "register_residual_likelihood",
        "register_residual_impact",
        "treatment_strategy",
        "treatment_status",
        "treatment_plan",
    ):
        item[field] = getattr(row, field)
    item["treatment_due_at"] = (
        row.treatment_due_at.isoformat() if row.treatment_due_at else None
    )
    item["register_inherent_score"] = _score(
        row.register_likelihood, row.register_impact
    )
    item["register_residual_score"] = _score(
        row.register_residual_likelihood, row.register_residual_impact
    )
    return item


def register_heatmap(*, user, db: Session):
    """Rated risks per likelihood and impact cell, with the severity thresholds
    that colour the cells."""

    result = {"thresholds": _settings(db)}
    for key, likelihood, impact in (
        ("inherent", Risk.register_likelihood, Risk.register_impact),
        ("residual", Risk.register_residual_likelihood, Risk.register_residual_impact),
    ):
        rows = (
            db.query(likelihood, impact, func.count(Risk.id))
            .filter(likelihood.isnot(None), impact.isnot(None))
            .group_by(likelihood, impact)
            .all()
        )
        result[key] = [
            {"likelihood": score_l, "impact": score_i, "count": count}
            for score_l, score_i, count in rows
        ]
    return result


def list_register(
    *,
    framework: str = "iso_27001_2022",
    limit: int = 200,
    offset: int = 0,
    user,
    db: Session,
):
    fw = _clean_framework(framework)
    limit, offset = max(1, min(limit, 1000)), max(0, offset)
    rows = (
        db.query(Risk)
        .order_by(Risk.updated_at.desc(), Risk.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": db.query(Risk.id).count(),
        "items": [_register_out(db, r, fw) for r in rows],
        "thresholds": _settings(db),
        "limit": limit,
        "offset": offset,
    }


def update_settings(*, payload: RegisterThresholds, user, db: Session):
    if not (payload.low_max < payload.moderate_max < payload.high_max):
        raise DomainError(400, "Thresholds must increase from low through high")
    row = db.get(RiskRegisterSettings, 1) or RiskRegisterSettings(id=1)
    row.low_max, row.moderate_max, row.high_max = (
        payload.low_max,
        payload.moderate_max,
        payload.high_max,
    )
    db.add(row)
    db.commit()
    return _settings(db)


def rate_risk(*, risk_id: uuid.UUID, payload: RegisterRating, user, db: Session):
    row = _risk_or_404(db, str(risk_id))
    if payload.treatment_strategy not in {
        "",
        "mitigate",
        "accept",
        "avoid",
        "transfer",
    }:
        raise DomainError(400, "Invalid treatment strategy")
    if payload.treatment_status not in {
        "open",
        "in_progress",
        "awaiting_review",
        "closed",
    }:
        raise DomainError(400, "Invalid treatment status")
    before = _register_out(db, row, "iso_27001_2022")
    for field, value in payload.model_dump().items():
        setattr(row, field, value.strip() if isinstance(value, str) else value)
    row.threat_score = 1
    row.vulnerability_score = row.register_likelihood or 1
    row.impact_score = row.register_impact or 1
    row.residual_vulnerability_score = row.register_residual_likelihood or 1
    row.residual_impact_score = row.register_residual_impact or 1
    row.risk_score = _score(row.register_likelihood, row.register_impact) or 0
    row.residual_risk_score = (
        _score(row.register_residual_likelihood, row.register_residual_impact) or 0
    )
    db.add(row)
    db.flush()
    record_entity_changelog(
        db,
        entity_type="risk",
        entity_id=row.id,
        action="updated",
        before=before,
        after=_register_out(db, row, "iso_27001_2022"),
        user=user,
        request_method="PATCH",
        request_path=f"/v1/risks/{risk_id}/register",
    )
    db.commit()
    return _register_out(db, row, "iso_27001_2022")


def _csv_safe(value: Any) -> Any:
    value = "" if value is None else str(value)
    return spreadsheet_cell(value)


def _library_out(row):
    return {
        "id": str(row.id),
        "name": row.name,
        "threat_summary": row.threat_summary,
        "method": row.method or "risk",
        "pestle_type": row.pestle_type or "",
        "lens": row.lens or "",
        "risk_types": row.risk_types or [],
        "treatment_guidance": row.treatment_guidance,
        "suggested_assessment": row.suggested_assessment or {},
    }


def list_library(*, method: str = "", user, db: Session):
    qry = db.query(RiskLibraryEntry)
    wanted = (method or "").strip().lower()
    if wanted:
        if wanted not in LIBRARY_METHODS:
            raise DomainError(400, "method must be risk or pestle")
        qry = qry.filter(RiskLibraryEntry.method == wanted)
    return {
        "items": [
            _library_out(row)
            for row in qry.order_by(func.lower(RiskLibraryEntry.name)).all()
        ]
    }


def add_library(*, payload: LibraryInput, user, db: Session):
    name = payload.name.strip()
    if (
        db.query(RiskLibraryEntry.id)
        .filter(func.lower(RiskLibraryEntry.name) == name.lower())
        .first()
    ):
        raise DomainError(409, "Library item already exists")
    suggestion = {
        key: value
        for key, value in payload.suggested_assessment.items()
        if key
        in {
            "asset",
            "inherent_likelihood",
            "inherent_impact",
            "residual_likelihood",
            "residual_impact",
        }
    }
    if payload.source_risk_id:
        if not db.get(Risk, payload.source_risk_id):
            raise DomainError(400, "Source risk not found")
        refs = (
            db.query(ControlItem.ref)
            .join(RiskControlLink, RiskControlLink.control_item_id == ControlItem.id)
            .filter(
                RiskControlLink.risk_id == payload.source_risk_id,
                RiskControlLink.framework_slug == "KEEN-AF:1.0",
            )
            .order_by(ControlItem.ref)
            .all()
        )
        if refs:
            suggestion["keen_af_control_refs"] = list(
                dict.fromkeys(ref for (ref,) in refs)
            )
    method = (payload.method or "risk").strip().lower()
    if method not in LIBRARY_METHODS:
        raise DomainError(400, "method must be risk or pestle")
    pestle_type = lens = ""
    if method == "pestle":
        pestle_type = _clean_type(payload.pestle_type)
        lens = _clean_lens(payload.lens)
    row = RiskLibraryEntry(
        name=name,
        threat_summary=payload.threat_summary.strip(),
        method=method,
        pestle_type=pestle_type,
        lens=lens,
        risk_types=_clean_risk_types(payload.risk_types) if method == "risk" else [],
        treatment_guidance=payload.treatment_guidance.strip(),
        suggested_assessment=suggestion,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _library_out(row)


def remove_library(*, item_id: uuid.UUID, user, db: Session):
    row = db.get(RiskLibraryEntry, item_id)
    if not row:
        raise DomainError(404, "Library item not found")
    db.delete(row)
    db.commit()


def import_register(
    *, raw: bytes, framework: str = "iso_27001_2022", user, db: Session
):
    fw = _clean_framework(framework)
    if len(raw) > 2_000_000:
        raise DomainError(413, "Risk CSV exceeds 2 MB")
    try:
        source = io.StringIO(raw.decode("utf-8-sig"), newline="")
        reader = csv.DictReader(source)
        if not reader.fieldnames or not {
            "asset",
            "category",
            "subcategory",
            "threat_summary",
            "risk_types",
        }.issubset(reader.fieldnames):
            raise ValueError(
                "CSV requires asset, category, subcategory, threat_summary and risk_types columns"
            )
        entries = list(reader)
        if len(entries) > 1000:
            raise ValueError("Import at most 1000 risks per file")
        if not entries:
            raise ValueError("CSV contains no risks")
        # The file is saved as one transaction, so every imported risk and its
        # creation record carry the same moment: the time of the import.
        imported_at = utc_now_naive()
        linked_risk_ids: list[uuid.UUID] = []
        for lineno, entry in enumerate(entries, start=2):
            if None in entry:
                raise ValueError(f"Row {lineno}: extra columns")

            def rating(key):
                val = (entry.get(key) or "").strip()
                if not val:
                    return None
                number = int(val)
                if number not in range(1, 6):
                    raise ValueError(f"{key} must be 1-5")
                return number

            payload = RiskUpsertPayload(
                asset_name=entry["asset"],
                category_name=entry["category"],
                subcategory_name=entry["subcategory"],
                threat_summary=entry["threat_summary"],
                risk_types=[
                    part.strip()
                    for part in (entry["risk_types"] or "").split(";")
                    if part.strip()
                ],
                risk_owner_username=(entry.get("owner_username") or "").strip() or None,
                risk_owner_role_id=(entry.get("owner_role_id") or "").strip() or None,
                framework=fw,
                controls=[
                    s.strip()
                    for s in (entry.get("controls") or "").split(";")
                    if s.strip()
                ],
            )
            row = Risk(created_by_user_id=user.id, created_at=imported_at)
            _apply_payload(db, row, payload, is_create=True)
            row.updated_at = imported_at
            for field in (
                "register_likelihood",
                "register_impact",
                "register_residual_likelihood",
                "register_residual_impact",
            ):
                setattr(row, field, rating(field))
            row.vulnerability_score, row.impact_score = (
                row.register_likelihood or 1,
                row.register_impact or 1,
            )
            row.residual_vulnerability_score, row.residual_impact_score = (
                row.register_residual_likelihood or 1,
                row.register_residual_impact or 1,
            )
            row.risk_score = _score(row.register_likelihood, row.register_impact) or 0
            row.residual_risk_score = (
                _score(row.register_residual_likelihood, row.register_residual_impact)
                or 0
            )
            strategy = (entry.get("treatment_strategy") or "").strip()
            status = (entry.get("treatment_status") or "open").strip()
            if strategy not in {
                "",
                "mitigate",
                "accept",
                "avoid",
                "transfer",
            } or status not in {"open", "in_progress", "awaiting_review", "closed"}:
                raise ValueError("Invalid treatment strategy or status")
            row.treatment_strategy, row.treatment_status = strategy, status
            row.treatment_plan = (entry.get("treatment_plan") or "")[:20000]
            due = (entry.get("treatment_due_at") or "").strip()
            row.treatment_due_at = date.fromisoformat(due) if due else None
            db.add(row)
            db.flush()
            if payload.controls:
                _replace_control_links(
                    db, row, framework=fw, control_values=payload.controls
                )
                linked_risk_ids.append(row.id)
            db.flush()  # the recorded state reads the new control links
            record_entity_changelog(
                db,
                entity_type="risk",
                entity_id=row.id,
                action="created",
                before=None,
                after=risk_changelog_state(db, row, framework=fw),
                user=user,
                request_method="POST",
                request_path="/v1/risks/register/import.csv",
                changed_at=imported_at,
            )
    except (ValueError, DomainError, TypeError, ValidationError) as exc:
        db.rollback()
        raise DomainError(
            400,
            f"Import failed at row {locals().get('lineno', 'header')}: {getattr(exc, 'detail', str(exc))}",
        ) from exc
    # Diary entries store artifacts, so they are written only once every row
    # has been accepted; a rejected file leaves nothing in artifact storage.
    for risk_id in linked_risk_ids:
        sync_entity_link_diary(db, "risk", risk_id, user)
    db.commit()
    return {"created": len(entries)}


def export_register(*, framework: str = "iso_27001_2022", user, db: Session):
    fw = _clean_framework(framework)

    def generate():
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        for row in db.query(Risk).order_by(Risk.created_at, Risk.id).yield_per(100):
            item = _register_out(db, row, fw)
            values = {
                "asset": item["asset"],
                "category": item["category"]["name"],
                "subcategory": item["subcategory"]["name"],
                "threat_summary": item["threat_summary"],
                "risk_types": ";".join(item["risk_types"]),
                "owner_username": row.owner.username if row.owner else "",
                "owner_role_id": str(row.risk_owner_role_id)
                if row.risk_owner_role_id
                else "",
                "controls": ";".join(c["ref"] for c in item["controls"] or []),
            }
            values.update({k: item.get(k) for k in _CSV_COLUMNS if k not in values})
            writer.writerow({k: _csv_safe(v) for k, v in values.items()})
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    return generate()
