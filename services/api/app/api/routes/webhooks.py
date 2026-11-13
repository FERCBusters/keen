from __future__ import annotations

from typing import NoReturn

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.context import change_context, mark_machine_credential
from app.api.routing import Router
from app.core.valkey import get_valkey
from app.db.session import get_db
from app.ingest.webhooks import BUILT_IN_PROVIDERS, ingest_webhook, verify_secret
from app.security.client_ip import request_ip
from app.security.rate_limit import fixed_window_spend
from app.security.signed_webhooks import SIGNATURE_HEADER, TIMESTAMP_HEADER
from app.services import webhooks as service

router = Router()

# Each provider on a connection may deliver this many bytes a minute, from any
# number of addresses. Storing a delivery (parsing, redaction, masking, rule
# matching) costs processor time in proportion to its size, so a holder of one
# provider's secret on a connection can spend only this much of it.
CONNECTION_BYTES_PER_MINUTE = 32 * 1024 * 1024


def _require_webhook_enabled():
    from app.ingest.connections import deployment_settings

    if not deployment_settings.webhooks_enabled:
        raise HTTPException(503, "Webhook ingestion is disabled")


# Deliveries whose secret verifies draw on an allowance for their provider and
# client address: each built-in provider has its own, so one busy sender leaves
# the others theirs, and every other provider name shares one "other"
# allowance, so a made-up provider never opens a fresh allowance or adds a
# counter. Refused deliveries draw on a separate allowance for their address.
# Senders such as Git hosting services deliver for many customers from shared
# addresses, so a sender without the secret can spend only the refusals'
# allowance, never the one its neighbours' verified deliveries use.
def _generic_limit(provider: str) -> str:
    if provider in BUILT_IN_PROVIDERS:
        return f"webhook:provider:{provider}"
    return "webhook:other"


def _refuse_webhook(request: Request) -> NoReturn:
    """Refuse a delivery whose secret did not verify, counting it."""
    service.refuse(get_valkey(), request_ip(request), "Invalid webhook secret")


@router.post("/v1/webhooks/isms/effectiveness-metrics/{metric_key}")
async def isms_effectiveness_metric_webhook(
    metric_key: str, request: Request, db: Session = Depends(get_db)
):
    """Record an effectiveness measurement signed by a reporting job.

    The job signs ``<timestamp>.<body>`` with HMAC-SHA256 under the measure's
    submission secret and sends ``X-Keen-Timestamp`` and
    ``X-Keen-Signature: v1=<hex>``. ``source_type`` must be a source that a measure
    already names as its expected source, or ``other``; left out, the entry
    takes this measure's expected source. A typical payload is:
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
    # The signature covers the body, so the body (at most 256 KiB) is read
    # first; all of the work runs in the thread pool, off the event loop.
    await run_in_threadpool(_require_webhook_enabled)
    body = await request.body()
    result = await run_in_threadpool(_submit_metric, metric_key, request, db, body)
    mark_machine_credential(request, "measure")
    return result


def _submit_metric(metric_key: str, request: Request, db: Session, body: bytes):
    return service.submit_signed_metric(
        db=db,
        redis=get_valkey(),
        metric_key=metric_key,
        timestamps=request.headers.getlist(TIMESTAMP_HEADER),
        signatures=request.headers.getlist(SIGNATURE_HEADER),
        body=body,
        context=change_context(request),
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
    # The secret is checked before the handler reads the body; all of the
    # work runs in the thread pool, off the event loop.
    connection = await run_in_threadpool(_admit, provider, request, db, connection_id)
    mark_machine_credential(request, "webhook:" + connection.id)
    body = await request.body()
    return await run_in_threadpool(
        _ingest, provider, event_type, request, db, connection, body
    )


def _admit(provider: str, request: Request, db: Session, connection_id: str | None):
    """The enabled connection whose secret this request carries, or a refusal."""
    from app.ingest.connections import connection_scope, connections, resolve

    _require_webhook_enabled()
    # An unauthenticated caller learns nothing about connections or ingestion
    # state before its secret is verified, and spends only the refusals'
    # allowance for its address.
    key = connection_id or "env:webhooks"
    connection = next(
        (c for c in connections(db, "webhooks") if c.id == key and c.enabled), None
    )
    if connection is None:
        _refuse_webhook(request)
    with connection_scope(resolve(connection)):
        if not verify_secret(provider, dict(request.headers.items())):
            _refuse_webhook(request)
        service.spend_address_allowance(
            get_valkey(), _generic_limit(provider), request_ip(request)
        )
        from app.services.ingestion_pause import require_receiving

        require_receiving(db, "webhooks")
    return connection


def _ingest(
    provider: str,
    event_type: str,
    request: Request,
    db: Session,
    connection,
    body: bytes,
):
    from app.ingest.connections import connection_scope, resolve

    with connection_scope(resolve(connection)):
        # Each provider's secret on a connection spends its own allowance, so
        # the holder of one provider's secret cannot hold up another's.
        ok, retry = fixed_window_spend(
            get_valkey(),
            f"keen:rl:webhook:bytes:{connection.id}:{provider}",
            len(body),
            CONNECTION_BYTES_PER_MINUTE,
            60,
            fail_closed=True,
        )
        if not ok:
            raise HTTPException(
                429,
                "Webhook volume limit exceeded for this provider and connection",
                headers={"Retry-After": str(retry)},
            )
        headers = {k: v for k, v in request.headers.items()}
        try:
            return ingest_webhook(
                db, provider=provider, event_type=event_type, body=body, headers=headers
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
