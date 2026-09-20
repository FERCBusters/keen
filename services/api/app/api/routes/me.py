from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.payloads import (
    ChangePasswordPayload,
    PreferencesOut,
    PreferencesUpdatePayload,
    SavedSearchCreatePayload,
    SavedSearchOut,
)
from app.api.utils import (
    ALLOWED_THEMES,
    normalize_saved_search_url as _normalize_saved_search_url,
    try_uuid as _try_uuid,
    validate_theme_id as _validate_theme_id,
    validate_timezone_name as _validate_timezone_name,
)
from app.core.config import settings
from app.core.source_meta import normalize_hex_color
from app.db.models import EventQuestionThread, SavedSearch, User
from app.db.session import get_db
from app.security.auth import ROLE_ADMIN
from app.security.permissions import has_permission
from app.security.roles import compute_effective_role
from app.security.passwords import hash_password, verify_password
from app.security.rate_limit import client_ip as _client_ip, fixed_window_allow
from app.core.valkey import get_valkey

router = APIRouter()


# -----------------------------------------------------------------------------
# Preference helpers
# -----------------------------------------------------------------------------


def _normalize_source_colors(raw: dict | None) -> dict[str, str]:
    """Validate/normalize a user-supplied source->hex mapping."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="source_colors must be an object")
    if len(raw) > 500:
        raise HTTPException(status_code=400, detail="source_colors is too large")
    out: dict[str, str] = {}
    for k, v in raw.items():
        key = (str(k) if k is not None else "").strip()
        if not key:
            continue
        if len(key) > 128:
            raise HTTPException(status_code=400, detail="source_colors key is too long")
        col = normalize_hex_color(str(v) if v is not None else None)
        if not col:
            raise HTTPException(
                status_code=400, detail="Invalid hex color for source: " + key
            )
        out[key] = col
    return out


# Allowed visualisation modes (Visualisation page)
_VIZ_MODES = {"graph", "heatmap", "sunburst_source", "sunburst_control", "histogram"}


def _validate_viz_mode(raw: str | None) -> str:
    """Validate user-selected visualisation mode. Blank/None resets to default."""
    v = (raw or "").strip()
    if not v:
        return "graph"
    if v not in _VIZ_MODES:
        raise HTTPException(status_code=400, detail="Invalid viz_mode")
    return v


def _validate_date_format(raw: str | None) -> str:
    """Validate visible date input preference. Blank/None means app default."""
    v = (raw or "default").strip().lower()
    aliases = {
        "iso": "ymd",
        "yyyy-mm-dd": "ymd",
        "dd/mm/yyyy": "dmy",
    }
    v = aliases.get(v, v)
    if v not in {"default", "ymd", "dmy"}:
        raise HTTPException(status_code=400, detail="Invalid date_format")
    return v


# Default Visualisation page state (mode + date range + optional sunburst focus)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_default_visualisation(raw: dict | None) -> dict | None:
    """Validate/normalize the user's saved default visualisation state.

    Expected shape (all fields optional):
      {
        "mode": "graph"|"heatmap"|"sunburst_source"|"sunburst_control"|"histogram",
        "date_range": {"days": 7} OR {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"},
        "sunburst_focus": {"source_key": "..."} OR {"control_id": "..."},
      }

    Returning None means "no default set".
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=400, detail="default_visualisation must be an object"
        )

    mode = _validate_viz_mode(raw.get("mode"))
    out: dict = {"mode": mode}

    # Date range
    dr = raw.get("date_range")
    if dr is not None:
        if not isinstance(dr, dict):
            raise HTTPException(
                status_code=400,
                detail="default_visualisation.date_range must be an object",
            )

        days = dr.get("days")
        if days is not None and str(days).strip() != "":
            try:
                d = int(days)
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid default_visualisation.date_range.days",
                )
            if d < 1 or d > 3650:
                raise HTTPException(
                    status_code=400,
                    detail="default_visualisation.date_range.days out of range",
                )
            out["date_range"] = {"days": d}
        else:
            start = (dr.get("start_date") or dr.get("from") or "").strip()
            end = (dr.get("end_date") or dr.get("to") or "").strip()
            if start or end:
                if not (_ISO_DATE_RE.match(start) and _ISO_DATE_RE.match(end)):
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid default_visualisation.date_range start/end",
                    )
                if start > end:
                    start, end = end, start
                out["date_range"] = {"start_date": start, "end_date": end}

    # Sunburst focus (top-level segment)
    sf = raw.get("sunburst_focus")
    if sf is not None:
        if not isinstance(sf, dict):
            raise HTTPException(
                status_code=400,
                detail="default_visualisation.sunburst_focus must be an object",
            )
        source_key = (sf.get("source_key") or "").strip()
        control_id = (sf.get("control_id") or "").strip()
        if source_key and control_id:
            raise HTTPException(
                status_code=400,
                detail="default_visualisation.sunburst_focus must specify one of source_key or control_id",
            )
        if source_key:
            if len(source_key) > 128:
                raise HTTPException(
                    status_code=400,
                    detail="default_visualisation.sunburst_focus.source_key too long",
                )
            out["sunburst_focus"] = {"source_key": source_key}
        elif control_id:
            u = _try_uuid(control_id)
            if not u:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid default_visualisation.sunburst_focus.control_id",
                )
            out["sunburst_focus"] = {"control_id": str(u)}

    return out


def _validate_landing_page(raw: str | None, *, is_admin: bool) -> str:
    """Validate a landing page path. Blank/None resets to default ('/')."""
    v = (raw or "").strip()
    if not v:
        return "/"

    parts = urlsplit(v)
    # Only allow same-origin relative/absolute paths (no scheme or netloc).
    if parts.scheme or parts.netloc:
        raise HTTPException(status_code=400, detail="Invalid landing_page")

    path = (parts.path or "").strip() or "/"
    if not path.startswith("/"):
        path = "/" + path
    if path.startswith("//"):
        raise HTTPException(status_code=400, detail="Invalid landing_page")

    allowed = {
        "/",
        "/controls.html",
        "/events.html",
        "/sources.html",
        "/account.html",
        "/my-questions.html",
        "/risks.html",
        "/pestle.html",
        "/pestle_item.html",
        "/interested_parties.html",
        "/interested_party.html",
        "/statement-of-applicability.html",
        "/isms.html",
    }
    if is_admin:
        allowed.add("/admin.html")

    if path not in allowed:
        raise HTTPException(status_code=400, detail="Invalid landing_page")

    # Normalize query: drop pagination-like params so landing is stable.
    pairs: list[tuple[str, str]] = []
    for k, val in parse_qsl(parts.query or "", keep_blank_values=False):
        if not k:
            continue
        if k == "offset":
            continue
        if val is None:
            continue
        vv = str(val)
        if vv == "":
            continue
        pairs.append((k, vv))

    query = urlencode(pairs, doseq=True)
    out = path + (("?" + query) if query else "")

    # Allow long URLs (saved searches), but keep within reasonable bounds.
    if len(out) > 2048:
        raise HTTPException(status_code=400, detail="landing_page is too long")
    return out


def _validate_default_framework(raw: str | None) -> str | None:
    """Validate a user-selected default framework slug.

    None/blank clears the preference so the app-level default applies.
    """

    v = (raw or "").strip()
    if not v:
        return None
    if len(v) > 64:
        raise HTTPException(status_code=400, detail="default_framework is too long")
    if not re.match(r"^[A-Za-z0-9._:-]+$", v):
        raise HTTPException(
            status_code=400,
            detail="default_framework may contain only letters, numbers, dot, underscore, colon and hyphen",
        )
    return v


@router.get("/v1/me")
def me(request: Request, db: Session = Depends(get_db)) -> dict:
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")

    footer = (settings.sample_pdf_footer or "").strip()
    header = (getattr(settings, "sample_pdf_header", "") or "").strip()
    fname_prefix = (getattr(settings, "evidence_filename_prefix", "") or "").strip()

    prefs = PreferencesOut(
        use_local_timezone=bool(getattr(user, "pref_use_local_timezone", False)),
        timezone=getattr(user, "pref_timezone", None),
        theme=(getattr(user, "pref_theme", None) or "purple"),
        date_format=_validate_date_format(getattr(user, "pref_date_format", None)),
        auto_apply_filters=bool(getattr(user, "pref_auto_apply_filters", False)),
        source_colors=(getattr(user, "pref_source_colors", None) or {}),
        default_visualisation=(
            getattr(user, "pref_default_visualisation", None) or None
        ),
        viz_mode=(getattr(user, "pref_viz_mode", None) or "graph"),
        landing_page=(getattr(user, "pref_landing_page", None) or "/"),
        default_framework=(getattr(user, "pref_default_framework", None) or None),
    )

    # Use effective role (accounts for group role inheritance). In most requests
    # this will already be attached by middleware, but computing here ensures
    # /v1/me remains correct even if called outside that path.
    eff_role = (
        getattr(user, "effective_role", None)
        or compute_effective_role(db, user)
        or user.role
    )
    try:
        setattr(user, "effective_role", eff_role)
    except Exception:
        pass
    is_admin = (eff_role or "") == ROLE_ADMIN
    can_audit_trail = bool(is_admin or has_permission(db, user, "audittrail.read"))
    can_manage_audits = bool(is_admin or has_permission(db, user, "audits.manage"))
    can_view_audits = bool(can_manage_audits or has_permission(db, user, "audits.read"))
    can_manage_risks = bool(is_admin or has_permission(db, user, "risk.manage"))
    can_view_risks = bool(can_manage_risks or has_permission(db, user, "risk.read"))
    can_manage_pestle = bool(
        is_admin or has_permission(db, user, "pestle.manage") or can_manage_risks
    )
    can_view_pestle = bool(
        can_manage_pestle or has_permission(db, user, "pestle.read") or can_view_risks
    )
    can_manage_interested_parties = bool(
        is_admin
        or has_permission(db, user, "interested_parties.manage")
        or can_manage_risks
    )
    can_view_interested_parties = bool(
        can_manage_interested_parties
        or has_permission(db, user, "interested_parties.read")
        or can_view_risks
    )
    can_manage_isms = bool(
        is_admin or has_permission(db, user, "isms.manage") or can_manage_risks
    )
    can_view_isms = bool(
        can_manage_isms or has_permission(db, user, "isms.read") or can_view_risks
    )
    can_view_events = bool(is_admin or has_permission(db, user, "events.read"))
    can_create_questions = bool(is_admin or has_permission(db, user, "question.create"))
    can_question_events = can_create_questions
    can_delete_questions = bool(is_admin or has_permission(db, user, "question.delete"))
    incident_webhook_enabled = bool(
        (getattr(settings, "incident_webhook_url", "") or "").strip()
    )
    can_create_incidents = bool(
        incident_webhook_enabled
        and (is_admin or has_permission(db, user, "incident.create"))
    )
    can_delete_incidents = bool(is_admin or has_permission(db, user, "incident.delete"))

    unread_question_replies_count = int(
        db.query(EventQuestionThread)
        .filter(EventQuestionThread.created_by_user_id == user.id)
        .filter(EventQuestionThread.last_admin_reply_at.isnot(None))
        .filter(
            (EventQuestionThread.author_last_seen_at.is_(None))
            | (
                EventQuestionThread.author_last_seen_at
                < EventQuestionThread.last_admin_reply_at
            )
        )
        .count()
    )

    oidc_enabled = bool(getattr(settings, "oidc_enabled", False))
    trust_remote = (
        bool(getattr(settings, "trust_remote_user", False)) and not oidc_enabled
    )
    local_auth_enabled = bool(getattr(settings, "local_auth_enabled", True))
    upstream_logout_url = (
        getattr(settings, "upstream_logout_url", "") or ""
    ).strip() or None
    logout_url = None
    if oidc_enabled:
        logout_url = "/api/v1/auth/oidc/logout"
    elif trust_remote and upstream_logout_url:
        logout_url = upstream_logout_url

    return {
        "user": user.username,
        "role": user.role,
        "effective_role": eff_role,
        "is_admin": is_admin,
        "can_audit_trail": can_audit_trail,
        "can_view_audits": can_view_audits,
        "can_manage_audits": can_manage_audits,
        "can_view_risks": can_view_risks,
        "can_manage_risks": can_manage_risks,
        "can_view_pestle": can_view_pestle,
        "can_manage_pestle": can_manage_pestle,
        "can_view_interested_parties": can_view_interested_parties,
        "can_manage_interested_parties": can_manage_interested_parties,
        "can_view_isms": can_view_isms,
        "can_manage_isms": can_manage_isms,
        "can_view_events": can_view_events,
        "can_create_questions": can_create_questions,
        "can_question_events": can_question_events,
        "can_delete_questions": can_delete_questions,
        "incident_webhook_enabled": incident_webhook_enabled,
        "can_create_incidents": can_create_incidents,
        "can_delete_incidents": can_delete_incidents,
        "unread_question_replies_count": unread_question_replies_count,
        "password_change_enabled": bool(
            local_auth_enabled and not trust_remote and not oidc_enabled
        ),
        # In native OIDC mode, logout_url clears KEEN's session and then starts
        # RP-initiated logout when the provider end-session endpoint is configured.
        "logout_enabled": bool((not trust_remote) or logout_url),
        "logout_url": logout_url,
        "sample_pdf_header": header or None,
        "sample_pdf_footer": footer or None,
        "evidence_filename_prefix": fname_prefix or None,
        "event_data_masking": settings.event_data_masking,
        "default_timezone": (settings.timezone or "Etc/UTC"),
        "default_date_format": _validate_date_format(settings.ui_date_format),
        "preferences": prefs.model_dump(),
        "available_themes": [{"id": t[0], "name": t[1]} for t in ALLOWED_THEMES],
    }


@router.get("/v1/me/preferences", response_model=PreferencesOut)
def get_my_preferences(request: Request) -> PreferencesOut:
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")

    return PreferencesOut(
        use_local_timezone=bool(getattr(user, "pref_use_local_timezone", False)),
        timezone=getattr(user, "pref_timezone", None),
        theme=(getattr(user, "pref_theme", None) or "purple"),
        date_format=_validate_date_format(getattr(user, "pref_date_format", None)),
        auto_apply_filters=bool(getattr(user, "pref_auto_apply_filters", False)),
        source_colors=(getattr(user, "pref_source_colors", None) or {}),
        default_visualisation=(
            getattr(user, "pref_default_visualisation", None) or None
        ),
        viz_mode=(getattr(user, "pref_viz_mode", None) or "graph"),
        landing_page=(getattr(user, "pref_landing_page", None) or "/"),
        default_framework=(getattr(user, "pref_default_framework", None) or None),
    )


@router.patch("/v1/me/preferences", response_model=PreferencesOut)
def update_my_preferences(
    payload: PreferencesUpdatePayload, request: Request, db: Session = Depends(get_db)
) -> PreferencesOut:
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Apply updates (PATCH semantics: only apply fields present in the payload).
    fields = set(payload.model_fields_set or set())

    if "use_local_timezone" in fields:
        user.pref_use_local_timezone = bool(payload.use_local_timezone)

    if "timezone" in fields:
        user.pref_timezone = _validate_timezone_name(payload.timezone)

    if "theme" in fields:
        theme = _validate_theme_id(payload.theme)
        # Blank/None is treated as "no change"; caller can always set "purple" explicitly.
        if theme:
            user.pref_theme = theme

    if "date_format" in fields:
        user.pref_date_format = _validate_date_format(payload.date_format)

    if "auto_apply_filters" in fields:
        user.pref_auto_apply_filters = bool(payload.auto_apply_filters)

    if "source_colors" in fields:
        # Treat null as "clear overrides". Otherwise replace the whole mapping.
        if payload.source_colors is None:
            user.pref_source_colors = {}
        else:
            user.pref_source_colors = _normalize_source_colors(payload.source_colors)

    if "viz_mode" in fields:
        user.pref_viz_mode = _validate_viz_mode(payload.viz_mode)

    if "default_visualisation" in fields:
        # None clears the saved default. Any dict is validated/normalized.
        user.pref_default_visualisation = _validate_default_visualisation(
            payload.default_visualisation
        )

        # Keep the legacy mode-only preference in sync when a default is set.
        dv = user.pref_default_visualisation
        if isinstance(dv, dict):
            m = dv.get("mode")
            if isinstance(m, str) and m in _VIZ_MODES:
                user.pref_viz_mode = m

    if "landing_page" in fields:
        eff_role = (
            getattr(user, "effective_role", None)
            or compute_effective_role(db, user)
            or user.role
        )
        is_admin = (eff_role or "") == ROLE_ADMIN
        user.pref_landing_page = _validate_landing_page(
            payload.landing_page, is_admin=is_admin
        )

    if "default_framework" in fields:
        user.pref_default_framework = _validate_default_framework(
            payload.default_framework
        )

    db.add(user)
    db.commit()
    db.refresh(user)

    return PreferencesOut(
        use_local_timezone=bool(user.pref_use_local_timezone),
        timezone=user.pref_timezone,
        theme=(user.pref_theme or "purple"),
        date_format=_validate_date_format(getattr(user, "pref_date_format", None)),
        auto_apply_filters=bool(getattr(user, "pref_auto_apply_filters", False)),
        source_colors=(getattr(user, "pref_source_colors", None) or {}),
        default_visualisation=(
            getattr(user, "pref_default_visualisation", None) or None
        ),
        viz_mode=(getattr(user, "pref_viz_mode", None) or "graph"),
        landing_page=(getattr(user, "pref_landing_page", None) or "/"),
        default_framework=(getattr(user, "pref_default_framework", None) or None),
    )


# -----------------------------------------------------------------------------
# Saved searches
# -----------------------------------------------------------------------------


@router.get("/v1/me/saved-searches", response_model=list[SavedSearchOut])
def list_my_saved_searches(
    request: Request, db: Session = Depends(get_db)
) -> list[SavedSearchOut]:
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")

    rows = (
        db.query(SavedSearch)
        .filter(SavedSearch.user_id == user.id)
        .order_by(desc(SavedSearch.updated_at), SavedSearch.name.asc())
        .all()
    )

    return [
        SavedSearchOut(
            id=r.id,
            name=r.name,
            url=r.url,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.post("/v1/me/saved-searches", response_model=SavedSearchOut)
def upsert_my_saved_search(
    payload: SavedSearchCreatePayload, request: Request, db: Session = Depends(get_db)
) -> SavedSearchOut:
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")

    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if len(name) > 128:
        raise HTTPException(status_code=400, detail="name is too long")

    url = _normalize_saved_search_url(payload.url)

    existing = (
        db.query(SavedSearch)
        .filter(SavedSearch.user_id == user.id)
        .filter(SavedSearch.name == name)
        .first()
    )

    if existing:
        existing.url = url
        existing.updated_at = datetime.utcnow()
        db.add(existing)
        db.commit()
        db.refresh(existing)
        r = existing
    else:
        r = SavedSearch(
            user_id=user.id,
            name=name,
            url=url,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(r)
        db.commit()
        db.refresh(r)

    return SavedSearchOut(
        id=r.id,
        name=r.name,
        url=r.url,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


@router.delete("/v1/me/saved-searches/{saved_search_id}")
def delete_my_saved_search(
    saved_search_id: str, request: Request, db: Session = Depends(get_db)
) -> dict:
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")

    sid = _try_uuid(saved_search_id)
    if not sid:
        raise HTTPException(status_code=400, detail="Invalid id")

    row = (
        db.query(SavedSearch)
        .filter(SavedSearch.id == sid)
        .filter(SavedSearch.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    db.delete(row)
    db.commit()
    return {"ok": True}


# -----------------------------------------------------------------------------
# Shortcuts compatibility endpoints (alias of saved searches)
# -----------------------------------------------------------------------------


@router.get("/v1/me/shortcuts", response_model=list[SavedSearchOut])
def list_my_shortcuts(
    request: Request, db: Session = Depends(get_db)
) -> list[SavedSearchOut]:
    return list_my_saved_searches(request=request, db=db)


@router.post("/v1/me/shortcuts", response_model=SavedSearchOut)
def upsert_my_shortcut(
    payload: SavedSearchCreatePayload, request: Request, db: Session = Depends(get_db)
) -> SavedSearchOut:
    return upsert_my_saved_search(payload=payload, request=request, db=db)


@router.delete("/v1/me/shortcuts/{shortcut_id}")
def delete_my_shortcut(
    shortcut_id: str, request: Request, db: Session = Depends(get_db)
) -> dict:
    return delete_my_saved_search(saved_search_id=shortcut_id, request=request, db=db)


@router.post("/v1/me/password")
def change_my_password(
    payload: ChangePasswordPayload, request: Request, db: Session = Depends(get_db)
) -> dict:
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Rate limit password changes to prevent abuse (fail closed if Redis unavailable)
    try:
        r = get_valkey()
        ip = _client_ip(request) or "unknown"
        window = int(getattr(settings, "login_rate_limit_window_seconds", 300))
        limit = int(getattr(settings, "login_rate_limit_per_ip", 30))
        ok, retry = fixed_window_allow(
            r, f"keen:rl:password_change:ip:{ip}", limit, window, fail_closed=True
        )
        if not ok:
            raise HTTPException(
                status_code=429,
                detail="Too many password change attempts. Please try again later.",
            )
    except HTTPException:
        raise
    except Exception:
        # Fail closed if Redis unavailable
        raise HTTPException(
            status_code=503,
            detail="Service temporarily unavailable. Please try again later.",
        )

    # In remote-user proxy mode, the upstream IdP/proxy is authoritative.
    # Disallow local password changes to avoid confusing users.
    if bool(getattr(settings, "trust_remote_user", False)):
        raise HTTPException(
            status_code=403,
            detail="Password changes are disabled when KEEN_TRUST_REMOTE_USER is enabled",
        )

    if not verify_password(payload.current_password or "", user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    try:
        user.password_hash = hash_password(payload.new_password or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.add(user)
    db.commit()
    return {"ok": True}
