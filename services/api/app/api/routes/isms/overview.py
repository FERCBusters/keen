"""ISMS overview; see docs/maintainability-review.md for module boundaries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.utils import ref_sort_key as _ref_sort_key
from app.core.config import settings
from app.db.models import (
    ControlItem,
    FrameworkClause,
    IsmsAccessControlMatrixEntry,
    IsmsAwsAccount,
    IsmsBusinessProcess,
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    IsmsLicense,
    IsmsMeeting,
    IsmsObjective,
    IsmsOrgNode,
    RiskAsset,
    User,
)
from app.db.session import get_db

from .access import (
    _can_manage_isms,
    _can_read_isms,
    require_isms_read,
)
from .common import (
    _clean_framework,
    _clean_isms_section,
)
from .constants import (
    APP_SOURCE_TYPES,
    DOCUMENT_TYPES,
    OBJECTIVE_STATUSES,
)
from .metric_values import (
    _effectiveness_metric_sources_out,
)
from .serializers import (
    _access_control_matrix_out,
    _asset_categories_out,
    _asset_out,
    _aws_account_out,
    _clause_out,
    _control_out,
    _document_out,
    _effectiveness_measure_out,
    _entity_out,
    _license_out,
    _meeting_out,
    _objective_out,
    _org_node_out,
    _user_summary,
)

router = APIRouter()


@router.get("/v1/isms/meta")
def isms_meta(
    framework: str = settings.default_framework_slug,
    section: str | None = None,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    section_key = _clean_isms_section(section)
    include_all = section_key is None

    def wants(*keys: str) -> bool:
        return include_all or section_key in keys

    payload: dict[str, Any] = {
        "framework": fw,
        "section": section_key or "all",
        "can_view": _can_read_isms(db, user),
        "can_manage": _can_manage_isms(db, user),
        "document_types": sorted(DOCUMENT_TYPES),
        "objective_statuses": sorted(OBJECTIVE_STATUSES),
        "application_values": ["Low", "Medium", "High"],
        "application_source_types": sorted(APP_SOURCE_TYPES),
        "access_statuses": ["Pending Approval", "Approved"],
        "effectiveness_threshold_operators": ["", "lt", "lte", "eq", "gte", "gt"],
    }

    if wants(
        "objectives", "org", "assets", "access", "effectiveness", "app", "meetings"
    ):
        users = (
            db.query(User)
            .filter(User.is_active.is_(True))
            .order_by(User.username.asc())
            .all()
        )
        payload["users"] = [_user_summary(u) for u in users]

    if wants("org", "assets", "access", "app"):
        org_nodes = (
            db.query(IsmsOrgNode)
            .order_by(IsmsOrgNode.sort_order.asc(), IsmsOrgNode.name.asc())
            .all()
        )
        payload["org_nodes"] = [
            _org_node_out(n, include_people=False) for n in org_nodes
        ]

    if wants("app", "meetings"):
        docs = db.query(IsmsDocument).order_by(IsmsDocument.title.asc()).all()
        payload["documents"] = [
            _document_out(db, d, fw, include_links=False) for d in docs
        ]

    if wants("access", "app"):
        assets = db.query(RiskAsset).order_by(func.lower(RiskAsset.name).asc()).all()
        payload["assets"] = [_asset_out(db, a, fw, include_links=False) for a in assets]

    if wants("assets"):
        licenses = (
            db.query(IsmsLicense).order_by(func.lower(IsmsLicense.name).asc()).all()
        )
        payload["asset_categories"] = _asset_categories_out(db)
        payload["licenses"] = [
            _license_out(row, include_asset_count=True) for row in licenses
        ]

    if wants("access"):
        aws_accounts = (
            db.query(IsmsAwsAccount)
            .order_by(func.lower(IsmsAwsAccount.name).asc())
            .all()
        )
        payload["aws_accounts"] = [
            _aws_account_out(row, include_entry_count=True) for row in aws_accounts
        ]

    if wants("objectives", "documents", "org", "assets", "effectiveness", "meetings"):
        controls = (
            db.query(ControlItem)
            .filter(ControlItem.framework_slug == fw)
            .order_by(ControlItem.type.asc(), ControlItem.ref.asc())
            .all()
        )
        controls.sort(key=lambda c: (c.type, _ref_sort_key(c.ref)))
        payload["controls"] = [_control_out(c) for c in controls]

    if wants("objectives", "documents", "org", "assets", "meetings"):
        clauses = (
            db.query(FrameworkClause)
            .filter(FrameworkClause.framework_slug == fw)
            .order_by(FrameworkClause.sort_order.asc(), FrameworkClause.ref.asc())
            .all()
        )
        clauses.sort(key=lambda c: (int(c.sort_order or 0), _ref_sort_key(c.ref)))
        payload["clauses"] = [_clause_out(c) for c in clauses]

    if wants("effectiveness"):
        effectiveness_metric_sources = _effectiveness_metric_sources_out(db, user)
        payload["effectiveness_metric_sources"] = effectiveness_metric_sources
        payload["effectiveness_metric_source_types"] = [
            item["id"] for item in effectiveness_metric_sources
        ]

    return payload


@router.get("/v1/isms/summary")
def isms_summary(
    framework: str = settings.default_framework_slug,
    section: str | None = None,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    section_key = _clean_isms_section(section)
    include_all = section_key is None

    def wants(*keys: str) -> bool:
        return include_all or section_key in keys

    counts = {
        "objectives": int(db.query(IsmsObjective.id).count() or 0),
        "documents": int(db.query(IsmsDocument.id).count() or 0),
        "org_nodes": int(db.query(IsmsOrgNode.id).count() or 0),
        "assets": int(db.query(RiskAsset.id).count() or 0),
        "licenses": int(db.query(IsmsLicense.id).count() or 0),
        "aws_accounts": int(db.query(IsmsAwsAccount.id).count() or 0),
        "access_control_matrix": int(
            db.query(IsmsAccessControlMatrixEntry.id).count() or 0
        ),
        "effectiveness_measures": int(
            db.query(IsmsEffectivenessMeasure.id)
            .filter(IsmsEffectivenessMeasure.framework_slug == fw)
            .count()
            or 0
        ),
        "effectiveness_metric_entries": int(
            db.query(IsmsEffectivenessMetricEntry.id)
            .join(
                IsmsEffectivenessMeasure,
                IsmsEffectivenessMeasure.id == IsmsEffectivenessMetricEntry.measure_id,
            )
            .filter(IsmsEffectivenessMeasure.framework_slug == fw)
            .count()
            or 0
        ),
        "meetings": int(db.query(IsmsMeeting.id).count() or 0),
        "business_processes": int(db.query(IsmsBusinessProcess.id).count() or 0),
    }
    payload: dict[str, Any] = {
        "framework": fw,
        "section": section_key or "all",
        "can_manage": _can_manage_isms(db, user),
        "counts": counts,
    }

    if wants("objectives"):
        objectives = (
            db.query(IsmsObjective)
            .order_by(IsmsObjective.updated_at.desc())
            .limit(500)
            .all()
        )
        payload["objectives"] = [_objective_out(db, row, fw) for row in objectives]

    if wants("documents"):
        documents = (
            db.query(IsmsDocument)
            .order_by(IsmsDocument.updated_at.desc())
            .limit(500)
            .all()
        )
        payload["documents"] = [_document_out(db, row, fw) for row in documents]

    if wants("org"):
        org_nodes = (
            db.query(IsmsOrgNode)
            .order_by(IsmsOrgNode.sort_order.asc(), IsmsOrgNode.name.asc())
            .limit(1000)
            .all()
        )
        payload["org_nodes"] = [
            _entity_out(db, "org_node", row, fw) for row in org_nodes
        ]

    if wants("assets"):
        assets = (
            db.query(RiskAsset)
            .order_by(func.lower(RiskAsset.name).asc())
            .limit(1000)
            .all()
        )
        payload["assets"] = [_asset_out(db, row, fw) for row in assets]

    if wants("access"):
        access_control_matrix = (
            db.query(IsmsAccessControlMatrixEntry)
            .order_by(IsmsAccessControlMatrixEntry.updated_at.desc())
            .limit(2000)
            .all()
        )
        payload["access_control_matrix"] = [
            _access_control_matrix_out(db, row, fw) for row in access_control_matrix
        ]

    if wants("effectiveness"):
        effectiveness_measures = (
            db.query(IsmsEffectivenessMeasure)
            .filter(IsmsEffectivenessMeasure.framework_slug == fw)
            .order_by(IsmsEffectivenessMeasure.updated_at.desc())
            .limit(1000)
            .all()
        )
        payload["effectiveness_measures"] = [
            _effectiveness_measure_out(db, row, fw) for row in effectiveness_measures
        ]

    if wants("meetings"):
        meetings = (
            db.query(IsmsMeeting)
            .order_by(IsmsMeeting.date.desc(), IsmsMeeting.start_time.desc())
            .limit(500)
            .all()
        )
        payload["meetings"] = [_meeting_out(db, row, fw) for row in meetings]

    return payload
