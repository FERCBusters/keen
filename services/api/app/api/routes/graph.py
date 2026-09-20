from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.utils import control_upstream_url as _control_upstream_url
from app.api.utils import try_uuid as _try_uuid
from app.core.cache import cached_json, user_cache_scope
from app.core.config import settings
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    Event,
    FrameworkClause,
    Mapping,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    RiskControlLink,
    User,
)
from app.db.session import get_db
from app.security.diary_visibility import diary_filter_condition
from app.security.permissions import has_permission
from app.core.source_meta import get_source_meta, apply_user_source_overrides

router = APIRouter()


def _event_filters(start_date: Optional[date], end_date: Optional[date]):
    filters = []
    if start_date:
        filters.append(
            Event.timestamp >= datetime.combine(start_date, datetime.min.time())
        )
    if end_date:
        filters.append(
            Event.timestamp
            < datetime.combine(end_date + timedelta(days=1), datetime.min.time())
        )
    return filters


def _source_node(src: str, total: int, request: Request) -> dict[str, Any]:
    default_meta = get_source_meta(src)
    user = getattr(request.state, "user", None)
    overrides = (
        getattr(user, "pref_source_colors", None) if isinstance(user, User) else None
    )
    meta = apply_user_source_overrides(default_meta, overrides)
    return {
        "id": f"source:{src}",
        "type": "source",
        "node_kind": "source",
        "label": meta.label,
        "source": src,
        "color": meta.color,
        "mapped_events": int(total or 0),
    }


def _clause_color(ref: str | None) -> str:
    """Deterministic but reasonably strong colour for clause nodes."""
    # Bright fixed palette so the clause/control bipartite graph has the same
    # visual energy as the source/control graph.
    palette = [
        "#2563eb",
        "#16a34a",
        "#ea580c",
        "#dc2626",
        "#9333ea",
        "#0891b2",
        "#ca8a04",
        "#db2777",
        "#4f46e5",
        "#059669",
        "#f97316",
        "#0ea5e9",
    ]
    s = str(ref or "")
    h = 0
    for ch in s:
        h = (h * 33 + ord(ch)) & 0xFFFFFFFF
    return palette[h % len(palette)]


@router.get("/v1/graph/control_source")
def graph_control_source(
    request: Request,
    framework: str = settings.default_framework_slug,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    source: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Graph for visualisation: Sources -> Controls.

    Counts are **distinct events**, not number of mappings. One event can map to
    multiple controls, so counting mappings would inflate totals.
    """

    user = getattr(request.state, "user", None)
    cache_parts = {
        "framework": framework,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "source": source,
        "user": user_cache_scope(user),
    }

    def _load():
        event_filters = _event_filters(start_date, end_date)
        if source:
            event_filters.append(Event.source == source)

        edge_rows = (
            db.query(
                Event.source,
                ControlItem.id,
                ControlItem.ref,
                ControlItem.title,
                func.count(func.distinct(Event.id)).label("n"),
            )
            .join(Mapping, Mapping.event_id == Event.id)
            .join(ControlItem, ControlItem.id == Mapping.control_item_id)
            .filter(ControlItem.framework_slug == framework)
            .filter(diary_filter_condition(db, user))
            .filter(*event_filters)
            .group_by(Event.source, ControlItem.id, ControlItem.ref, ControlItem.title)
            .all()
        )

        src_totals_rows = (
            db.query(Event.source, func.count(func.distinct(Event.id)).label("n"))
            .join(Mapping, Mapping.event_id == Event.id)
            .join(ControlItem, ControlItem.id == Mapping.control_item_id)
            .filter(ControlItem.framework_slug == framework)
            .filter(diary_filter_condition(db, user))
            .filter(*event_filters)
            .group_by(Event.source)
            .all()
        )
        src_totals = {f"source:{src}": int(n or 0) for src, n in src_totals_rows}

        # A single event has exactly one source, so summing the source/control
        # edge counts per control gives exact distinct-event control totals
        # without running the same aggregate join again.
        ctrl_totals: dict[str, int] = {}
        for _src, cid, _cref, _ctitle, n in edge_rows:
            key = f"control:{cid}"
            ctrl_totals[key] = ctrl_totals.get(key, 0) + int(n or 0)

        nodes: dict[str, dict[str, Any]] = {}
        links: list[dict[str, Any]] = []
        ctrl_ids: set = set()

        for src, cid, cref, ctitle, n in edge_rows:
            src_id = f"source:{src}"
            ctrl_id = f"control:{cid}"
            ctrl_ids.add(cid)

            if src_id not in nodes:
                nodes[src_id] = _source_node(src, src_totals.get(src_id, 0), request)
            if ctrl_id not in nodes:
                nodes[ctrl_id] = {
                    "id": ctrl_id,
                    "type": "control",
                    "node_kind": "control",
                    "label": cref,
                    "control_id": str(cid),
                    "ref": cref,
                    "title": ctitle,
                    "mapped_events": ctrl_totals.get(ctrl_id, 0),
                    "upstream_url": None,
                }

            links.append(
                {
                    "source": src_id,
                    "target": ctrl_id,
                    "value": int(n or 0),
                    "link_kind": "events",
                }
            )

        if ctrl_ids:
            ctrl_objs = (
                db.query(ControlItem).filter(ControlItem.id.in_(list(ctrl_ids))).all()
            )
            ctrl_map = {c.id: c for c in ctrl_objs}
            for node in nodes.values():
                if node.get("node_kind") == "control":
                    cid = _try_uuid(node.get("control_id"))
                    if cid and cid in ctrl_map:
                        node["upstream_url"] = _control_upstream_url(ctrl_map[cid])

        return {
            "nodes": list(nodes.values()),
            "links": links,
            "graph_kind": "source_control_events",
        }

    return cached_json("graph:control_source", cache_parts, _load)


@router.get("/v1/graph/clause_source")
def graph_clause_source(
    request: Request,
    framework: str = settings.default_framework_slug,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Graph for visualisation: Sources -> Clauses by mapped-event evidence.

    This is materially more expensive than the source -> control graph because it
    has to fan mapped controls out through clause applicability links. Cache it
    with the same short-lived aggregate cache used by other UI count endpoints,
    while keeping the cache scoped to the current user visibility preferences.
    """

    user = getattr(request.state, "user", None)
    cache_parts = {
        "framework": framework,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "user": user_cache_scope(user),
    }

    def _load():
        event_filters = _event_filters(start_date, end_date)

        edge_rows = (
            db.query(
                Event.source,
                FrameworkClause.id,
                FrameworkClause.ref,
                FrameworkClause.title,
                func.count(func.distinct(Event.id)).label("n"),
            )
            .join(Mapping, Mapping.event_id == Event.id)
            .join(ControlItem, ControlItem.id == Mapping.control_item_id)
            .join(
                ControlClauseLink, ControlClauseLink.control_item_id == ControlItem.id
            )
            .join(FrameworkClause, FrameworkClause.id == ControlClauseLink.clause_id)
            .filter(ControlItem.framework_slug == framework)
            .filter(FrameworkClause.framework_slug == framework)
            .filter(diary_filter_condition(db, user))
            .filter(*event_filters)
            .group_by(
                Event.source,
                FrameworkClause.id,
                FrameworkClause.ref,
                FrameworkClause.title,
            )
            .all()
        )

        src_totals_rows = (
            db.query(Event.source, func.count(func.distinct(Event.id)).label("n"))
            .join(Mapping, Mapping.event_id == Event.id)
            .join(ControlItem, ControlItem.id == Mapping.control_item_id)
            .join(
                ControlClauseLink, ControlClauseLink.control_item_id == ControlItem.id
            )
            .join(FrameworkClause, FrameworkClause.id == ControlClauseLink.clause_id)
            .filter(ControlItem.framework_slug == framework)
            .filter(FrameworkClause.framework_slug == framework)
            .filter(diary_filter_condition(db, user))
            .filter(*event_filters)
            .group_by(Event.source)
            .all()
        )
        src_totals = {f"source:{src}": int(n or 0) for src, n in src_totals_rows}

        # A single event has exactly one source, so summing the source/clause
        # edge counts per clause gives the same distinct-event clause totals
        # without running the heavy Event -> Mapping -> Control -> Clause join
        # a third time.
        clause_totals: dict[str, int] = {}
        for _src, clause_id, _cref, _ctitle, n in edge_rows:
            key = f"clause:{clause_id}"
            clause_totals[key] = clause_totals.get(key, 0) + int(n or 0)

        nodes: dict[str, dict[str, Any]] = {}
        links: list[dict[str, Any]] = []

        for src, clause_id, cref, ctitle, n in edge_rows:
            src_id = f"source:{src}"
            clause_node_id = f"clause:{clause_id}"
            if src_id not in nodes:
                nodes[src_id] = _source_node(src, src_totals.get(src_id, 0), request)
            if clause_node_id not in nodes:
                nodes[clause_node_id] = {
                    "id": clause_node_id,
                    "type": "control",
                    "node_kind": "clause",
                    "label": cref,
                    "clause_id": str(clause_id),
                    "control_id": str(clause_id),
                    "ref": cref,
                    "title": ctitle,
                    "color": _clause_color(cref),
                    "mapped_events": clause_totals.get(clause_node_id, 0),
                }
            links.append(
                {
                    "source": src_id,
                    "target": clause_node_id,
                    "value": int(n or 0),
                    "link_kind": "events",
                }
            )

        return {
            "nodes": list(nodes.values()),
            "links": links,
            "graph_kind": "source_clause_events",
        }

    return cached_json("graph:clause_source", cache_parts, _load)


@router.get("/v1/graph/clause_control")
def graph_clause_control(
    framework: str = settings.default_framework_slug,
    db: Session = Depends(get_db),
):
    """Static relationship graph: Clauses -> Controls."""

    cache_parts = {"framework": framework}

    def _load():
        rows = (
            db.query(FrameworkClause, ControlItem, ControlClauseLink.applicability)
            .join(ControlClauseLink, ControlClauseLink.clause_id == FrameworkClause.id)
            .join(ControlItem, ControlItem.id == ControlClauseLink.control_item_id)
            .filter(FrameworkClause.framework_slug == framework)
            .filter(ControlItem.framework_slug == framework)
            .all()
        )

        nodes: dict[str, dict[str, Any]] = {}
        links: list[dict[str, Any]] = []
        clause_counts: dict[str, int] = {}
        control_counts: dict[str, int] = {}

        for clause, control, applicability in rows:
            clause_id = f"clause:{clause.id}"
            control_id = f"control:{control.id}"
            clause_counts[clause_id] = clause_counts.get(clause_id, 0) + 1
            control_counts[control_id] = control_counts.get(control_id, 0) + 1
            if clause_id not in nodes:
                nodes[clause_id] = {
                    "id": clause_id,
                    "type": "source",
                    "node_kind": "clause",
                    "label": clause.ref,
                    "source": f"clause:{clause.id}",
                    "clause_id": str(clause.id),
                    "ref": clause.ref,
                    "title": clause.title,
                    "color": _clause_color(clause.ref),
                }
            if control_id not in nodes:
                nodes[control_id] = {
                    "id": control_id,
                    "type": "control",
                    "node_kind": "control",
                    "label": control.ref,
                    "control_id": str(control.id),
                    "ref": control.ref,
                    "title": control.title,
                    "in_scope": control.in_scope,
                    "upstream_url": _control_upstream_url(control),
                }
            links.append(
                {
                    "source": clause_id,
                    "target": control_id,
                    "value": 1,
                    "applicability": applicability,
                    "link_kind": "relationship",
                }
            )

        for nid, n in nodes.items():
            if n.get("node_kind") == "clause":
                n["mapped_events"] = clause_counts.get(nid, 0)
                n["link_count"] = clause_counts.get(nid, 0)
            elif n.get("node_kind") == "control":
                n["mapped_events"] = control_counts.get(nid, 0)
                n["link_count"] = control_counts.get(nid, 0)

        return {
            "nodes": list(nodes.values()),
            "links": links,
            "graph_kind": "clause_control_relationship",
        }

    return cached_json("graph:clause_control", cache_parts, _load)


@router.get("/v1/graph/risk_control")
def graph_risk_control(
    request: Request,
    framework: str = settings.default_framework_slug,
    db: Session = Depends(get_db),
):
    """Static relationship graph: Risks -> Controls for the selected framework.

    Risks are global, but their mitigating-control assignments are scoped by
    framework_slug, because a DVSTF control set and an ISO27001:2022 control set
    are different catalogues even when the underlying risk is the same.
    """

    user = getattr(request.state, "user", None)
    if not (
        has_permission(db, user, "risk.manage") or has_permission(db, user, "risk.read")
    ):
        raise HTTPException(status_code=403, detail="risk.read permission required")

    cache_parts = {"framework": framework}

    def _load():
        rows = (
            db.query(Risk, RiskAsset, RiskCategory, RiskAssetSubcategory, ControlItem)
            .join(RiskAsset, RiskAsset.id == Risk.asset_id)
            .join(RiskCategory, RiskCategory.id == RiskAsset.category_id)
            .join(
                RiskAssetSubcategory,
                RiskAssetSubcategory.id == RiskAsset.subcategory_id,
            )
            .join(RiskControlLink, RiskControlLink.risk_id == Risk.id)
            .join(ControlItem, ControlItem.id == RiskControlLink.control_item_id)
            .filter(RiskControlLink.framework_slug == framework)
            .filter(ControlItem.framework_slug == framework)
            .all()
        )

        nodes: dict[str, dict[str, Any]] = {}
        links: list[dict[str, Any]] = []
        risk_counts: dict[str, int] = {}
        control_counts: dict[str, int] = {}

        def risk_colour(risk_id: str) -> str:
            """Use a source-like categorical palette for risk nodes.

            Risk score is already represented by edge/cell weight and labels;
            using red/amber severity colours made the D3 risk visuals look
            brown/maroon and visually inconsistent with Sources × Controls.
            Keep risk colours deterministic per risk, but restrict the palette
            to the blue/purple/teal family used elsewhere in Keen visuals.
            """

            palette = [
                "#2563eb",  # blue
                "#7c3aed",  # purple
                "#0891b2",  # cyan
                "#0f766e",  # teal
                "#4f46e5",  # indigo
                "#0284c7",  # sky
                "#059669",  # emerald
                "#9333ea",  # violet
                "#0d9488",  # teal
                "#4338ca",  # indigo
            ]
            h = 0
            for ch in str(risk_id or ""):
                h = (h * 33 + ord(ch)) & 0xFFFFFFFF
            return palette[h % len(palette)]

        for risk, asset, category, subcategory, control in rows:
            risk_id = f"risk:{risk.id}"
            control_id = f"control:{control.id}"
            value = max(1, int(risk.risk_score or 0))
            risk_counts[risk_id] = risk_counts.get(risk_id, 0) + 1
            control_counts[control_id] = control_counts.get(control_id, 0) + 1
            if risk_id not in nodes:
                threat_label = (risk.threat_summary or "").strip()
                label = (
                    asset.name if not threat_label else f"{asset.name} — {threat_label}"
                )
                nodes[risk_id] = {
                    "id": risk_id,
                    "type": "source",
                    "node_kind": "risk",
                    "label": label,
                    "risk_id": str(risk.id),
                    "source": f"risk:{risk.id}",
                    "asset": asset.name,
                    "asset_id": str(asset.id),
                    "threat_summary": risk.threat_summary or "",
                    "category": category.name if category else None,
                    "subcategory": subcategory.name if subcategory else None,
                    "risk_types": list(risk.risk_types or []),
                    "risk_score": int(risk.risk_score or 0),
                    "residual_risk_score": int(risk.residual_risk_score or 0),
                    "mapped_events": value,
                    "link_count": 0,
                    "color": risk_colour(str(risk.id)),
                }
            if control_id not in nodes:
                nodes[control_id] = {
                    "id": control_id,
                    "type": "control",
                    "node_kind": "control",
                    "label": control.ref,
                    "control_id": str(control.id),
                    "ref": control.ref,
                    "title": control.title,
                    "in_scope": control.in_scope,
                    "upstream_url": _control_upstream_url(control),
                }
            links.append(
                {
                    "source": risk_id,
                    "target": control_id,
                    "value": value,
                    "risk_score": int(risk.risk_score or 0),
                    "residual_risk_score": int(risk.residual_risk_score or 0),
                    "link_kind": "relationship",
                }
            )

        for nid, node in nodes.items():
            if node.get("node_kind") == "risk":
                node["link_count"] = risk_counts.get(nid, 0)
                # For the UI distribution sidebar, this is a relationship count:
                # how many controls are mapped to the risk. Keep risk_score as its
                # own field for weighting graph edges/heatmap cells.
                node["mapped_events"] = risk_counts.get(nid, 0)
            elif node.get("node_kind") == "control":
                node["link_count"] = control_counts.get(nid, 0)
                node["mapped_events"] = control_counts.get(nid, 0)

        return {
            "nodes": list(nodes.values()),
            "links": links,
            "graph_kind": "risk_control_relationship",
        }

    return cached_json("graph:risk_control", cache_parts, _load)
