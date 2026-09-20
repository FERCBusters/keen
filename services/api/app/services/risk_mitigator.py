from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any
import uuid

import yaml
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.utils import control_justification as _control_justification
from app.api.utils import control_upstream_url as _control_upstream_url
from app.api.utils import ref_sort_key as _ref_sort_key
from app.core.config import settings
from app.db.models import (
    ControlItem,
    FrameworkClause,
    InterestedParty,
    InterestedPartyName,
    InterestedPartyNature,
    PestleBusinessProcess,
    PestleItem,
    Risk,
    RiskAsset,
    RiskCategory,
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+._/-]*", re.I)
_RISK_TYPES = {"Confidentiality", "Integrity", "Availability"}
_PESTLE_TYPES = {
    "Political",
    "Economical",
    "Social",
    "Technological",
    "Legal",
    "Environmental",
    "Ethical",
}
_BUSINESS_PROCESS_NAMES = [
    "Board Strategy",
    "Business Development",
    "Development Operations",
    "Implementation and Support",
    "Platform Security and Operations",
    "Software Development",
    "Architecture and requirements",
]


def _default_rules_path() -> str:
    """Return the root-level config path used by Docker Compose deployments."""
    return "/app/config/risk_mitigator.yml"


def _repo_rules_path() -> Path | None:
    """Return the repo-local root config path for local development/tests.

    The API container runs this module from /app/app/services, while a source
    checkout usually has the file under services/api/app/services. Avoid a fixed
    parents[N] lookup so Docker, local development, and tests all resolve the
    config file safely.
    """
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "config" / "risk_mitigator.yml"
        if candidate.exists():
            return candidate
    return None


def _rules_path_candidates(path: str | None = None) -> list[Path]:
    """Return ordered candidate locations for the Mitigator YAML rules file."""
    raw_candidates = [
        Path(str(path).strip()) if path else None,
        (
            Path(str(settings.risk_mitigator_rules_path).strip())
            if settings.risk_mitigator_rules_path
            else None
        ),
        Path(_default_rules_path()),
        _repo_rules_path(),
    ]

    candidates: list[Path] = []
    seen: set[Path] = set()
    for candidate in raw_candidates:
        if not candidate:
            continue
        expanded = candidate.expanduser()
        if expanded in seen:
            continue
        candidates.append(expanded)
        seen.add(expanded)
    return candidates


@lru_cache(maxsize=4)
def load_rules(path: str | None = None) -> dict[str, Any]:
    candidates = _rules_path_candidates(path)
    p = next(
        (candidate for candidate in candidates if candidate.exists()),
        None,
    )
    if not p:
        return {}
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return {}
    return data


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _tokens(value: Any) -> set[str]:
    return {
        m.group(0).lower().strip("._/-")
        for m in _TOKEN_RE.finditer(str(value or ""))
        if m.group(0).strip("._/-")
    }


def _contains_term(haystack: str, term: str) -> bool:
    h = _norm_text(haystack)
    t = _norm_text(term)
    if not h or not t:
        return False
    if " " in t:
        return t in h
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", h))


def _unique_strs(values: list[Any] | tuple[Any, ...] | set[Any] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        value = str(raw or "").strip()
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _clamp_weight(raw: Any) -> int:
    try:
        n = int(raw)
    except Exception:
        n = 0
    return max(0, min(100, n))


def _confidence(score: float, *, strong: float = 30.0, medium: float = 16.0) -> str:
    if score >= strong:
        return "strong"
    if score >= medium:
        return "medium"
    return "weak"


def _normalise_risk_types(
    raw: list[str] | None, text_blob: str, rules: dict[str, Any]
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for value in raw or []:
        v = str(value or "").strip()
        match = next((rt for rt in _RISK_TYPES if rt.lower() == v.lower()), None)
        if match and match not in seen:
            seen.add(match)
            selected.append(match)
    if selected:
        return selected

    # If the UI did not explicitly send a type, infer a conservative default from
    # scenario terms. Creation still uses the final normalized list.
    for rt, cfg in (rules.get("risk_types") or {}).items():
        if rt not in _RISK_TYPES:
            continue
        if any(
            _contains_term(text_blob, term) for term in cfg.get("scenario_terms") or []
        ):
            selected.append(rt)
    return selected or ["Confidentiality"]


def _control_doc(control: ControlItem) -> str:
    parts = [
        control.ref,
        control.title,
        control.type,
        _control_justification(control),
        _control_upstream_url(control),
    ]
    meta = getattr(control, "meta", None) or {}
    tags = getattr(control, "tags", None) or {}
    if meta:
        parts.append(str(meta))
    if tags:
        parts.append(str(tags))
    return " ".join(str(p or "") for p in parts)


def _control_out(control: ControlItem | None) -> dict[str, Any]:
    if not control:
        return {}
    return {
        "id": str(control.id),
        "framework": control.framework_slug,
        "type": control.type,
        "ref": control.ref,
        "title": control.title,
        "in_scope": control.in_scope,
        "justification": _control_justification(control),
        "upstream_url": _control_upstream_url(control),
    }


def _category_name(
    db: Session, category_id: uuid.UUID | None, category_name: str | None
) -> str:
    if category_name:
        return str(category_name or "").strip()
    if category_id:
        row = (
            db.query(RiskCategory).filter(RiskCategory.id == category_id).one_or_none()
        )
        if row:
            return row.name
    return ""


def _fts_scores(db: Session, *, framework: str, terms: list[str]) -> dict[str, float]:
    q_terms = []
    for term in terms:
        if len(term) < 3:
            continue
        # Keep the DB FTS query compact and word-like. Phrase/substring matching is
        # still handled in Python below so operators are not needed here.
        q_terms.extend(sorted(_tokens(term)))
    query = " ".join(_unique_strs(q_terms)[:32])
    if not query:
        return {}
    sql = text("""
        SELECT id::text AS id,
               ts_rank_cd(
                 to_tsvector('simple',
                   coalesce(ref, '') || ' ' ||
                   coalesce(title, '') || ' ' ||
                   coalesce(type, '') || ' ' ||
                   coalesce(tags::text, '') || ' ' ||
                   coalesce("metadata"::text, '')
                 ),
                 plainto_tsquery('simple', :q)
               ) AS rank
        FROM control_items
        WHERE framework_slug = :framework
          AND type != 'clause'
          AND to_tsvector('simple',
            coalesce(ref, '') || ' ' ||
            coalesce(title, '') || ' ' ||
            coalesce(type, '') || ' ' ||
            coalesce(tags::text, '') || ' ' ||
            coalesce("metadata"::text, '')
          ) @@ plainto_tsquery('simple', :q)
        """)
    try:
        rows = db.execute(sql, {"framework": framework, "q": query}).mappings().all()
    except Exception:
        # Keep the wizard usable on non-Postgres dev/test databases. The Python
        # scorer still works; the migration provides the production FTS index.
        return {}
    return {str(r["id"]): float(r.get("rank") or 0.0) for r in rows}


def _term_score(
    doc: str, terms: list[str], *, title: str = ""
) -> tuple[float, list[str]]:
    doc_norm = _norm_text(doc)
    title_norm = _norm_text(title)
    score = 0.0
    matched_terms: list[str] = []
    for term in terms:
        term_norm = _norm_text(term)
        if not term_norm:
            continue
        matched = (
            term_norm in doc_norm
            if " " in term_norm
            else _contains_term(doc_norm, term_norm)
        )
        if not matched:
            matched = any(
                tok and tok in doc_norm for tok in _tokens(term) if len(tok) >= 5
            )
        if not matched:
            continue
        matched_terms.append(term)
        score += 10.0 if term_norm and term_norm in title_norm else 4.0
    return score, _unique_strs(matched_terms)


def _detect_pestle_types(
    *,
    text_blob: str,
    rules: dict[str, Any],
    active_flags: dict[str, bool],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Return (type_hits, signals, terms) for PESTLE(E) matching."""

    out: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    terms: list[str] = []
    for pestle_type, cfg in (rules.get("pestle_types") or {}).items():
        canonical = next(
            (
                value
                for value in _PESTLE_TYPES
                if value.lower() == str(pestle_type).lower()
            ),
            None,
        )
        if not canonical:
            continue
        scenario_terms = cfg.get("scenario_terms") or []
        matched = [t for t in scenario_terms if _contains_term(text_blob, t)]
        for flag in active_flags:
            if flag in (cfg.get("impact_flags") or []):
                matched.append(flag.replace("_", " "))
        matched = _unique_strs(matched)
        if not matched:
            continue
        terms.extend((cfg.get("search_terms") or []) + scenario_terms)
        hit = {
            "type": canonical,
            "lens": cfg.get("lens") or "External",
            "score": len(matched) * 8 + len(cfg.get("search_terms") or []),
            "matched_terms": matched[:12],
            "reason": cfg.get("reason")
            or f"The scenario matched {canonical} PESTLE(E) signals.",
        }
        out.append(hit)
        signals.append(
            {
                "kind": "pestle_type",
                "label": canonical,
                "matched_terms": matched[:12],
                "reason": hit["reason"],
            }
        )
    out.sort(
        key=lambda row: (-float(row.get("score") or 0), str(row.get("type") or ""))
    )
    return out, signals, terms


def _detect_business_processes(
    db: Session,
    *,
    text_blob: str,
    rules: dict[str, Any],
    pestle_hits: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    by_name = {
        _norm_text(row.name): row
        for row in db.query(PestleBusinessProcess)
        .filter(PestleBusinessProcess.name.in_(_BUSINESS_PROCESS_NAMES))
        .all()
    }
    scored: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    terms: list[str] = []
    hit_types = {str(h.get("type") or "") for h in pestle_hits}

    for name, cfg in (rules.get("pestle_business_processes") or {}).items():
        canonical = next(
            (bp for bp in _BUSINESS_PROCESS_NAMES if bp.lower() == str(name).lower()),
            str(name),
        )
        matched = [t for t in cfg.get("terms") or [] if _contains_term(text_blob, t)]
        type_bonus = [t for t in cfg.get("pestle_types") or [] if t in hit_types]
        if not matched and not type_bonus:
            continue
        score = len(matched) * 6 + len(type_bonus) * 4
        row = by_name.get(_norm_text(canonical))
        item = {
            "id": str(row.id) if row else None,
            "name": canonical,
            "score": score,
            "matched_terms": _unique_strs(matched + type_bonus)[:12],
            "reason": cfg.get("reason")
            or "The scenario matched this business process.",
            "relevance_code": "high" if score >= 10 else "medium",
            "exists": bool(row),
        }
        scored.append(item)
        terms.extend(cfg.get("terms") or [])
        signals.append(
            {
                "kind": "business_process",
                "label": canonical,
                "matched_terms": item["matched_terms"],
                "reason": item["reason"],
            }
        )

    if not scored and pestle_hits:
        # A conservative default keeps a freshly-created item useful without
        # pretending to know operational relevance.
        row = by_name.get(_norm_text("Board Strategy"))
        scored.append(
            {
                "id": str(row.id) if row else None,
                "name": "Board Strategy",
                "score": 1,
                "matched_terms": [],
                "reason": "Default governance process for a newly identified PESTLE(E) scenario.",
                "relevance_code": "medium",
                "exists": bool(row),
            }
        )

    scored.sort(
        key=lambda row: (-float(row.get("score") or 0), str(row.get("name") or ""))
    )
    return scored[:4], signals, terms


def _clause_summary(row: FrameworkClause | None) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": str(row.id),
        "framework": row.framework_slug,
        "ref": row.ref,
        "title": row.title,
        "parent_id": str(row.parent_clause_id) if row.parent_clause_id else None,
        "parent_ref": row.parent.ref if getattr(row, "parent", None) else None,
        "sort_order": int(row.sort_order or 0),
    }


def _pestle_item_doc(row: PestleItem) -> str:
    business_processes = " ".join(
        getattr(link.business_process, "name", "") or ""
        for link in (row.business_process_links or [])
    )
    clauses = " ".join(
        f"{getattr(link.clause, 'ref', '')} {getattr(link.clause, 'title', '')}"
        for link in (row.clause_links or [])
    )
    return " ".join(
        str(p or "")
        for p in [
            row.item,
            row.rationale,
            row.type,
            row.lens,
            business_processes,
            clauses,
        ]
    )


def _pestle_item_out(
    row: PestleItem, score: float, matched_terms: list[str], reasons: list[str]
) -> dict[str, Any]:
    clause_links = [
        link
        for link in (row.clause_links or [])
        if getattr(getattr(link, "relevance", None), "code", None) != "na"
    ]
    bp_links = [
        link
        for link in (row.business_process_links or [])
        if getattr(getattr(link, "relevance", None), "code", None) != "na"
    ]
    return {
        "id": str(row.id),
        "framework": row.framework_slug,
        "type": row.type,
        "lens": row.lens,
        "item": row.item or "",
        "rationale": row.rationale or "",
        "score": round(score, 2),
        "confidence": _confidence(score, strong=28, medium=14),
        "matched_terms": _unique_strs(matched_terms)[:12],
        "reasons": _unique_strs(reasons)[:4],
        "business_processes": [
            {
                "id": str(link.business_process_id),
                "name": getattr(link.business_process, "name", ""),
                "relevance_code": getattr(
                    getattr(link, "relevance", None), "code", None
                ),
                "relevance_label": getattr(
                    getattr(link, "relevance", None), "label", None
                ),
            }
            for link in sorted(
                bp_links, key=lambda x: getattr(x.business_process, "name", "")
            )
        ],
        "clauses": [
            {
                "clause": _clause_summary(link.clause),
                "relevance_code": getattr(
                    getattr(link, "relevance", None), "code", None
                ),
                "relevance_label": getattr(
                    getattr(link, "relevance", None), "label", None
                ),
            }
            for link in sorted(
                clause_links,
                key=lambda x: (
                    int(getattr(x.clause, "sort_order", 0) or 0),
                    _ref_sort_key(getattr(x.clause, "ref", "")),
                ),
            )
        ],
    }


def _search_pestle_items(
    db: Session,
    *,
    framework: str,
    terms: list[str],
    pestle_hits: list[dict[str, Any]],
    limit: int = 8,
) -> list[dict[str, Any]]:
    rows = db.query(PestleItem).filter(PestleItem.framework_slug == framework).all()
    hit_types = {str(hit.get("type") or "") for hit in pestle_hits}
    suggestions: list[dict[str, Any]] = []
    for row in rows:
        doc = _pestle_item_doc(row)
        score, matched_terms = _term_score(doc, terms, title=row.item or "")
        reasons: list[str] = []
        if row.type in hit_types:
            score += 8.0
            reasons.append(f"The scenario matched the {row.type} PESTLE(E) lens.")
        if matched_terms:
            reasons.append(
                "The PESTLE(E) item text, rationale, clauses or business processes matched the issue keywords."
            )
        # Exact-ish duplicate guard: if the user's sentence contains the item name,
        # or the item name contains a meaningful issue phrase, push it up.
        item_norm = _norm_text(row.item or "")
        if item_norm and any(
            _contains_term(t, item_norm) for t in terms if len(t) >= 8
        ):
            score += 10.0
            reasons.append("The item name closely matches a phrase in the issue text.")
        if score <= 0:
            continue
        suggestions.append(_pestle_item_out(row, score, matched_terms, reasons))

    suggestions.sort(
        key=lambda x: (-float(x.get("score") or 0), str(x.get("item") or ""))
    )
    return suggestions[: max(1, min(limit, 12))]


def _short_issue_title(issue: str, *, max_len: int = 180) -> str:
    value = re.sub(r"\s+", " ", str(issue or "").strip())
    if not value:
        return "Keen Mitigator scenario"
    first_sentence = re.split(r"(?<=[.!?])\s+", value, maxsplit=1)[0]
    title = first_sentence.strip(" .!?;:\n\t") or value
    if len(title) > max_len:
        title = title[: max_len - 1].rstrip() + "…"
    return title


def _draft_pestle_item(
    *,
    framework: str,
    issue: str,
    pestle_hits: list[dict[str, Any]],
    business_processes: list[dict[str, Any]],
    existing_pestle_items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not pestle_hits and not existing_pestle_items:
        return None
    primary = pestle_hits[0] if pestle_hits else {}
    pestle_type = primary.get("type") or (
        existing_pestle_items[0].get("type")
        if existing_pestle_items
        else "Technological"
    )
    lens = primary.get("lens") or (
        existing_pestle_items[0].get("lens") if existing_pestle_items else "External"
    )
    rationale_lines = [
        "Created from Keen Mitigator questionnaire.",
        str(issue or "").strip(),
    ]
    if pestle_hits:
        rationale_lines.append(
            "Detected PESTLE(E) signals: "
            + ", ".join(
                f"{hit.get('type')} ({', '.join(hit.get('matched_terms') or [])})".strip()
                for hit in pestle_hits[:4]
            )
        )
    if existing_pestle_items:
        rationale_lines.append(
            "Possible existing related items: "
            + "; ".join(str(x.get("item") or "") for x in existing_pestle_items[:5])
        )
    return {
        "framework": framework,
        "type": pestle_type,
        "lens": lens,
        "item": _short_issue_title(issue),
        "overall_relevance_code": "medium",
        "rationale": "\n".join([line for line in rationale_lines if line]).strip(),
        "business_processes": [bp for bp in business_processes if bp.get("id")],
    }


def _risk_doc(row: Risk) -> str:
    asset = getattr(row, "asset", None)
    category = getattr(asset, "category", None)
    subcategory = getattr(asset, "subcategory", None)
    control_refs = " ".join(
        f"{getattr(link.control, 'ref', '')} {getattr(link.control, 'title', '')}"
        for link in (row.control_links or [])
    )
    return " ".join(
        str(p or "")
        for p in [
            getattr(asset, "name", ""),
            getattr(category, "name", ""),
            getattr(subcategory, "name", ""),
            row.threat_summary,
            row.note,
            " ".join(row.risk_types or []),
            control_refs,
        ]
    )


def _search_existing_risks(
    db: Session, *, terms: list[str], limit: int = 6
) -> list[dict[str, Any]]:
    rows = db.query(Risk).join(RiskAsset, RiskAsset.id == Risk.asset_id).all()
    matches: list[dict[str, Any]] = []
    for row in rows:
        asset = getattr(row, "asset", None)
        doc = _risk_doc(row)
        score, matched_terms = _term_score(
            doc, terms, title=f"{getattr(asset, 'name', '')} {row.threat_summary}"
        )
        if score < 8:
            continue
        matches.append(
            {
                "id": str(row.id),
                "asset_name": getattr(asset, "name", "") or "",
                "risk_types": list(row.risk_types or []),
                "threat_summary": row.threat_summary or "",
                "score": round(score, 2),
                "confidence": _confidence(score, strong=24, medium=12),
                "matched_terms": matched_terms[:12],
            }
        )
    matches.sort(
        key=lambda x: (-float(x.get("score") or 0), str(x.get("asset_name") or ""))
    )
    return matches[:limit]


def _detect_interested_parties(
    *, text_blob: str, rules: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    suggestions: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    terms: list[str] = []
    for key, cfg in (rules.get("interested_parties") or {}).items():
        name = str(cfg.get("name") or key).strip()
        matched = [t for t in cfg.get("terms") or [] if _contains_term(text_blob, t)]
        if not matched:
            continue
        terms.extend(cfg.get("terms") or [])
        nature = str(cfg.get("nature") or "Impacted by scenario").strip()
        suggestion = {
            "key": key,
            "name": name,
            "nature": nature,
            "note": cfg.get("note")
            or "Suggested by Keen Mitigator from issue keywords.",
            "matched_terms": _unique_strs(matched)[:12],
            "reason": cfg.get("reason")
            or f"The issue mentions terms that affect {name}.",
            "communication": cfg.get("communication") or None,
        }
        suggestions.append(suggestion)
        signals.append(
            {
                "kind": "interested_party",
                "label": name,
                "matched_terms": suggestion["matched_terms"],
                "reason": suggestion["reason"],
            }
        )
    suggestions.sort(key=lambda row: str(row.get("name") or ""))
    return suggestions, signals, terms


def _party_out(
    row: InterestedParty, score: float = 0, matched_terms: list[str] | None = None
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "framework": row.framework_slug,
        "name": getattr(row.name, "name", "") or "",
        "nature": getattr(row.nature, "name", "") or "",
        "label": f"{getattr(row.name, 'name', '') or ''} — {getattr(row.nature, 'name', '') or ''}",
        "note": row.note or "",
        "score": round(score, 2) if score else 0,
        "confidence": _confidence(score, strong=24, medium=12) if score else "existing",
        "matched_terms": _unique_strs(matched_terms or [])[:12],
    }


def _search_interested_parties(
    db: Session,
    *,
    framework: str,
    terms: list[str],
    suggested_parties: list[dict[str, Any]],
    selected_control_ids: list[str],
    issue: str,
    limit: int = 8,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = (
        db.query(InterestedParty)
        .join(InterestedPartyName, InterestedPartyName.id == InterestedParty.name_id)
        .join(
            InterestedPartyNature, InterestedPartyNature.id == InterestedParty.nature_id
        )
        .filter(InterestedParty.framework_slug == framework)
        .all()
    )
    existing_matches: list[dict[str, Any]] = []
    exact_existing_keys: set[str] = set()
    for row in rows:
        doc = " ".join(
            str(p or "")
            for p in [
                getattr(row.name, "name", ""),
                getattr(row.nature, "name", ""),
                row.note,
            ]
        )
        score, matched_terms = _term_score(
            doc,
            terms,
            title=f"{getattr(row.name, 'name', '')} {getattr(row.nature, 'name', '')}",
        )
        for suggestion in suggested_parties:
            if _norm_text(getattr(row.name, "name", "")) == _norm_text(
                suggestion.get("name")
            ):
                score += 8.0
                matched_terms.extend(suggestion.get("matched_terms") or [])
                if _norm_text(getattr(row.nature, "name", "")) == _norm_text(
                    suggestion.get("nature")
                ):
                    score += 12.0
                    exact_existing_keys.add(
                        _norm_text(
                            f"{suggestion.get('name')}|{suggestion.get('nature')}"
                        )
                    )
        if score < 8:
            continue
        existing_matches.append(
            _party_out(row, score=score, matched_terms=matched_terms)
        )
    existing_matches.sort(
        key=lambda x: (-float(x.get("score") or 0), str(x.get("label") or ""))
    )

    create_suggestions: list[dict[str, Any]] = []
    for idx, suggestion in enumerate(suggested_parties):
        key = _norm_text(f"{suggestion.get('name')}|{suggestion.get('nature')}")
        communication = suggestion.get("communication") or None
        communications = [communication] if isinstance(communication, dict) else []
        draft = {
            "framework": framework,
            "name": suggestion.get("name"),
            "nature": suggestion.get("nature"),
            "note": "\n".join(
                [
                    "Created from Keen Mitigator questionnaire.",
                    str(suggestion.get("note") or "").strip(),
                    str(issue or "").strip(),
                ]
            ).strip(),
            "controls": selected_control_ids,
            "communications": communications,
        }
        create_suggestions.append(
            {
                **suggestion,
                "id": f"draft-{idx}",
                "duplicate_exact": key in exact_existing_keys,
                "draft": draft,
            }
        )

    return existing_matches[:limit], create_suggestions


def analyse_risk_mitigation(
    db: Session,
    *,
    framework: str,
    issue: str,
    asset_name: str = "",
    category_id: uuid.UUID | None = None,
    category_name: str | None = None,
    subcategory_id: uuid.UUID | None = None,
    subcategory_name: str | None = None,
    risk_types: list[str] | None = None,
    risk_weights: dict[str, Any] | None = None,
    impact_flags: dict[str, Any] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    rules = load_rules()
    category = _category_name(db, category_id, category_name)
    text_blob = " ".join(
        [issue or "", asset_name or "", category or "", subcategory_name or ""]
    )
    selected_types = _normalise_risk_types(risk_types, text_blob, rules)
    weights = {rt: _clamp_weight((risk_weights or {}).get(rt, 0)) for rt in _RISK_TYPES}
    for rt in selected_types:
        if weights.get(rt, 0) <= 0:
            weights[rt] = 50
    if not any(weights.values()):
        weights[selected_types[0]] = 50

    terms_for_matching: list[str] = []
    signals: list[dict[str, Any]] = []

    risk_type_rules = rules.get("risk_types") or {}
    for rt in selected_types:
        cfg = risk_type_rules.get(rt) or {}
        terms = _unique_strs(
            (cfg.get("mitigation_words") or []) + (cfg.get("scenario_terms") or [])
        )
        terms_for_matching.extend(terms)
        matched_scenario = [
            t for t in cfg.get("scenario_terms") or [] if _contains_term(text_blob, t)
        ]
        signals.append(
            {
                "kind": "risk_type",
                "label": rt,
                "weight": weights.get(rt, 0),
                "matched_terms": matched_scenario,
                "reason": cfg.get("reason")
                or f"{rt} was selected in the questionnaire.",
            }
        )

    asset_rules = rules.get("asset_categories") or {}
    matched_asset_cfg = None
    if category:
        for cat_name, cfg in asset_rules.items():
            if cat_name.lower() == category.lower():
                matched_asset_cfg = cfg
                terms_for_matching.extend(cfg.get("mitigation_words") or [])
                signals.append(
                    {
                        "kind": "asset_category",
                        "label": category,
                        "matched_terms": [category],
                        "reason": cfg.get("reason")
                        or f"The asset category is {category}.",
                    }
                )
                break

    flag_rules = rules.get("impact_flags") or {}
    active_flags = {str(k): bool(v) for k, v in (impact_flags or {}).items() if bool(v)}
    for flag, enabled in active_flags.items():
        if not enabled:
            continue
        cfg = flag_rules.get(flag) or {}
        terms_for_matching.extend(cfg.get("mitigation_words") or [])
        for rt in cfg.get("risk_types") or []:
            if rt in _RISK_TYPES and rt not in selected_types:
                selected_types.append(rt)
                weights[rt] = max(weights.get(rt, 0), 40)
        signals.append(
            {
                "kind": "impact",
                "label": cfg.get("label") or flag.replace("_", " ").title(),
                "matched_terms": cfg.get("mitigation_words") or [],
                "reason": cfg.get("reason")
                or "The questionnaire marked this impact as relevant.",
            }
        )

    scenario_rules = rules.get("scenario_terms") or {}
    for key, cfg in scenario_rules.items():
        matched = [t for t in cfg.get("terms") or [] if _contains_term(text_blob, t)]
        if not matched:
            continue
        terms_for_matching.extend(cfg.get("mitigation_words") or [])
        signals.append(
            {
                "kind": "scenario",
                "label": key.replace("_", " ").title(),
                "matched_terms": matched,
                "reason": cfg.get("reason")
                or "The issue text matched this scenario pattern.",
            }
        )

    pestle_hits, pestle_signals, pestle_terms = _detect_pestle_types(
        text_blob=text_blob,
        rules=rules,
        active_flags=active_flags,
    )
    business_processes, business_process_signals, bp_terms = _detect_business_processes(
        db,
        text_blob=text_blob,
        rules=rules,
        pestle_hits=pestle_hits,
    )
    party_detections, party_signals, party_terms = _detect_interested_parties(
        text_blob=text_blob,
        rules=rules,
    )
    signals.extend(pestle_signals)
    signals.extend(business_process_signals)
    signals.extend(party_signals)
    terms_for_matching.extend(pestle_terms)
    terms_for_matching.extend(bp_terms)
    terms_for_matching.extend(party_terms)

    # Let the user's own words influence matches as well, without letting very short
    # or generic tokens dominate the curated controls vocabulary.
    issue_tokens = [t for t in sorted(_tokens(text_blob)) if len(t) >= 4]
    terms_for_matching = _unique_strs(terms_for_matching + issue_tokens[:30])
    fts = _fts_scores(db, framework=framework, terms=terms_for_matching)

    controls = (
        db.query(ControlItem)
        .filter(ControlItem.framework_slug == framework, ControlItem.type != "clause")
        .all()
    )
    suggestions: list[dict[str, Any]] = []
    lower_terms = [
        (term, _norm_text(term), _tokens(term)) for term in terms_for_matching
    ]

    for control in controls:
        doc = _control_doc(control)
        doc_norm = _norm_text(doc)
        title_norm = _norm_text(control.title or "")
        score = 0.0
        matched_terms: list[str] = []
        reasons: list[str] = []

        for term, term_norm, term_tokens in lower_terms:
            if not term_norm:
                continue
            matched = False
            if " " in term_norm:
                matched = term_norm in doc_norm
            else:
                matched = _contains_term(doc_norm, term_norm)
            if not matched:
                # Small fuzzy bridge for British/US spellings and compound titles.
                matched = any(
                    tok and tok in doc_norm for tok in term_tokens if len(tok) >= 5
                )
            if not matched:
                continue
            matched_terms.append(term)
            if term_norm in title_norm:
                score += 10.0
            else:
                score += 4.0

        # Selected CIA types carry proportional weight if any of their mitigation
        # words appear in the control document.
        for rt in selected_types:
            cfg = risk_type_rules.get(rt) or {}
            rt_terms = cfg.get("mitigation_words") or []
            if any(_contains_term(doc_norm, t) for t in rt_terms):
                score += max(1.0, weights.get(rt, 0) / 10.0)
                reasons.append(cfg.get("reason") or f"Relevant to {rt}.")

        if (
            category
            and matched_asset_cfg
            and any(
                _contains_term(doc_norm, t)
                for t in matched_asset_cfg.get("mitigation_words") or []
            )
        ):
            score += 6.0
            reasons.append(
                matched_asset_cfg.get("reason") or f"Relevant to {category} assets."
            )

        fts_rank = fts.get(str(control.id), 0.0)
        if fts_rank:
            score += min(20.0, 200.0 * fts_rank)
            reasons.append(
                "The control catalog text matched the questionnaire terms via PostgreSQL full-text search."
            )

        # Prefer in-scope controls but do not hide out-of-scope controls; users may
        # still want to review them and bring them into scope.
        if control.in_scope:
            score += 1.0
        else:
            score *= 0.85

        if score <= 0:
            continue
        matched_terms = _unique_strs(matched_terms)[:12]
        if not reasons:
            reasons.append(
                "The control title or metadata matched the mitigation terms derived from the questionnaire."
            )
        suggestions.append(
            {
                "control": _control_out(control),
                "score": round(score, 2),
                "confidence": _confidence(score),
                "matched_terms": matched_terms,
                "reasons": _unique_strs(reasons)[:4],
            }
        )

    suggestions.sort(
        key=lambda item: (
            -float(item.get("score") or 0),
            _ref_sort_key(item.get("control", {}).get("ref") or ""),
        )
    )
    limit = max(1, min(int(limit or 10), 25))
    suggestions = suggestions[:limit]

    selected_control_ids = [s["control"]["id"] for s in suggestions[:8]]
    recommended_refs = [
        f"{s['control'].get('ref')} {s['control'].get('title') or ''}".strip()
        for s in suggestions[:8]
    ]
    threat_summary = str(issue or "").strip()
    if len(threat_summary) > 12000:
        threat_summary = threat_summary[:12000]
    note_lines = [
        "Created from Keen Mitigator questionnaire.",
        f"Dominant risk weighting: "
        + ", ".join(
            f"{rt}={weights.get(rt, 0)}"
            for rt in ["Confidentiality", "Integrity", "Availability"]
        ),
    ]
    if pestle_hits:
        note_lines.append(
            "Detected PESTLE(E) types: "
            + ", ".join(hit.get("type") or "" for hit in pestle_hits[:4])
        )
    if party_detections:
        note_lines.append(
            "Detected interested parties: "
            + ", ".join(p.get("name") or "" for p in party_detections[:6])
        )
    if active_flags:
        note_lines.append("Selected impacts: " + ", ".join(sorted(active_flags)))
    if recommended_refs:
        note_lines.append("Suggested controls: " + "; ".join(recommended_refs))
    note = "\n".join(note_lines)

    existing_risks = _search_existing_risks(db, terms=terms_for_matching, limit=6)
    existing_pestle_items = _search_pestle_items(
        db,
        framework=framework,
        terms=terms_for_matching,
        pestle_hits=pestle_hits,
        limit=8,
    )
    existing_interested_parties, interested_party_suggestions = (
        _search_interested_parties(
            db,
            framework=framework,
            terms=terms_for_matching,
            suggested_parties=party_detections,
            selected_control_ids=selected_control_ids,
            issue=threat_summary,
            limit=8,
        )
    )
    draft_pestle_item = _draft_pestle_item(
        framework=framework,
        issue=threat_summary,
        pestle_hits=pestle_hits,
        business_processes=business_processes,
        existing_pestle_items=existing_pestle_items,
    )

    context = {
        "source": "keen_mitigator",
        "issue": threat_summary,
        "asset_name": asset_name or "",
        "category_id": str(category_id) if category_id else None,
        "category_name": category or category_name or "",
        "subcategory_id": str(subcategory_id) if subcategory_id else None,
        "subcategory_name": subcategory_name or "",
        "risk_types": selected_types,
        "risk_weights": weights,
        "impact_flags": active_flags,
        "signals": signals,
        "suggested_controls": [
            {
                "id": s["control"]["id"],
                "ref": s["control"].get("ref"),
                "title": s["control"].get("title"),
                "score": s.get("score"),
                "confidence": s.get("confidence"),
                "matched_terms": s.get("matched_terms") or [],
                "reasons": s.get("reasons") or [],
            }
            for s in suggestions
        ],
        "pestle_hits": pestle_hits,
        "interested_party_suggestions": [
            {
                "name": x.get("name"),
                "nature": x.get("nature"),
                "matched_terms": x.get("matched_terms") or [],
            }
            for x in interested_party_suggestions
        ],
    }

    draft_risk = {
        "asset_name": asset_name or "",
        "category_id": str(category_id) if category_id else None,
        "category_name": category_name or None,
        "subcategory_id": str(subcategory_id) if subcategory_id else None,
        "subcategory_name": subcategory_name or None,
        "risk_types": selected_types,
        "threat_summary": threat_summary,
        "threat_score": 1,
        "vulnerability_score": 1,
        "impact_score": 1,
        "residual_vulnerability_score": 1,
        "residual_impact_score": 1,
        "note": note,
        "framework": framework,
        "controls": selected_control_ids,
        "mitigator_context": context,
    }

    return {
        "framework": framework,
        "risk_types": selected_types,
        "risk_weights": weights,
        "asset_category": category or category_name or "",
        "signals": signals,
        "query_terms": terms_for_matching[:80],
        "suggestions": suggestions,
        "control_suggestions": suggestions,
        "pestle_hits": pestle_hits,
        "business_process_suggestions": business_processes,
        "pestle_suggestions": existing_pestle_items,
        "interested_party_suggestions": interested_party_suggestions,
        "existing_matches": {
            "cia_risks": existing_risks,
            "pestle_items": existing_pestle_items,
            "interested_parties": existing_interested_parties,
        },
        "draft_risk": draft_risk,
        "draft_pestle_item": draft_pestle_item,
    }
