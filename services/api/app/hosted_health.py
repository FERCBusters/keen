"""Internal readiness probe: DB, broker, owner identity and worker response."""
from sqlalchemy import text
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import User, UserIdentity
from app.core.valkey import get_valkey


def main():
    if not settings.hosted_mode:
        raise RuntimeError("Not a hosted installation")
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
        identity = db.query(UserIdentity).filter_by(issuer=settings.oidc_issuer,
                         subject=settings.hosted_owner_subject, provider="oidc").one()
        user = db.query(User).filter_by(id=identity.user_id, is_active=True).one()
        if user.role != "admin":
            raise RuntimeError("Owner is not an administrator")
    get_valkey().ping()
    from app.worker.celery_app import celery_app
    if not celery_app.control.ping(timeout=5):
        raise RuntimeError("No worker responded")


if __name__ == "__main__":
    main()
