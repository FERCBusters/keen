"""Transactional security-email outbox and per-user successful-login IP history."""
import ipaddress
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.dialects.postgresql import insert
from app.core.config import settings
from app.db.models import SecurityNotification, UserLoginIP
from app.db.session import SessionLocal
from app.services.mailer import smtp_configured, send_email

log = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


from app.security.client_ip import normalise_ip, request_ip


def enqueue(db, user, event, request=None, *, ip=None):
    recipient = (user.email or '').strip()
    if not smtp_configured() or not recipient or '\n' in recipient or '\r' in recipient:
        return
    address = ip or request_ip(request) or 'Unavailable (operator action or unknown address)'
    titles = {
        'sso-email-added': 'An SSO email address was verified for your KEEN account',
        'sso-email-removed': 'An SSO email address was removed from your KEEN account',
        'login-ip': 'Sign-in from an unfamiliar IP address',
        'password-changed': 'Your KEEN password was changed',
        'password-reset': 'Your KEEN password was reset by an administrator',
        'factor-enrolled': 'An authenticator was added to your KEEN account',
        'factor-removed': 'An authenticator was removed from your KEEN account',
        'recovery-codes-regenerated': 'Your KEEN recovery codes were replaced',
        'operator-reset': 'Your KEEN two-factor authentication was reset by an operator',
    }
    title = titles[event]
    site = (settings.public_base_url or settings.mfa_origin or '').rstrip('/')
    body = f'{title}.\n\nAccount: {user.username}\nKEEN installation: {site or "Your KEEN installation"}\nTime (UTC): {now().isoformat(timespec="seconds")}Z\nIP address: {address}\n\n'
    if event == 'login-ip':
        body += 'This address has not previously been recorded for a successful sign-in to this account. A VPN, mobile connection or changing ISP address can cause this alert.\n\n'
    body += 'If this was you, no action is needed. If you do not recognise this activity, open your known KEEN address directly and contact your KEEN administrator. Review your password and authenticators.\n'
    db.add(SecurityNotification(user_id=user.id, recipient=recipient, subject='KEEN security: '+title, body=body))


def record_login(db, user, request):
    address = request_ip(request)
    if not address:
        return
    # Unique account/IP key makes parallel sign-ins enqueue only one alert.
    created = db.execute(insert(UserLoginIP).values(user_id=user.id, address=address, first_seen_at=now()).on_conflict_do_nothing().returning(UserLoginIP.address)).scalar_one_or_none()
    if created:
        enqueue(db, user, 'login-ip', request, ip=address)


def deliver_pending(limit=10):
    if not smtp_configured():
        return 0
    sent = 0
    for _ in range(limit):
        with SessionLocal() as db:
            row = db.query(SecurityNotification).filter(SecurityNotification.status == 'pending', SecurityNotification.next_attempt_at <= now()).order_by(SecurityNotification.created_at).with_for_update(skip_locked=True).first()
            if row is None:
                break
            row.attempts += 1
            try:
                send_email(to_email=row.recipient, subject=row.subject, body=row.body)
            except Exception as error:
                # Exception messages may expose SMTP credentials/addresses; retain only the class.
                row.last_error = type(error).__name__[:100]
                row.status = 'failed' if row.attempts >= 8 else 'pending'
                row.next_attempt_at = now() + timedelta(seconds=min(3600, 60 * 2 ** (row.attempts - 1)))
                log.warning('Security notification %s delivery failed; attempt=%s status=%s', row.id, row.attempts, row.status)
            else:
                row.status = 'sent'; row.sent_at = now(); row.last_error = None
                sent += 1
            db.commit()
    # Bound mail-body retention; known IPs remain until the account is deleted.
    with SessionLocal() as db:
        db.query(SecurityNotification).filter(SecurityNotification.status.in_(['sent','failed']), SecurityNotification.created_at < now()-timedelta(days=30)).delete(synchronize_session=False)
        db.commit()
    return sent
