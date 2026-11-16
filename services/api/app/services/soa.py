"""Soa application operations."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.references import CONTROL_JUSTIFICATIONS
from app.core.references import ref_sort_key as _ref_sort_key
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    FrameworkClause,
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
    IsmsMeeting,
    IsmsObjective,
    IsmsOrgNode,
    IsmsSwot,
    PestleItem,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
)
from app.services.affected import affected_list
from app.services.clauses import clause_with_evidence
from app.services.control_refs import clause_summary, control_summary
from app.services.features import state
from app.services.isms.access import can_read_isms
from app.services.isms.serializers import (
    _asset_out,
    _attach_links,
    _document_out,
    _effectiveness_measure_out,
    _entity_out,
    _meeting_out,
    _objective_out,
)
from app.services.isms.swot import summary as swot_summary
from app.services.pestle import can_read_pestle, relevance_summary
from app.services.risks import (
    _risk_clauses,
    _risk_controls,
    can_read_risks,
    risk_owner_out,
)


def _pestle_summary(item: PestleItem) -> dict[str, Any]:
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
        "overall_relevance": relevance_summary(item.overall_relevance),
        "rationale": item.rationale or "",
        "clauses": [
            {
                "clause": clause_with_evidence(link.clause),
                "relevance": relevance_summary(link.relevance),
            }
            for link in clause_links
        ],
        "controls": [
            {
                "control": control_summary(link.control),
                "relevance": relevance_summary(link.relevance),
            }
            for link in sorted(
                (link for link in item.control_links if link.relevance.code != "na"),
                key=lambda link: (link.control.type, _ref_sort_key(link.control.ref)),
            )
        ],
        "affected": affected_list(list(item.affected_links)),
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
        "risk_owner": risk_owner_out(risk),
        "threat_summary": risk.threat_summary or "",
        "risk_score": int(risk.risk_score or 0),
        "residual_risk_score": int(risk.residual_risk_score or 0),
        "controls": [
            control_summary(c) for c in _risk_controls(db, risk.id, framework)
        ],
        "clauses": [clause_summary(c) for c in _risk_clauses(db, risk.id, framework)],
    }


def statement_of_applicability(
    *, framework: str = settings.default_framework_slug, user, db: Session
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
        row = control_summary(control)
        row["clauses"] = control_clause_map.get(str(control.id), {})
        control_rows.append(row)

    clause_rows = []
    for clause in clauses:
        row = clause_with_evidence(clause)
        row["controls"] = clause_control_map.get(str(clause.id), {})
        clause_rows.append(row)

    can_view_risks = can_read_risks(db, user)
    can_view_pestle = can_read_pestle(db, user)
    can_view_isms = can_read_isms(db, user)
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

    can_view_swot = can_view_isms and state(db).enabled("swot")
    swot_rows = []
    if can_view_swot:
        mapped = []
        for model in (IsmsEntityControlLink, IsmsEntityClauseLink):
            mapped.append(
                IsmsSwot.id.in_(
                    db.query(model.entity_id).filter(
                        model.entity_type == "swot", model.framework_slug == fw
                    )
                )
            )
        rows = (
            db.query(IsmsSwot)
            .filter(or_(*mapped))
            .order_by(IsmsSwot.significance.desc(), IsmsSwot.title, IsmsSwot.id)
            .limit(500)
            .all()
        )
        swot_rows = [
            _attach_links(db, swot_summary(row), "swot", row.id, fw, True)
            for row in rows
        ]

    isms_objectives: list[dict[str, Any]] = []
    isms_documents: list[dict[str, Any]] = []
    isms_org_nodes: list[dict[str, Any]] = []
    isms_assets: list[dict[str, Any]] = []
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
        "can_view_isms": can_view_isms,
        "can_view_swot": can_view_swot,
        "swot_items": swot_rows,
        "counts": {
            "swot": len(swot_rows),
            "controls": len(control_rows),
            "clauses": len(clause_rows),
            "risks": len(risk_rows),
            "pestle": len(pestle_rows),
            "isms_objectives": len(isms_objectives),
            "isms_documents": len(isms_documents),
            "isms_org_nodes": len(isms_org_nodes),
            "isms_assets": len(isms_assets),
            "isms_effectiveness_measures": len(isms_effectiveness_measures),
            "isms_meetings": len(isms_meetings),
        },
        "controls": control_rows,
        "clauses": clause_rows,
        "risks": risk_rows,
        "pestle_items": pestle_rows,
        "isms_objectives": isms_objectives,
        "isms_documents": isms_documents,
        "isms_org_nodes": isms_org_nodes,
        "isms_assets": isms_assets,
        "isms_effectiveness_measures": isms_effectiveness_measures,
        "isms_meetings": isms_meetings,
    }
