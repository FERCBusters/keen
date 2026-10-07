"""Self-service, proof-of-ownership addresses for automatic SSO linking."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.valkey import get_valkey
from app.db.session import get_db
from app.db.models import User, UserSsoEmail, SsoEmailChallenge
from app.security.auth import require_authenticated
from app.security.sessions import get_session
from app.security.rate_limit import fixed_window_allow
from app.services import mailer

router = APIRouter()

class Address(BaseModel):
    email: str = Field(min_length=3, max_length=256)

class Proof(BaseModel):
    token: str = Field(min_length=20, max_length=128)


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def clean_email(value):
    from .users import _clean_email
    email = _clean_email(value)
    if not email or email.count('@') != 1 or any(c.isspace() or c in '<>,;:"\\' for c in email):
        raise HTTPException(400, 'Invalid email address')
    return email.lower()


def lock_user(db, user):
    current = db.query(User).filter(User.id == user.id).populate_existing().with_for_update().one()
    if not current.is_active:
        raise HTTPException(403, 'Account is inactive')
    return current


def recent_signin(request, user):
    session = get_session(get_valkey(), request.cookies.get(settings.session_cookie_name, '')) or {}
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(session['created_at'])).total_seconds()
        valid = session.get('user_id') == str(user.id) and 0 <= age <= 300
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise HTTPException(403, 'Sign out and sign in again, then manage SSO email addresses within five minutes')


def limit(user):
    allowed, _ = fixed_window_allow(get_valkey(), f'keen:rl:sso-email:{user.id}', 5, 3600, fail_closed=True)
    if not allowed:
        raise HTTPException(429, 'Too many verification requests; try again later')


def available(db, user, email):
    address = db.get(UserSsoEmail, email)
    other_primary = db.query(User.id).filter(User.id != user.id, func.lower(func.trim(User.email)) == email).first()
    if (address and address.user_id != user.id) or other_primary:
        raise HTTPException(409, 'This address cannot be linked to this account')
    return address


@router.get('/v1/me/sso-emails')
def list_addresses(user=Depends(require_authenticated), db: Session=Depends(get_db)):
    return {'smtp_configured': mailer.smtp_configured(), 'primary_email': user.email,
            'addresses': [{'email': a.email, 'verified_at': a.verified_at.isoformat()} for a in
                          db.query(UserSsoEmail).filter(UserSsoEmail.user_id == user.id).order_by(UserSsoEmail.email).all()]}


@router.post('/v1/me/sso-emails/request')
def request_address(payload: Address, request: Request, user=Depends(require_authenticated), db: Session=Depends(get_db)):
    if not mailer.smtp_configured():
        raise HTTPException(503, 'SMTP must be configured before verifying SSO email addresses')
    base = (settings.public_base_url or '').rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(503, 'An HTTPS KEEN_PUBLIC_BASE_URL is required for verification emails')
    recent_signin(request, user)
    limit(user)
    user = lock_user(db, user)
    email = clean_email(payload.email)
    if available(db, user, email):
        return {'ok': True, 'already_verified': True}
    if db.query(UserSsoEmail).filter(UserSsoEmail.user_id == user.id).count() >= 10:
        raise HTTPException(400, 'Remove an address before adding more than ten SSO email addresses')
    token = secrets.token_urlsafe(32)
    # One outstanding link per account; requesting another invalidates the old link.
    db.query(SsoEmailChallenge).filter(SsoEmailChallenge.user_id == user.id).delete(synchronize_session=False)
    db.add(SsoEmailChallenge(token_hash=digest(token), user_id=user.id, email=email,
                            expires_at=now()+timedelta(minutes=30), mfa_version=user.mfa_version,
                            password_digest=digest(user.password_hash)))
    db.flush()
    try:
        mailer.send_email(to_email=email, subject='Verify your KEEN SSO email address',
            body=f'You requested to add {email} to KEEN account {user.username}.\n\n'
                 f'While signed into that KEEN account, open this link and confirm:\n'
                 f'{base}/sso-email.html#token={token}\n\n'
                 'This link expires in 30 minutes and works once. If you did not request it, ignore this email.')
    except Exception:
        db.rollback()
        raise HTTPException(503, 'Verification email could not be sent; try again later') from None
    db.commit()
    return {'ok': True}


@router.post('/v1/me/sso-emails/confirm')
def confirm_address(payload: Proof, request: Request, user=Depends(require_authenticated), db: Session=Depends(get_db)):
    user = lock_user(db, user)
    row = db.query(SsoEmailChallenge).filter(SsoEmailChallenge.token_hash == digest(payload.token),
        SsoEmailChallenge.user_id == user.id).with_for_update().one_or_none()
    if not row or row.expires_at <= now() or row.mfa_version != user.mfa_version or row.password_digest != digest(user.password_hash):
        raise HTTPException(400, 'Verification link is invalid or expired')
    existing = available(db, user, row.email)
    if not existing:
        db.add(UserSsoEmail(email=row.email, user_id=user.id, verified_at=now()))
    email = row.email
    from app.services.security_notifications import enqueue
    enqueue(db, user, 'sso-email-added', request)
    db.delete(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'This address cannot be linked to this account') from None
    return {'ok': True, 'email': email}


@router.post('/v1/me/sso-emails/remove')
def remove_address(payload: Address, request: Request, user=Depends(require_authenticated), db: Session=Depends(get_db)):
    recent_signin(request, user)
    user = lock_user(db, user)
    email = clean_email(payload.email)
    db.query(SsoEmailChallenge).filter(SsoEmailChallenge.user_id == user.id, SsoEmailChallenge.email == email).delete(synchronize_session=False)
    removed = db.query(UserSsoEmail).filter(UserSsoEmail.user_id == user.id, UserSsoEmail.email == email).delete(synchronize_session=False)
    if removed:
        from app.services.security_notifications import enqueue
        enqueue(db, user, 'sso-email-removed', request)
    db.commit()
    return {'ok': True}
