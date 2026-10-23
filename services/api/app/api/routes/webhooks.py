from __future__ import annotations

import json

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.context import change_context
from app.api.routing import Router
from app.core.valkey import get_valkey
from app.db.session import get_db
from app.ingest.webhooks import ingest_webhook, verify_secret
from app.security.rate_limit import fixed_window_allow
from app.services import webhooks as service

router = Router()


def _require_webhook_enabled():
    from app.ingest.connections import deployment_settings

    if not deployment_settings.webhooks_enabled:
        raise HTTPException(503, "Webhook ingestion is disabled")


def _limit_webhook(request: Request, provider: str):
    from app.security.client_ip import request_ip

    ok, retry = fixed_window_allow(
        get_valkey(),
        f"keen:rl:webhook:{provider}:ip:{request_ip(request)}",
        100,
        60,
        fail_closed=True,
    )
    if not ok:
        raise HTTPException(
            429, "Webhook rate limit exceeded", headers={"Retry-After": str(retry)}
        )


@router.post("/v1/webhooks/isms/effectiveness-metrics/{metric_key}")
async def isms_effectiveness_metric_webhook(
    metric_key: str, request: Request, db: Session = Depends(get_db)
):
    """Record an ISMS effectiveness metric entry from an external scheduler/tool.

    Configure provider ``isms_metrics`` in config/webhooks.yml. ``source_type``
    must be an existing Keen evidence source, or ``other``. A typical payload is:
    {
      "value": 42,
      "unit": "requests",
      "period_start": "2026-05-01",
      "period_end": "2026-05-31",
      "source_type": "loki",
      "source_title": "ossec-reportd May 2026",
      "source_url": "https://grafana.example/..."
    }
    """
    _require_webhook_enabled()
    _limit_webhook(request, "isms_metrics")
    headers = {k: v for k, v in request.headers.items()}
    if not verify_secret("isms_metrics", headers):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    from app.services.ingestion_pause import require_receiving

    require_receiving(db, "webhooks")
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        raise HTTPException(status_code=400, detail="Expected JSON payload")
    return service.record_metric(
        db=db, metric_key=metric_key, payload=payload, context=change_context(request)
    )


@router.post("/v1/webhooks/connections/{connection_id}/{provider}/{event_type}")
@router.post("/v1/webhooks/{provider}/{event_type}")
async def webhook_ingest(
    provider: str,
    event_type: str,
    request: Request,
    db: Session = Depends(get_db),
    connection_id: str | None = None,
):
    from app.ingest.connections import connection_scope, connections, resolve

    _require_webhook_enabled()
    # Throttle before any database work; an unauthenticated caller learns
    # nothing about connections or ingestion state before its secret is verified.
    _limit_webhook(request, provider)
    key = connection_id or "env:webhooks"
    connection = next(
        (c for c in connections(db, "webhooks") if c.id == key and c.enabled), None
    )
    if connection is None:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    with connection_scope(resolve(connection)):
        headers = {k: v for k, v in request.headers.items()}
        if not verify_secret(provider, headers):
            raise HTTPException(status_code=403, detail="Invalid webhook secret")
        from app.services.ingestion_pause import require_receiving

        require_receiving(db, "webhooks")
        body = await request.body()
        try:
            return ingest_webhook(
                db, provider=provider, event_type=event_type, body=body, headers=headers
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
