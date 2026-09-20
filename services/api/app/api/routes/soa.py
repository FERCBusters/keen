from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routes.clauses import _clause_evidence_mappings
from app.api.routes.isms import (
    _can_read_isms,
    _app_config_out,
    _asset_out,
    _business_process_out,
    _document_out,
    _effectiveness_measure_out,
    _entity_out,
    _meeting_out,
    _objective_out,
)
from app.api.utils import (
    CONTROL_JUSTIFICATIONS,
    control_justification as _control_justification,
    control_upstream_url as _control_upstream_url,
    ref_sort_key as _ref_sort_key,
)
from app.core.config import settings
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    FrameworkClause,
    InterestedParty,
    InterestedPartyCommunication,
    InterestedPartyControlLink,
    InterestedPartyName,
    InterestedPartyNature,
    IsmsApplicationConfigurationEntry,
    IsmsBusinessProcess,
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsMeeting,
    IsmsObjective,
    IsmsOrgNode,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    RiskControlLink,
    PestleBusinessProcess,
    PestleBusinessProcessRelevance,
    PestleClauseRelevance,
    PestleItem,
    PestleRelevanceLevel,
    User,
)
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.security.permissions import has_permission

router = APIRouter()

RISK_READ_PERMISSION = "risk.read"
RISK_MANAGE_PERMISSION = "risk.manage"
PESTLE_READ_PERMISSION = "pestle.read"
PESTLE_MANAGE_PERMISSION = "pestle.manage"
INTERESTED_PARTIES_READ_PERMISSION = "interested_parties.read"
INTERESTED_PARTIES_MANAGE_PERMISSION = "interested_parties.manage"


def _can_view_risks(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return has_permission(db, user, RISK_READ_PERMISSION) or has_permission(
        db, user, RISK_MANAGE_PERMISSION
    )


def _can_view_pestle(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return bool(
        has_permission(db, user, PESTLE_READ_PERMISSION)
        or has_permission(db, user, PESTLE_MANAGE_PERMISSION)
        or _can_view_risks(db, user)
    )


def _can_view_interested_parties(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return bool(
        has_permission(db, user, INTERESTED_PARTIES_READ_PERMISSION)
        or has_permission(db, user, INTERESTED_PARTIES_MANAGE_PERMISSION)
        or _can_view_risks(db, user)
    )


def _control_summary(control: ControlItem) -> dict[str, Any]:
    return {
        "id": str(control.id),
        "framework": control.framework_slug,
        "type": control.type,
        "ref": control.ref,
        "title": control.title,
        "in_scope": bool(control.in_scope),
        "justification": _control_justification(control),
        "upstream_url": _control_upstream_url(control),
    }


def _clause_summary(clause: FrameworkClause) -> dict[str, Any]:
    evidence_mappings = _clause_evidence_mappings(clause)
    return {
        "id": str(clause.id),
        "framework": clause.framework_slug,
        "ref": clause.ref,
        "title": clause.title,
        "parent_id": str(clause.parent_clause_id) if clause.parent_clause_id else None,
        "parent_ref": clause.parent.ref if getattr(clause, "parent", None) else None,
        "sort_order": int(clause.sort_order or 0),
        "upstream_url": _control_upstream_url(clause),
        "evidence_mappings": evidence_mappings,
        "evidence_urls": [item["url"] for item in evidence_mappings],
        "evidence_count": len(evidence_mappings),
    }


def _risk_controls(db: Session, risk_id, framework: str) -> list[dict[str, Any]]:
    rows = (
        db.query(ControlItem)
        .join(RiskControlLink, RiskControlLink.control_item_id == ControlItem.id)
        .filter(
            RiskControlLink.risk_id == risk_id,
            RiskControlLink.framework_slug == framework,
            ControlItem.framework_slug == framework,
        )
        .all()
    )
    rows.sort(key=lambda c: (c.type, _ref_sort_key(c.ref)))
    return [_control_summary(c) for c in rows]


def _relevance_summary(row: PestleRelevanceLevel | None) -> dict[str, Any]:
    return {
        "id": str(row.id) if row else None,
        "code": row.code if row else "na",
        "label": row.label if row else "N/A",
        "sort_order": int(row.sort_order or 0) if row else 0,
    }


def _business_process_summary(row: PestleBusinessProcess) -> dict[str, Any]:
    return {"id": str(row.id), "name": row.name}


def _pestle_summary(item: PestleItem) -> dict[str, Any]:
    business_links = sorted(
        [
            link
            for link in list(item.business_process_links or [])
            if getattr(getattr(link, "relevance", None), "code", None) != "na"
        ],
        key=lambda link: (
            (link.business_process.name or "").lower(),
            link.business_process.name or "",
        ),
    )
    clause_links = sorted(
        [
            link
            for link in list(item.clause_links or [])
            if getattr(getattr(link, "relevance", None), "code", None) != "na"
        ],
        key=lambda link: (
            int(link.clause.sort_order or 0),
            _ref_sort_key(link.clause.ref),
        ),
    )
    return {
        "id": str(item.id),
        "framework": item.framework_slug,
        "type": item.type,
        "lens": item.lens,
        "item": item.item or "",
        "overall_relevance": _relevance_summary(item.overall_relevance),
        "rationale": item.rationale or "",
        "business_processes": [
            {
                "business_process": _business_process_summary(link.business_process),
                "relevance": _relevance_summary(link.relevance),
            }
            for link in business_links
        ],
        "clauses": [
            {
                "clause": _clause_summary(link.clause),
                "relevance": _relevance_summary(link.relevance),
            }
            for link in clause_links
        ],
    }


def _interested_party_controls(
    db: Session, party_id, framework: str
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
        .all()
    )
    rows.sort(key=lambda c: (c.type, _ref_sort_key(c.ref)))
    return [_control_summary(c) for c in rows]


def _communication_summary(row: InterestedPartyCommunication) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "event": row.event,
        "when": row.when,
        "with_whom": row.with_whom,
        "methods": list(row.methods or []),
    }


def _interested_party_summary(
    db: Session, row: InterestedParty, framework: str
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "framework": row.framework_slug,
        "name": {
            "id": str(row.name.id) if row.name else None,
            "name": row.name.name if row.name else None,
        },
        "nature": {
            "id": str(row.nature.id) if row.nature else None,
            "name": row.nature.name if row.nature else None,
        },
        "note": row.note or "",
        "controls": _interested_party_controls(db, row.id, framework),
        "communications": [
            _communication_summary(comm) for comm in list(row.communications or [])
        ],
    }


def _risk_summary(db: Session, risk: Risk, framework: str) -> dict[str, Any]:
    asset = risk.asset
    category = asset.category if asset else None
    subcategory = asset.subcategory if asset else None
    return {
        "id": str(risk.id),
        "asset": asset.name if asset else "",
        "asset_id": str(asset.id) if asset else None,
        "category": {
            "id": str(category.id) if category else None,
            "name": category.name if category else None,
        },
        "subcategory": {
            "id": str(subcategory.id) if subcategory else None,
            "name": subcategory.name if subcategory else None,
        },
        "risk_types": list(risk.risk_types or []),
        "risk_owner": {
            "id": str(risk.owner.id) if risk.owner else None,
            "username": risk.owner.username if risk.owner else None,
        },
        "threat_summary": risk.threat_summary or "",
        "threat_score": int(risk.threat_score or 0),
        "vulnerability_score": int(risk.vulnerability_score or 0),
        "impact_score": int(risk.impact_score or 0),
        "risk_score": int(risk.risk_score or 0),
        "residual_vulnerability_score": int(risk.residual_vulnerability_score or 0),
        "residual_impact_score": int(risk.residual_impact_score or 0),
        "residual_risk_score": int(risk.residual_risk_score or 0),
        "controls": _risk_controls(db, risk.id, framework),
    }


@router.get("/v1/statement-of-applicability")
def statement_of_applicability(
    framework: str = settings.default_framework_slug,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    fw = (framework or settings.default_framework_slug or "").strip()

    controls = db.query(ControlItem).filter(ControlItem.framework_slug == fw).all()
    controls.sort(key=lambda c: (c.type, _ref_sort_key(c.ref)))

    clauses = (
        db.query(FrameworkClause).filter(FrameworkClause.framework_slug == fw).all()
    )
    clauses.sort(key=lambda c: (int(c.sort_order or 0), _ref_sort_key(c.ref)))

    clause_links = (
        db.query(ControlClauseLink)
        .join(ControlItem, ControlItem.id == ControlClauseLink.control_item_id)
        .join(FrameworkClause, FrameworkClause.id == ControlClauseLink.clause_id)
        .filter(ControlItem.framework_slug == fw, FrameworkClause.framework_slug == fw)
        .all()
    )

    control_clause_map: dict[str, dict[str, str]] = {}
    clause_control_map: dict[str, dict[str, str]] = {}
    for link in clause_links:
        control_id = str(link.control_item_id)
        clause_id = str(link.clause_id)
        control_clause_map.setdefault(control_id, {})[clause_id] = link.applicability
        clause_control_map.setdefault(clause_id, {})[control_id] = link.applicability

    control_rows = []
    for control in controls:
        row = _control_summary(control)
        row["clauses"] = control_clause_map.get(str(control.id), {})
        control_rows.append(row)

    clause_rows = []
    for clause in clauses:
        row = _clause_summary(clause)
        row["controls"] = clause_control_map.get(str(clause.id), {})
        clause_rows.append(row)

    can_view_risks = _can_view_risks(db, user)
    can_view_pestle = _can_view_pestle(db, user)
    can_view_interested_parties = _can_view_interested_parties(db, user)
    can_view_isms = _can_read_isms(db, user)
    risk_rows: list[dict[str, Any]] = []
    if can_view_risks:
        risks = (
            db.query(Risk)
            .join(RiskAsset, RiskAsset.id == Risk.asset_id)
            .join(RiskCategory, RiskCategory.id == RiskAsset.category_id)
            .join(
                RiskAssetSubcategory,
                RiskAssetSubcategory.id == RiskAsset.subcategory_id,
            )
            .order_by(
                Risk.risk_score.desc(),
                RiskAsset.name.asc(),
                Risk.threat_summary.asc(),
                Risk.updated_at.desc(),
            )
            .all()
        )
        risk_rows = [_risk_summary(db, risk, fw) for risk in risks]

    pestle_rows: list[dict[str, Any]] = []
    business_process_rows: list[dict[str, Any]] = []
    if can_view_pestle:
        pestle_items = (
            db.query(PestleItem)
            .filter(PestleItem.framework_slug == fw)
            .order_by(
                PestleItem.type.asc(),
                PestleItem.lens.asc(),
                PestleItem.updated_at.desc(),
            )
            .all()
        )
        pestle_rows = [_pestle_summary(item) for item in pestle_items]
        business_processes = (
            db.query(PestleBusinessProcess)
            .order_by(PestleBusinessProcess.name.asc())
            .all()
        )
        business_process_rows = [
            _business_process_summary(row) for row in business_processes
        ]

    interested_party_rows: list[dict[str, Any]] = []
    if can_view_interested_parties:
        interested_parties = (
            db.query(InterestedParty)
            .join(
                InterestedPartyName, InterestedPartyName.id == InterestedParty.name_id
            )
            .join(
                InterestedPartyNature,
                InterestedPartyNature.id == InterestedParty.nature_id,
            )
            .filter(InterestedParty.framework_slug == fw)
            .order_by(InterestedPartyNature.name.asc(), InterestedPartyName.name.asc())
            .all()
        )
        interested_party_rows = [
            _interested_party_summary(db, row, fw) for row in interested_parties
        ]

    isms_objectives: list[dict[str, Any]] = []
    isms_documents: list[dict[str, Any]] = []
    isms_org_nodes: list[dict[str, Any]] = []
    isms_assets: list[dict[str, Any]] = []
    isms_business_processes: list[dict[str, Any]] = []
    isms_application_configurations: list[dict[str, Any]] = []
    isms_effectiveness_measures: list[dict[str, Any]] = []
    isms_meetings: list[dict[str, Any]] = []
    if can_view_isms:
        isms_objectives = [
            _objective_out(db, row, fw)
            for row in db.query(IsmsObjective)
            .order_by(IsmsObjective.updated_at.desc())
            .limit(500)
            .all()
        ]
        isms_documents = [
            _document_out(db, row, fw)
            for row in db.query(IsmsDocument)
            .order_by(IsmsDocument.updated_at.desc())
            .limit(500)
            .all()
        ]
        isms_org_nodes = [
            _entity_out(db, "org_node", row, fw)
            for row in db.query(IsmsOrgNode)
            .order_by(IsmsOrgNode.sort_order.asc(), IsmsOrgNode.name.asc())
            .limit(1000)
            .all()
        ]
        isms_assets = [
            _asset_out(db, row, fw)
            for row in db.query(RiskAsset)
            .order_by(RiskAsset.name.asc())
            .limit(1000)
            .all()
        ]
        isms_business_processes = [
            _business_process_out(row)
            for row in db.query(IsmsBusinessProcess)
            .order_by(
                IsmsBusinessProcess.sort_order.asc(), IsmsBusinessProcess.name.asc()
            )
            .all()
        ]
        isms_application_configurations = [
            _app_config_out(db, row, fw)
            for row in db.query(IsmsApplicationConfigurationEntry)
            .order_by(IsmsApplicationConfigurationEntry.updated_at.desc())
            .limit(2000)
            .all()
        ]
        isms_effectiveness_measures = [
            _effectiveness_measure_out(db, row, fw, include_entries=False)
            for row in db.query(IsmsEffectivenessMeasure)
            .filter(IsmsEffectivenessMeasure.framework_slug == fw)
            .order_by(IsmsEffectivenessMeasure.updated_at.desc())
            .limit(1000)
            .all()
        ]
        isms_meetings = [
            _meeting_out(db, row, fw)
            for row in db.query(IsmsMeeting)
            .order_by(IsmsMeeting.date.desc(), IsmsMeeting.start_time.desc())
            .limit(500)
            .all()
        ]

    return {
        "framework": fw,
        "justification_options": list(CONTROL_JUSTIFICATIONS),
        "can_view_risks": can_view_risks,
        "can_view_pestle": can_view_pestle,
        "can_view_interested_parties": can_view_interested_parties,
        "can_view_isms": can_view_isms,
        "counts": {
            "controls": len(control_rows),
            "clauses": len(clause_rows),
            "risks": len(risk_rows),
            "pestle": len(pestle_rows),
            "business_processes": len(business_process_rows),
            "interested_parties": len(interested_party_rows),
            "interested_party_communications": sum(
                len(row.get("communications") or []) for row in interested_party_rows
            ),
            "isms_objectives": len(isms_objectives),
            "isms_documents": len(isms_documents),
            "isms_org_nodes": len(isms_org_nodes),
            "isms_assets": len(isms_assets),
            "isms_business_processes": len(isms_business_processes),
            "isms_application_configurations": len(isms_application_configurations),
            "isms_effectiveness_measures": len(isms_effectiveness_measures),
            "isms_meetings": len(isms_meetings),
        },
        "controls": control_rows,
        "clauses": clause_rows,
        "risks": risk_rows,
        "pestle_items": pestle_rows,
        "business_processes": business_process_rows,
        "interested_parties": interested_party_rows,
        "isms_objectives": isms_objectives,
        "isms_documents": isms_documents,
        "isms_org_nodes": isms_org_nodes,
        "isms_assets": isms_assets,
        "isms_business_processes": isms_business_processes,
        "isms_application_configurations": isms_application_configurations,
        "isms_effectiveness_measures": isms_effectiveness_measures,
        "isms_meetings": isms_meetings,
    }
