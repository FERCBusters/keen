"""Session issuance after identity and factor verification."""

from __future__ import annotations

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.core.valkey import get_valkey
from app.security.auth import (
    ROLE_ADMIN,
)
from app.security.permissions import get_effective_permission_codes, has_permission
from app.security.roles import attach_effective_role
from app.security.sessions import create_session, delete_session


def complete_local_login(
    db, user, *, existing_sid, client_ip, mfa_verified=False, verified_version=None
):
    account_session_version = user.session_version
    session_version = user.mfa_version if verified_version is None else verified_version
    # Delete any existing session for this user to prevent session fixation.
    if existing_sid:
        try:
            delete_session(get_valkey(), existing_sid)
        except Exception:
            pass

    # Best-effort last-login timestamp
    try:
        user.last_login_at = utc_now_naive()
        db.add(user)
        db.commit()
    except Exception:
        db.rollback()

    eff = attach_effective_role(db, user)
    permission_codes = get_effective_permission_codes(db, user, use_cache=False)
    authz_version = int(getattr(user, "authz_version", 0) or 0)

    # Create new Valkey-backed session + set cookie.
    # Always create a fresh session ID on login to prevent session fixation attacks.
    sid = create_session(
        get_valkey(),
        user_id=str(user.id),
        ttl_seconds=int(settings.session_ttl_seconds),
        auth_method=user.auth_backend or "local",
        mfa_version=session_version,
        session_version=account_session_version,
        mfa_verified=mfa_verified,
        effective_role=eff,
        permission_codes=permission_codes,
        authz_version=authz_version,
    )
    user.session_id = sid
    user.session_authz_version = authz_version
    user.effective_permission_codes = set(permission_codes)

    from app.services.security_notifications import record_login

    record_login(db, user, ip=client_ip)
    db.commit()

    can_admin_audits = has_permission(db, user, "audits.admin")
    can_manage_audits = can_admin_audits or has_permission(db, user, "audits.manage")
    can_view_audits = can_manage_audits or has_permission(db, user, "audits.read")
    can_manage_risks = has_permission(db, user, "risk.manage")
    can_view_risks = can_manage_risks or has_permission(db, user, "risk.read")
    can_manage_interested_parties = bool(
        has_permission(db, user, "interested_parties.manage") or can_manage_risks
    )
    can_view_interested_parties = bool(
        can_manage_interested_parties
        or has_permission(db, user, "interested_parties.read")
        or can_view_risks
    )
    can_manage_isms = bool(has_permission(db, user, "isms.manage") or can_manage_risks)
    can_view_isms = bool(
        can_manage_isms or has_permission(db, user, "isms.read") or can_view_risks
    )
    can_view_events = bool(has_permission(db, user, "events.read"))
    can_create_questions = bool(has_permission(db, user, "question.create"))
    can_delete_questions = bool(has_permission(db, user, "question.delete"))
    incident_webhook_enabled = bool(
        (getattr(settings, "incident_webhook_url", "") or "").strip()
    )
    can_create_incidents = bool(
        incident_webhook_enabled and has_permission(db, user, "incident.create")
    )
    can_delete_incidents = bool(has_permission(db, user, "incident.delete"))

    return sid, {
        "user": user.username,
        "user_id": str(user.id),
        "role": user.role,
        "effective_role": eff,
        "is_admin": (eff or "") == ROLE_ADMIN,
        "can_view_audits": can_view_audits,
        "can_manage_audits": can_manage_audits,
        "can_admin_audits": can_admin_audits,
        "can_sample_isms": has_permission(db, user, "isms.read"),
        "can_view_risks": can_view_risks,
        "can_manage_risks": can_manage_risks,
        "can_view_interested_parties": can_view_interested_parties,
        "can_manage_interested_parties": can_manage_interested_parties,
        "can_view_isms": can_view_isms,
        "can_manage_isms": can_manage_isms,
        "can_view_events": can_view_events,
        "can_create_questions": can_create_questions,
        "can_question_events": can_create_questions,
        "can_delete_questions": can_delete_questions,
        "incident_webhook_enabled": incident_webhook_enabled,
        "can_create_incidents": can_create_incidents,
        "can_delete_incidents": can_delete_incidents,
        "sample_pdf_footer": (settings.sample_pdf_footer or "").strip() or None,
        "event_data_masking": settings.event_data_masking,
    }


def complete_sso_login(db, user, *, provider, id_token, existing_sid, client_ip):
    account_session_version = user.session_version
    eff = attach_effective_role(db, user)
    permission_codes = get_effective_permission_codes(db, user, use_cache=False)
    authz_version = int(getattr(user, "authz_version", 0) or 0)
    if existing_sid:
        delete_session(get_valkey(), existing_sid)
    sid = create_session(
        get_valkey(),
        user_id=str(user.id),
        ttl_seconds=int(settings.session_ttl_seconds),
        auth_method=provider.key,
        session_version=account_session_version,
        oidc_id_token=id_token,
        effective_role=eff,
        permission_codes=permission_codes,
        authz_version=authz_version,
    )

    from app.services.security_notifications import record_login

    record_login(db, user, ip=client_ip)
    db.commit()

    return sid
