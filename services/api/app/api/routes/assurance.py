"""Organisation assurance registers. People do not require application accounts."""
from __future__ import annotations

import uuid
from datetime import date
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.api.routes.isms import require_isms_manage, require_isms_read
from app.db.models import (
    IsmsOrgNode, IsmsPerson, IsmsPersonAsset, IsmsPersonAssurance,
    IsmsVendor, RiskAsset, User,
)
from app.db.session import get_db

router = APIRouter()


class PersonInput(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    email: str = Field(default="", max_length=256)
    position: str = Field(default="", max_length=256)
    notes: str = Field(default="", max_length=12000)
    user_id: uuid.UUID | None = None
    org_node_id: uuid.UUID | None = None
    asset_ids: list[uuid.UUID] = Field(default_factory=list)


class VendorInput(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=12000)
    website: str = Field(default="", max_length=2048)
    contact: str = Field(default="", max_length=256)
    asset_ids: list[uuid.UUID] = Field(default_factory=list)


class AssuranceInput(BaseModel):
    category: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    status: str = Field(default="pending")
    source_system: str = Field(default="", max_length=128)
    evidence_url: str = Field(default="", max_length=2048)
    completed_at: date | None = None
    expires_at: date | None = None
    notes: str = Field(default="", max_length=12000)


def _row(db, cls, identifier):
    row = db.get(cls, identifier)
    if row is None:
        raise HTTPException(404, "Record not found")
    return row


def _asset_ids(db: Session, raw: list[uuid.UUID]) -> list[uuid.UUID]:
    ids = list(dict.fromkeys(raw))
    if len(ids) > 200:
        raise HTTPException(400, "Too many linked assets")
    if ids and db.query(RiskAsset.id).filter(RiskAsset.id.in_(ids)).count() != len(ids):
        raise HTTPException(400, "Unknown asset")
    return ids


def _person_out(row: IsmsPerson) -> dict:
    return {
        "id": str(row.id), "name": row.name, "email": row.email,
        "position": row.position, "notes": row.notes,
        "user_id": str(row.user_id) if row.user_id else None,
        "username": row.user.username if row.user else None,
        "org_node_id": str(row.org_node_id) if row.org_node_id else None,
        "org_node": row.org_node.name if row.org_node else None,
        "asset_ids": [str(link.asset_id) for link in row.assets],
        "assurances": [_assurance_out(a) for a in sorted(row.assurances, key=lambda x: (x.category, x.name))],
    }


def _vendor_out(row: IsmsVendor) -> dict:
    return {
        "id": str(row.id), "name": row.name, "description": row.description,
        "website": row.website, "contact": row.contact,
        "asset_ids": [str(asset.id) for asset in row.assets],
    }


def _assurance_out(row: IsmsPersonAssurance) -> dict:
    return {key: str(getattr(row, key)) if getattr(row, key) is not None else None
            for key in ("id", "person_id", "completed_at", "expires_at")} | {
        key: getattr(row, key) for key in
        ("category", "name", "status", "source_system", "evidence_url", "notes")
    }


@router.get("/v1/isms/assurance/meta")
def assurance_meta(user=Depends(require_isms_read), db: Session = Depends(get_db)):
    return {
        "users": [{"id": str(u.id), "name": u.username} for u in db.query(User).filter(User.is_active.is_(True)).order_by(User.username)],
        "org_nodes": [{"id": str(o.id), "name": o.name} for o in db.query(IsmsOrgNode).order_by(IsmsOrgNode.name)],
        "assets": [{"id": str(a.id), "name": a.name} for a in db.query(RiskAsset).order_by(RiskAsset.name)],
    }


@router.get("/v1/isms/people")
def list_people(user=Depends(require_isms_read), db: Session = Depends(get_db)):
    rows = db.query(IsmsPerson).options(
        selectinload(IsmsPerson.user), selectinload(IsmsPerson.org_node),
        selectinload(IsmsPerson.assets), selectinload(IsmsPerson.assurances),
    ).order_by(func.lower(IsmsPerson.name)).all()
    return {"items": [_person_out(row) for row in rows]}


def _save_person(db: Session, row: IsmsPerson, payload: PersonInput):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    if payload.user_id:
        _row(db, User, payload.user_id)
        other = db.query(IsmsPerson).filter(IsmsPerson.user_id == payload.user_id, IsmsPerson.id != row.id).first()
        if other:
            raise HTTPException(409, "This KEEN user is already linked to another person")
    if payload.org_node_id:
        _row(db, IsmsOrgNode, payload.org_node_id)
    ids = _asset_ids(db, payload.asset_ids)
    row.name, row.email, row.position, row.notes = name, payload.email.strip(), payload.position.strip(), payload.notes.strip()
    row.user_id, row.org_node_id = payload.user_id, payload.org_node_id
    db.add(row)
    db.flush()
    current = {link.asset_id: link for link in row.assets}
    for asset_id, link in current.items():
        if asset_id not in ids:
            row.assets.remove(link)
    for asset_id in ids:
        if asset_id not in current:
            row.assets.append(IsmsPersonAsset(asset_id=asset_id))
    db.commit()
    return db.query(IsmsPerson).options(
        selectinload(IsmsPerson.user), selectinload(IsmsPerson.org_node),
        selectinload(IsmsPerson.assets), selectinload(IsmsPerson.assurances),
    ).filter(IsmsPerson.id == row.id).one()


@router.post("/v1/isms/people", status_code=201)
def create_person(payload: PersonInput, user=Depends(require_isms_manage), db: Session = Depends(get_db)):
    return _person_out(_save_person(db, IsmsPerson(), payload))


@router.patch("/v1/isms/people/{person_id}")
def update_person(person_id: uuid.UUID, payload: PersonInput, user=Depends(require_isms_manage), db: Session = Depends(get_db)):
    return _person_out(_save_person(db, _row(db, IsmsPerson, person_id), payload))


@router.delete("/v1/isms/people/{person_id}", status_code=204)
def delete_person(person_id: uuid.UUID, user=Depends(require_isms_manage), db: Session = Depends(get_db)):
    db.delete(_row(db, IsmsPerson, person_id))
    db.commit()


@router.get("/v1/isms/vendors")
def list_vendors(user=Depends(require_isms_read), db: Session = Depends(get_db)):
    rows = db.query(IsmsVendor).options(selectinload(IsmsVendor.assets)).order_by(func.lower(IsmsVendor.name)).all()
    return {"items": [_vendor_out(row) for row in rows]}


def _save_vendor(db: Session, row: IsmsVendor, payload: VendorInput):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    other = db.query(IsmsVendor).filter(func.lower(IsmsVendor.name) == name.lower(), IsmsVendor.id != row.id).first()
    if other:
        raise HTTPException(409, "Vendor already exists")
    website = payload.website.strip()
    if website and urlparse(website).scheme not in {"https", "http"}:
        raise HTTPException(400, "Website must use http or https")
    ids = _asset_ids(db, payload.asset_ids)
    row.name, row.description, row.website, row.contact = name, payload.description.strip(), website, payload.contact.strip()
    db.add(row)
    db.flush()
    # Only change the links explicitly selected for this vendor. Other vendors
    # retain their assets, while changing ownership of an asset is intentional.
    for asset in db.query(RiskAsset).filter(RiskAsset.vendor_id == row.id).all():
        if asset.id not in ids:
            asset.vendor_id = None
    for asset in db.query(RiskAsset).filter(RiskAsset.id.in_(ids)).all():
        asset.vendor_id = row.id
    db.commit()
    return db.query(IsmsVendor).options(selectinload(IsmsVendor.assets)).filter(IsmsVendor.id == row.id).one()


@router.post("/v1/isms/vendors", status_code=201)
def create_vendor(payload: VendorInput, user=Depends(require_isms_manage), db: Session = Depends(get_db)):
    return _vendor_out(_save_vendor(db, IsmsVendor(), payload))


@router.patch("/v1/isms/vendors/{vendor_id}")
def update_vendor(vendor_id: uuid.UUID, payload: VendorInput, user=Depends(require_isms_manage), db: Session = Depends(get_db)):
    return _vendor_out(_save_vendor(db, _row(db, IsmsVendor, vendor_id), payload))


@router.delete("/v1/isms/vendors/{vendor_id}", status_code=204)
def delete_vendor(vendor_id: uuid.UUID, user=Depends(require_isms_manage), db: Session = Depends(get_db)):
    db.delete(_row(db, IsmsVendor, vendor_id))
    db.commit()


@router.post("/v1/isms/people/{person_id}/assurances", status_code=201)
def add_assurance(person_id: uuid.UUID, payload: AssuranceInput, user=Depends(require_isms_manage), db: Session = Depends(get_db)):
    _row(db, IsmsPerson, person_id)
    row = IsmsPersonAssurance(person_id=person_id)
    return _save_assurance(db, row, payload)


def _save_assurance(db: Session, row: IsmsPersonAssurance, payload: AssuranceInput):
    if payload.status not in {"pending", "met", "failed", "expired", "not_applicable"}:
        raise HTTPException(400, "Invalid assurance status")
    if not payload.name.strip() or not payload.category.strip():
        raise HTTPException(400, "Category and name are required")
    url = payload.evidence_url.strip()
    if url and urlparse(url).scheme not in {"http", "https"} and not url.startswith("/api/v1/artifacts/"):
        raise HTTPException(400, "Evidence link must be an http(s) URL or an artifact path")
    if payload.completed_at and payload.expires_at and payload.expires_at < payload.completed_at:
        raise HTTPException(400, "Expiry precedes completion")
    for field in ("category", "name", "source_system", "notes"):
        setattr(row, field, getattr(payload, field).strip())
    row.status, row.evidence_url = payload.status, url
    row.completed_at, row.expires_at = payload.completed_at, payload.expires_at
    db.add(row)
    db.commit()
    db.refresh(row)
    return _assurance_out(row)


@router.patch("/v1/isms/people/{person_id}/assurances/{assurance_id}")
def update_assurance(person_id: uuid.UUID, assurance_id: uuid.UUID, payload: AssuranceInput, user=Depends(require_isms_manage), db: Session = Depends(get_db)):
    row = _row(db, IsmsPersonAssurance, assurance_id)
    if row.person_id != person_id:
        raise HTTPException(404, "Assurance record not found")
    return _save_assurance(db, row, payload)


@router.delete("/v1/isms/people/{person_id}/assurances/{assurance_id}", status_code=204)
def delete_assurance(person_id: uuid.UUID, assurance_id: uuid.UUID, user=Depends(require_isms_manage), db: Session = Depends(get_db)):
    row = _row(db, IsmsPersonAssurance, assurance_id)
    if row.person_id != person_id:
        raise HTTPException(404, "Assurance record not found")
    db.delete(row)
    db.commit()
