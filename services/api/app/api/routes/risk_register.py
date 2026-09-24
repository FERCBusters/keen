"""A conventional risk register layered on KEEN's existing scenario records."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.routes.risks import (
    RiskUpsertPayload, _apply_payload, _clean_framework, _clean_risk_types,
    _replace_control_links, _risk_or_404, _risk_out, require_risk_manage, require_risk_read,
)
from app.db.models import Risk, RiskLibraryEntry, RiskRegisterSettings
from app.db.session import get_db
from app.services.entity_changelog import record_entity_changelog

router = APIRouter()


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


class LibraryInput(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    threat_summary: str = Field(default="", max_length=12000)
    risk_types: list[str] = Field(min_length=1)
    treatment_guidance: str = Field(default="", max_length=20000)


def _settings(db):
    row = db.get(RiskRegisterSettings, 1)
    return {"low_max": row.low_max, "moderate_max": row.moderate_max,
            "high_max": row.high_max} if row else {"low_max": 4, "moderate_max": 9, "high_max": 16}


def _score(likelihood, impact):
    return int(likelihood) * int(impact) if likelihood is not None and impact is not None else None


def _register_out(db, row, framework):
    item = _risk_out(db, row, framework=framework, include_controls=True)
    for field in ("register_likelihood", "register_impact", "register_residual_likelihood", "register_residual_impact",
                  "treatment_strategy", "treatment_status", "treatment_plan"):
        item[field] = getattr(row, field)
    item["treatment_due_at"] = row.treatment_due_at.isoformat() if row.treatment_due_at else None
    item["register_inherent_score"] = _score(row.register_likelihood, row.register_impact)
    item["register_residual_score"] = _score(row.register_residual_likelihood, row.register_residual_impact)
    return item


@router.get("/v1/risks/register")
def list_register(framework: str = "ISO27001:2022", limit: int = 200, offset: int = 0,
                  user=Depends(require_risk_read), db: Session = Depends(get_db)):
    fw = _clean_framework(framework)
    limit, offset = max(1, min(limit, 1000)), max(0, offset)
    rows = db.query(Risk).order_by(Risk.updated_at.desc(), Risk.id).offset(offset).limit(limit).all()
    return {"total": db.query(Risk.id).count(), "items": [_register_out(db, r, fw) for r in rows],
            "thresholds": _settings(db), "limit": limit, "offset": offset}


@router.patch("/v1/risks/register/settings")
def update_settings(payload: RegisterThresholds, user=Depends(require_risk_manage), db: Session = Depends(get_db)):
    if not (payload.low_max < payload.moderate_max < payload.high_max):
        raise HTTPException(400, "Thresholds must increase from low through high")
    row = db.get(RiskRegisterSettings, 1) or RiskRegisterSettings(id=1)
    row.low_max, row.moderate_max, row.high_max = payload.low_max, payload.moderate_max, payload.high_max
    db.add(row)
    db.commit()
    return _settings(db)


@router.patch("/v1/risks/{risk_id}/register")
def rate_risk(risk_id: uuid.UUID, payload: RegisterRating, user=Depends(require_risk_manage), db: Session = Depends(get_db)):
    row = _risk_or_404(db, str(risk_id))
    if payload.treatment_strategy not in {"", "mitigate", "accept", "avoid", "transfer"}:
        raise HTTPException(400, "Invalid treatment strategy")
    if payload.treatment_status not in {"open", "in_progress", "awaiting_review", "closed"}:
        raise HTTPException(400, "Invalid treatment status")
    before = _register_out(db, row, "ISO27001:2022")
    for field, value in payload.model_dump().items():
        setattr(row, field, value.strip() if isinstance(value, str) else value)
    db.add(row)
    db.flush()
    record_entity_changelog(db, entity_type="risk", entity_id=row.id, action="updated",
        before=before, after=_register_out(db, row, "ISO27001:2022"), user=user,
        request_method="PATCH", request_path=f"/v1/risks/{risk_id}/register")
    db.commit()
    return _register_out(db, row, "ISO27001:2022")


_CSV_COLUMNS = ["asset", "category", "subcategory", "threat_summary", "risk_types", "owner_username",
                "register_likelihood", "register_impact", "register_residual_likelihood", "register_residual_impact",
                "treatment_strategy", "treatment_status", "treatment_plan", "treatment_due_at", "controls"]


def _csv_safe(value: Any) -> Any:
    value = "" if value is None else str(value)
    return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


@router.get("/v1/risks/register/export.csv")
def export_register(framework: str = "ISO27001:2022", user=Depends(require_risk_read), db: Session = Depends(get_db)):
    fw = _clean_framework(framework)
    def generate():
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=_CSV_COLUMNS)
        writer.writeheader(); yield output.getvalue(); output.seek(0); output.truncate(0)
        for row in db.query(Risk).order_by(Risk.created_at, Risk.id).yield_per(100):
            item = _register_out(db, row, fw)
            values = {"asset": item["asset"], "category": item["category"]["name"],
                      "subcategory": item["subcategory"]["name"], "threat_summary": item["threat_summary"],
                      "risk_types": ";".join(item["risk_types"]),
                      "owner_username": item["risk_owner"]["username"] or "",
                      "controls": ";".join(c["ref"] for c in item["controls"] or [])}
            values.update({k: item.get(k) for k in _CSV_COLUMNS if k not in values})
            writer.writerow({k: _csv_safe(v) for k, v in values.items()})
            yield output.getvalue(); output.seek(0); output.truncate(0)
    return StreamingResponse(generate(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=keen-risk-register.csv"})


@router.post("/v1/risks/register/import.csv")
async def import_register(file: UploadFile = File(...), framework: str = "ISO27001:2022",
                          user=Depends(require_risk_manage), db: Session = Depends(get_db)):
    fw = _clean_framework(framework)
    raw = await file.read(2_000_001)
    if len(raw) > 2_000_000:
        raise HTTPException(413, "Risk CSV exceeds 2 MB")
    try:
        source = io.StringIO(raw.decode("utf-8-sig"), newline="")
        reader = csv.DictReader(source)
        if not reader.fieldnames or not {"asset", "category", "subcategory", "threat_summary", "risk_types"}.issubset(reader.fieldnames):
            raise ValueError("CSV requires asset, category, subcategory, threat_summary and risk_types columns")
        entries = list(reader)
        if len(entries) > 1000:
            raise ValueError("Import at most 1000 risks per file")
        if not entries:
            raise ValueError("CSV contains no risks")
        for lineno, entry in enumerate(entries, start=2):
            if None in entry:
                raise ValueError(f"Row {lineno}: extra columns")
            def rating(key):
                val = (entry.get(key) or "").strip()
                if not val: return None
                number = int(val)
                if number not in range(1, 6): raise ValueError(f"{key} must be 1–5")
                return number
            payload = RiskUpsertPayload(
                asset_name=entry["asset"], category_name=entry["category"],
                subcategory_name=entry["subcategory"], threat_summary=entry["threat_summary"],
                risk_types=[part.strip() for part in (entry["risk_types"] or "").split(";") if part.strip()],
                risk_owner_username=(entry.get("owner_username") or "").strip() or None,
                framework=fw, controls=[s.strip() for s in (entry.get("controls") or "").split(";") if s.strip()],
            )
            row = Risk(created_by_user_id=user.id)
            _apply_payload(db, row, payload, is_create=True)
            for field in ("register_likelihood", "register_impact", "register_residual_likelihood", "register_residual_impact"):
                setattr(row, field, rating(field))
            strategy = (entry.get("treatment_strategy") or "").strip()
            status = (entry.get("treatment_status") or "open").strip()
            if strategy not in {"", "mitigate", "accept", "avoid", "transfer"} or status not in {"open", "in_progress", "awaiting_review", "closed"}:
                raise ValueError("Invalid treatment strategy or status")
            row.treatment_strategy, row.treatment_status = strategy, status
            row.treatment_plan = (entry.get("treatment_plan") or "")[:20000]
            due = (entry.get("treatment_due_at") or "").strip()
            row.treatment_due_at = date.fromisoformat(due) if due else None
            db.add(row); db.flush()
            if payload.controls: _replace_control_links(db, row, framework=fw, control_values=payload.controls)
        db.commit()
    except (ValueError, HTTPException, TypeError, ValidationError) as exc:
        db.rollback()
        raise HTTPException(400, f"Import failed at row {locals().get('lineno', 'header')}: {getattr(exc, 'detail', str(exc))}") from exc
    return {"created": len(entries)}


def _library_out(row):
    return {"id": str(row.id), "name": row.name, "threat_summary": row.threat_summary,
            "risk_types": row.risk_types, "treatment_guidance": row.treatment_guidance}


@router.get("/v1/risks/library")
def list_library(user=Depends(require_risk_read), db: Session = Depends(get_db)):
    return {"items": [_library_out(row) for row in db.query(RiskLibraryEntry).order_by(func.lower(RiskLibraryEntry.name)).all()]}


@router.post("/v1/risks/library", status_code=201)
def add_library(payload: LibraryInput, user=Depends(require_risk_manage), db: Session = Depends(get_db)):
    name = payload.name.strip()
    if db.query(RiskLibraryEntry.id).filter(func.lower(RiskLibraryEntry.name) == name.lower()).first():
        raise HTTPException(409, "Library item already exists")
    row = RiskLibraryEntry(name=name, threat_summary=payload.threat_summary.strip(),
                           risk_types=_clean_risk_types(payload.risk_types),
                           treatment_guidance=payload.treatment_guidance.strip())
    db.add(row); db.commit(); db.refresh(row)
    return _library_out(row)


@router.delete("/v1/risks/library/{item_id}", status_code=204)
def remove_library(item_id: uuid.UUID, user=Depends(require_risk_manage), db: Session = Depends(get_db)):
    row = db.get(RiskLibraryEntry, item_id)
    if not row: raise HTTPException(404, "Library item not found")
    db.delete(row); db.commit()
