"""Me application operations."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.core.references import try_uuid as _try_uuid
from app.core.source_meta import normalize_hex_color
from app.core.valkey import get_valkey
from app.db.models import EventQuestionThread, SavedSearch, User
from app.schemas import (
    ChangePasswordPayload,
    PreferencesOut,
    PreferencesUpdatePayload,
    SavedSearchCreatePayload,
    SavedSearchOut,
)
from app.security.auth import ROLE_ADMIN
from app.security.passwords import hash_password, verify_password
from app.security.permissions import has_permission
from app.security.rate_limit import fixed_window_allow
from app.security.roles import compute_effective_role, is_effective_admin
from app.services.context import ChangeContext
from app.services.errors import DomainError
from app.services.features import state
from app.services.presentation import ALLOWED_THEMES
from app.services.presentation import (
    normalize_saved_search_url as _normalize_saved_search_url,
)
from app.services.presentation import validate_theme_id as _validate_theme_id
from app.services.presentation import validate_timezone_name as _validate_timezone_name

_VIZ_MODES = {"graph", "heatmap", "sunburst_source", "sunburst_control", "histogram"}
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalize_source_colors(raw: dict[str, str]) -> dict[str, str]:
    """Validate/normalize a user-supplied source->hex mapping; the request
    schema has already established that it is a mapping of strings."""
    if len(raw) > 500:
        raise DomainError(status_code=400, detail="source_colors is too large")
    out: dict[str, str] = {}
    for k, v in raw.items():
        key = (str(k) if k is not None else "").strip()
        if not key:
            continue
        if len(key) > 128:
            raise DomainError(status_code=400, detail="source_colors key is too long")
        col = normalize_hex_color(str(v) if v is not None else None)
        if not col:
            raise DomainError(
                status_code=400, detail="Invalid hex color for source: " + key
            )
        out[key] = col
    return out


def _validate_visualisation_mode(raw: str | None) -> str:
    """Validate a visualisation mode. Blank/None means the default graph."""
    v = (raw or "").strip()
    if not v:
        return "graph"
    if v not in _VIZ_MODES:
        raise DomainError(status_code=400, detail="Invalid default_visualisation.mode")
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
        raise DomainError(status_code=400, detail="Invalid date_format")
    return v


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

    mode = _validate_visualisation_mode(raw.get("mode"))
    out: dict = {"mode": mode}

    # Date range
    dr = raw.get("date_range")
    if dr is not None:
        if not isinstance(dr, dict):
            raise DomainError(
                status_code=400,
                detail="default_visualisation.date_range must be an object",
            )

        days = dr.get("days")
        if days is not None and str(days).strip() != "":
            try:
                d = int(days)
            except Exception:
                raise DomainError(
                    status_code=400,
                    detail="Invalid default_visualisation.date_range.days",
                )
            if d < 1 or d > 3650:
                raise DomainError(
                    status_code=400,
                    detail="default_visualisation.date_range.days out of range",
                )
            out["date_range"] = {"days": d}
        else:
            start = (dr.get("start_date") or dr.get("from") or "").strip()
            end = (dr.get("end_date") or dr.get("to") or "").strip()
            if start or end:
                if not (_ISO_DATE_RE.match(start) and _ISO_DATE_RE.match(end)):
                    raise DomainError(
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
            raise DomainError(
                status_code=400,
                detail="default_visualisation.sunburst_focus must be an object",
            )
        source_key = (sf.get("source_key") or "").strip()
        control_id = (sf.get("control_id") or "").strip()
        if source_key and control_id:
            raise DomainError(
                status_code=400,
                detail="default_visualisation.sunburst_focus must specify one of source_key or control_id",
            )
        if source_key:
            if len(source_key) > 128:
                raise DomainError(
                    status_code=400,
                    detail="default_visualisation.sunburst_focus.source_key too long",
                )
            out["sunburst_focus"] = {"source_key": source_key}
        elif control_id:
            u = _try_uuid(control_id)
            if not u:
                raise DomainError(
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
        raise DomainError(status_code=400, detail="Invalid landing_page")

    path = (parts.path or "").strip() or "/"
    if not path.startswith("/"):
        path = "/" + path
    if path.startswith("//"):
        raise DomainError(status_code=400, detail="Invalid landing_page")

    allowed = {
        "/",
        "/controls.html",
        "/events.html",
        "/sources.html",
        "/account.html",
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
        raise DomainError(status_code=400, detail="Invalid landing_page")

    # Normalize query: drop pagination-like params so landing is stable.
    pairs: list[tuple[str, str]] = []
    for k, val in parse_qsl(parts.query or "", keep_blank_values=False):
        if not k:
            continue
        if k == "offset":
            continue
        # Blank values are dropped by parse_qsl itself.
        pairs.append((k, val))

    query = urlencode(pairs, doseq=True)
    out = path + (("?" + query) if query else "")

    # Allow long URLs (saved searches), but keep within reasonable bounds.
    if len(out) > 2048:
        raise DomainError(status_code=400, detail="landing_page is too long")
    return out


def _validate_default_framework(raw: str | None) -> str | None:
    """Validate a user-selected default framework slug.

    None/blank clears the preference so the app-level default applies.
    """

    v = (raw or "").strip()
    if not v:
        return None
    if len(v) > 64:
        raise DomainError(status_code=400, detail="default_framework is too long")
    if not re.match(r"^[A-Za-z0-9._:-]+$", v):
        raise DomainError(
            status_code=400,
            detail="default_framework may contain only letters, numbers, dot, underscore, colon and hyphen",
        )
    return v


def _preferences_out(user: User) -> PreferencesOut:
    return PreferencesOut(
        use_local_timezone=user.pref_use_local_timezone,
        timezone=user.pref_timezone,
        theme=user.pref_theme or "ink",
        date_format=_validate_date_format(user.pref_date_format),
        auto_apply_filters=user.pref_auto_apply_filters,
        source_colors=user.pref_source_colors or {},
        default_visualisation=user.pref_default_visualisation or None,
        landing_page=user.pref_landing_page or "/",
        default_framework=user.pref_default_framework or None,
    )


def _capabilities(db: Session, user: User, *, is_admin: bool) -> dict[str, bool]:
    """Navigation and page capabilities. API routes enforce permissions separately."""

    def allowed(code: str) -> bool:
        return is_admin or has_permission(db, user, code)

    admin_audits = allowed("audits.admin")
    manage_audits = admin_audits or allowed("audits.manage")
    manage_risks = allowed("risk.manage")
    view_risks = manage_risks or allowed("risk.read")
    manage_pestle = manage_risks or allowed("pestle.manage")
    manage_parties = manage_risks or allowed("interested_parties.manage")
    manage_isms = manage_risks or allowed("isms.manage")
    admin_stories = allowed("stories.admin")
    manage_stories = admin_stories or allowed("stories.manage")
    manage_quizzes = allowed("quiz.manage")
    create_questions = allowed("question.create")
    incident_webhook_enabled = bool(settings.incident_webhook_url.strip())
    return {
        "can_audit_trail": allowed("audittrail.read"),
        "can_view_audits": manage_audits or allowed("audits.read"),
        "can_manage_audits": manage_audits,
        "can_admin_audits": admin_audits,
        "can_sample_isms": allowed("isms.read"),
        "can_view_risks": view_risks,
        "can_manage_risks": manage_risks,
        "can_view_pestle": manage_pestle or view_risks or allowed("pestle.read"),
        "can_manage_pestle": manage_pestle,
        "can_view_interested_parties": manage_parties
        or view_risks
        or allowed("interested_parties.read"),
        "can_manage_interested_parties": manage_parties,
        "can_view_isms": manage_isms or view_risks or allowed("isms.read"),
        "can_manage_isms": manage_isms,
        "can_view_events": allowed("events.read"),
        "can_view_stories": manage_stories or allowed("stories.read"),
        "can_manage_stories": manage_stories,
        "can_admin_stories": admin_stories,
        "can_view_quizzes": manage_quizzes or allowed("quiz.read"),
        "can_manage_quizzes": manage_quizzes,
        "can_create_questions": create_questions,
        "can_question_events": create_questions,
        "can_delete_questions": allowed("question.delete"),
        "incident_webhook_enabled": incident_webhook_enabled,
        "can_create_incidents": incident_webhook_enabled and allowed("incident.create"),
        "can_delete_incidents": allowed("incident.delete"),
    }


# Capabilities hidden when their feature is switched off for this installation.
_FEATURE_CAPABILITIES = {
    "audits": ("can_view_audits", "can_manage_audits", "can_admin_audits"),
    "risks": ("can_view_risks", "can_manage_risks"),
    "pestle": ("can_view_pestle", "can_manage_pestle"),
    "interested_parties": (
        "can_view_interested_parties",
        "can_manage_interested_parties",
    ),
    "isms": ("can_view_isms", "can_manage_isms"),
    "evidence": ("can_view_events",),
    "stories": ("can_view_stories", "can_manage_stories", "can_admin_stories"),
    "quizzes": ("can_view_quizzes", "can_manage_quizzes"),
    "questions": (
        "can_create_questions",
        "can_question_events",
        "can_delete_questions",
    ),
    "incidents": ("can_create_incidents", "can_delete_incidents"),
}


def _unread_question_replies(db: Session, user: User) -> int:
    return (
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


def me(*, context: ChangeContext, db: Session) -> dict:
    user = context.actor
    if not isinstance(user, User):
        raise DomainError(status_code=401, detail="Not authenticated")

    # Middleware normally attaches the effective role (which accounts for group
    # role inheritance); compute it when this runs outside that path.
    if not user.effective_role:
        user.effective_role = compute_effective_role(db, user)
    is_admin = user.effective_role == ROLE_ADMIN

    features = state(db)
    capabilities = _capabilities(db, user, is_admin=is_admin)
    for feature, keys in _FEATURE_CAPABILITIES.items():
        if not features.enabled(feature):
            capabilities.update(dict.fromkeys(keys, False))

    oidc_enabled = settings.oidc_enabled
    trust_remote = settings.trust_remote_user and not oidc_enabled
    logout_url = None
    if oidc_enabled:
        logout_url = "/api/v1/auth/oidc/logout"
    elif trust_remote:
        logout_url = settings.upstream_logout_url.strip() or None

    return {
        "features": features.public(),
        "user": user.username,
        "user_id": str(user.id),
        "role": user.role,
        "effective_role": user.effective_role,
        "is_admin": is_admin,
        **capabilities,
        "unread_question_replies_count": _unread_question_replies(db, user)
        if features.enabled("questions")
        else 0,
        "password_change_enabled": bool(
            settings.local_auth_enabled
            and user.auth_backend != "ldap"
            and not trust_remote
            and not oidc_enabled
        ),
        # In native OIDC mode, logout_url clears Keen's session and then starts
        # RP-initiated logout when the provider end-session endpoint is configured.
        "logout_enabled": bool((not trust_remote) or logout_url),
        "logout_url": logout_url,
        "sample_pdf_header": settings.sample_pdf_header.strip() or None,
        "sample_pdf_footer": settings.sample_pdf_footer.strip() or None,
        "evidence_filename_prefix": settings.evidence_filename_prefix.strip() or None,
        "event_data_masking": settings.event_data_masking,
        "default_timezone": settings.timezone or "Etc/UTC",
        "default_date_format": _validate_date_format(settings.ui_date_format),
        "preferences": _preferences_out(user).model_dump(),
        "available_themes": [{"id": t[0], "name": t[1]} for t in ALLOWED_THEMES],
        "bookstack_enabled": settings.bookstack_enabled,
    }


def get_my_preferences(*, context: ChangeContext) -> PreferencesOut:
    user = context.actor
    if not isinstance(user, User):
        raise DomainError(status_code=401, detail="Not authenticated")

    return _preferences_out(user)


def update_my_preferences(
    *, payload: PreferencesUpdatePayload, context: ChangeContext, db: Session
) -> PreferencesOut:
    user = context.actor
    if not isinstance(user, User):
        raise DomainError(status_code=401, detail="Not authenticated")

    # Apply updates (PATCH semantics: only apply fields present in the payload).
    fields = set(payload.model_fields_set or set())

    if "use_local_timezone" in fields:
        user.pref_use_local_timezone = bool(payload.use_local_timezone)

    if "timezone" in fields:
        user.pref_timezone = _validate_timezone_name(payload.timezone)

    if "theme" in fields:
        theme = _validate_theme_id(payload.theme)
        # Blank/None is treated as "no change"; caller can always set "ink" explicitly.
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

    if "default_visualisation" in fields:
        # None clears the saved default. Any dict is validated/normalized.
        user.pref_default_visualisation = _validate_default_visualisation(
            payload.default_visualisation
        )

    if "landing_page" in fields:
        user.pref_landing_page = _validate_landing_page(
            payload.landing_page, is_admin=is_effective_admin(db, user)
        )

    if "default_framework" in fields:
        user.pref_default_framework = _validate_default_framework(
            payload.default_framework
        )

    db.add(user)
    db.commit()
    db.refresh(user)

    return _preferences_out(user)


def list_my_saved_searches(
    *, context: ChangeContext, db: Session
) -> list[SavedSearchOut]:
    user = context.actor
    if not isinstance(user, User):
        raise DomainError(status_code=401, detail="Not authenticated")

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


def upsert_my_saved_search(
    *, payload: SavedSearchCreatePayload, context: ChangeContext, db: Session
) -> SavedSearchOut:
    user = context.actor
    if not isinstance(user, User):
        raise DomainError(status_code=401, detail="Not authenticated")

    name = (payload.name or "").strip()
    if not name:
        raise DomainError(status_code=400, detail="name is required")

    url = _normalize_saved_search_url(payload.url)

    existing = (
        db.query(SavedSearch)
        .filter(SavedSearch.user_id == user.id)
        .filter(SavedSearch.name == name)
        .first()
    )

    if existing:
        existing.url = url
        existing.updated_at = utc_now_naive()
        db.add(existing)
        db.commit()
        db.refresh(existing)
        r = existing
    else:
        r = SavedSearch(
            user_id=user.id,
            name=name,
            url=url,
            created_at=utc_now_naive(),
            updated_at=utc_now_naive(),
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


def delete_my_saved_search(
    *, saved_search_id: str, context: ChangeContext, db: Session
) -> dict:
    user = context.actor
    if not isinstance(user, User):
        raise DomainError(status_code=401, detail="Not authenticated")

    sid = _try_uuid(saved_search_id)
    if not sid:
        raise DomainError(status_code=400, detail="Invalid id")

    row = (
        db.query(SavedSearch)
        .filter(SavedSearch.id == sid)
        .filter(SavedSearch.user_id == user.id)
        .first()
    )
    if not row:
        raise DomainError(status_code=404, detail="Not found")

    db.delete(row)
    db.commit()
    return {"ok": True}


def change_my_password(
    *, payload: ChangePasswordPayload, context: ChangeContext, db: Session
) -> dict:
    user = context.actor
    if not isinstance(user, User):
        raise DomainError(status_code=401, detail="Not authenticated")

    # Rate limit password changes to prevent abuse (fail closed if Redis unavailable)
    try:
        r = get_valkey()
        ip = context.client_ip or "unknown"
        window = settings.login_rate_limit_window_seconds
        limit = settings.login_rate_limit_per_ip
        ok, retry = fixed_window_allow(
            r, f"keen:rl:password_change:ip:{ip}", limit, window, fail_closed=True
        )
        # A session holder must not be able to test many current passwords by
        # spreading attempts across addresses.
        per_user = settings.login_rate_limit_per_username
        if ok and per_user > 0:
            ok, retry = fixed_window_allow(
                r,
                f"keen:rl:password_change:user:{user.id}",
                per_user,
                window,
                fail_closed=True,
            )
        if not ok:
            raise DomainError(
                status_code=429,
                detail="Too many password change attempts. Please try again later.",
            )
    except DomainError:
        raise
    except Exception:
        # Fail closed if Redis unavailable
        raise DomainError(
            status_code=503,
            detail="Service temporarily unavailable. Please try again later.",
        )

    if user.auth_backend == "ldap":
        raise DomainError(403, "Change your password in your LDAP directory")

    # In remote-user proxy mode, the upstream IdP/proxy is authoritative.
    # Disallow local password changes to avoid confusing users.
    if settings.trust_remote_user:
        raise DomainError(
            status_code=403,
            detail="Password changes are disabled when KEEN_TRUST_REMOTE_USER is enabled",
        )

    user = (
        db.query(User)
        .filter(User.id == user.id)
        .populate_existing()
        .with_for_update()
        .one()
    )
    if not verify_password(payload.current_password or "", user.password_hash):
        raise DomainError(status_code=400, detail="Current password is incorrect")

    try:
        user.password_hash = hash_password(payload.new_password or "")
        user.mfa_version += 1
    except ValueError as e:
        raise DomainError(status_code=400, detail=str(e))

    from app.services.security_notifications import enqueue

    enqueue(db, user, "password-changed", ip=context.client_ip)
    db.add(user)
    db.commit()
    return {"ok": True}
