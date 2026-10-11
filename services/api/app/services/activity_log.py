"""Semantic audit records for domain actions; the caller owns the transaction."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive
from app.db.models import AuditLog, User
from app.services.context import ChangeContext


def record_action(
    db: Session,
    context: ChangeContext,
    user: User | None,
    action: str,
    meta: dict[str, Any] | None = None,
) -> None:
    qs = None
    if meta:
        try:
            from urllib.parse import urlencode

            qs = urlencode({k: str(v) for k, v in meta.items() if v is not None})
        except Exception:
            qs = None

    ua = (context.user_agent or "").strip() or None
    ref = (context.referer or "").strip() or None
    if ua and len(ua) > 256:
        ua = ua[:256]
    if ref and len(ref) > 512:
        ref = ref[:512]

    db.add(
        AuditLog(
            ts=utc_now_naive(),
            username=getattr(user, "username", None) if user else None,
            method="ACTION",
            path=f"/action/{action}",
            query_string=qs,
            status_code=200,
            duration_ms=0,
            client_ip=context.client_ip,
            user_agent=ua,
            referer=ref,
        )
    )
