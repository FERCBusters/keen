"""Administrator-owned connections, draft/live collectors and asynchronous previews."""

import json
import re
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import IntegrationCollector as Collector
from app.db.models import IntegrationConnection as Connection
from app.db.models import IntegrationRevision as Revision
from app.db.models import IntegrationRun as Run
from app.db.models import IsmsEffectivenessMeasure as Measure
from app.db.session import get_db
from app.integrations.runtime import dispatch, fail, queue_run
from app.integrations.schema import Definition, Strict
from app.integrations.templates import templates
from app.integrations.transport import encrypt
from app.security.auth import require_admin


def integration_demo_guard(request: Request):
    if settings.demo_mode and request.method not in ("GET", "HEAD", "OPTIONS"):
        raise HTTPException(
            403,
            "You are running in a demo environment. New API integrations cannot be configured or run here.",
        )


router = APIRouter(
    prefix="/v1/admin/integrations",
    dependencies=[Depends(require_admin), Depends(integration_demo_guard)],
)


class ConnectionInput(Strict):
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(max_length=2048)
    auth_kind: str = "none"
    auth_name: str = Field(default="", max_length=128)
    username: str = Field(default="", max_length=256)
    auth_options: dict[str, str] = Field(default_factory=dict)
    secret: str | None = Field(default=None, max_length=8192)
    version: int = 0


class CollectorInput(Strict):
    name: str = Field(min_length=1, max_length=128)
    connection_id: str
    definition: Definition
    version: int = 0


class Action(Strict):
    version: int
    revision: int | None = None


def require(db, model, key, lock=False):
    value = (
        db.scalar(select(model).where(model.id == key).with_for_update())
        if lock
        else db.get(model, key)
    )
    if not value:
        raise HTTPException(404, "Not found")
    return value


def match_version(obj, version):
    if obj.version != version:
        raise HTTPException(409, "This item changed. Reload before saving.")


def connection_view(c):
    return {
        k: getattr(c, k)
        for k in (
            "id",
            "name",
            "base_url",
            "auth_kind",
            "auth_name",
            "username",
            "auth_options",
            "version",
        )
    } | {"has_secret": bool(c.encrypted_secret)}


def collector_view(c):
    return {
        k: getattr(c, k)
        for k in (
            "id",
            "name",
            "connection_id",
            "draft",
            "version",
            "live_revision",
            "enabled",
            "failures",
            "cursor",
            "next_run",
            "last_success",
        )
    }


def run_view(r):
    return {
        k: getattr(r, k)
        for k in (
            "id",
            "collector_id",
            "revision",
            "status",
            "created_at",
            "started_at",
            "finished_at",
            "new_records",
            "duplicates",
            "message",
            "preview",
            "result",
        )
    }


def validate_definition(db, d):
    if len(json.dumps(d.model_dump())) > 65536:
        raise HTTPException(422, "Definition exceeds 64 KiB")

    # Literal credentials in definitions would leak through revision/export history.
    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if re.search(
                    r"(^|[_-])(password|secret|token|api[_-]?key|authorization|cookie)($|[_-])",
                    k,
                    re.I,
                ):
                    raise HTTPException(
                        422, "Store credentials in a Connection, not a definition"
                    )
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk({"query": d.query, "headers": d.headers, "body": d.body})
    if d.output == "measurement":
        try:
            measure = db.get(Measure, uuid.UUID(d.measure_id))
        except ValueError:
            measure = None
        if not measure or measure.target_unit != d.unit:
            raise HTTPException(
                422, "Select an existing measure and use its exact target unit"
            )


@router.get("")
def overview(db: Session = Depends(get_db)):
    return {
        "demo_mode": settings.demo_mode,
        "connections": [
            connection_view(c) for c in db.query(Connection).order_by(Connection.name)
        ],
        "collectors": [
            collector_view(c) for c in db.query(Collector).order_by(Collector.name)
        ],
        "runs": [
            run_view(r)
            for r in db.query(Run).order_by(Run.created_at.desc()).limit(100)
        ],
        "templates": templates(),
        "measures": [
            {
                "id": str(m.id),
                "name": m.summary,
                "framework": m.framework_slug,
                "unit": m.target_unit,
            }
            for m in db.query(Measure).order_by(Measure.summary)
        ],
    }


@router.post("/connections")
def create_connection(body: ConnectionInput, db: Session = Depends(get_db)):
    return _save_connection(str(uuid.uuid4()), body, db, new=True)


@router.put("/connections/{key}")
def save_connection(key: str, body: ConnectionInput, db: Session = Depends(get_db)):
    return _save_connection(key, body, db)


def _save_connection(key, body, db, new=False):
    u = urlsplit(body.base_url)
    if (
        u.scheme != "https"
        or not u.hostname
        or u.username
        or u.password
        or u.query
        or u.fragment
    ):
        raise HTTPException(
            422, "Base URL must use HTTPS without credentials, query or fragment"
        )
    if body.auth_kind not in (
        "none",
        "bearer",
        "basic",
        "header",
        "query",
        "oauth_client_credentials",
    ):
        raise HTTPException(422, "Unsupported authentication type")
    if body.auth_kind in ("header", "query") and (
        not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", body.auth_name)
        or body.auth_name.lower()
        in (
            "host",
            "cookie",
            "content-length",
            "transfer-encoding",
            "connection",
            "proxy-authorization",
        )
    ):
        raise HTTPException(422, "Provide a valid authentication parameter/header name")
    if set(body.auth_options) - {"token_path", "scope"}:
        raise HTTPException(422, "Unknown authentication option")
    token_path = body.auth_options.get("token_path", "/oauth/token")
    if (
        not token_path.startswith("/")
        or token_path.startswith("//")
        or "?" in token_path
        or "#" in token_path
    ):
        raise HTTPException(422, "OAuth token path must be relative to the connection")
    c = Connection(id=key, version=0) if new else require(db, Connection, key, True)
    match_version(c, body.version)
    if body.auth_kind != "none" and not body.secret and not c.encrypted_secret:
        raise HTTPException(422, "A credential is required")
    if (
        not new
        and body.auth_kind != c.auth_kind
        and body.auth_kind != "none"
        and not body.secret
    ):
        raise HTTPException(
            422, "Supply a new credential when changing authentication type"
        )
    for name in (
        "name",
        "base_url",
        "auth_kind",
        "auth_name",
        "username",
        "auth_options",
    ):
        setattr(c, name, getattr(body, name))
    try:
        if body.auth_kind == "none":
            c.encrypted_secret = ""
        elif body.secret:
            if "\n" in body.secret or "\r" in body.secret:
                raise ValueError("Credential must not contain newlines")
            c.encrypted_secret = encrypt({"secret": body.secret})
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    c.version += 1
    db.add(c)
    db.commit()
    return connection_view(c)


@router.post("/collectors")
def create_collector(body: CollectorInput, db: Session = Depends(get_db)):
    validate_definition(db, body.definition)
    require(db, Connection, body.connection_id, True)
    c = Collector(
        id=str(uuid.uuid4()),
        name=body.name,
        connection_id=body.connection_id,
        draft=body.definition.model_dump(),
    )
    db.add(c)
    db.commit()
    return collector_view(c)


@router.put("/collectors/{key}")
def save_collector(key: str, body: CollectorInput, db: Session = Depends(get_db)):
    c = require(db, Collector, key, True)
    match_version(c, body.version)
    validate_definition(db, body.definition)
    require(db, Connection, body.connection_id, True)
    c.name, c.connection_id, c.draft = (
        body.name,
        body.connection_id,
        body.definition.model_dump(),
    )
    c.version += 1
    db.commit()
    return collector_view(c)


@router.get("/collectors/{key}/revisions")
def revisions(key: str, db: Session = Depends(get_db)):
    require(db, Collector, key)
    return {
        "items": [
            {
                "revision": r.revision,
                "definition": r.definition,
                "connection_id": r.connection_id,
                "created_at": r.created_at,
            }
            for r in db.query(Revision)
            .filter(Revision.collector_id == key)
            .order_by(Revision.revision.desc())
        ]
    }


@router.post("/collectors/{key}/{action}")
def action_collector(
    key: str, action: str, body: Action, db: Session = Depends(get_db)
):
    c = require(db, Collector, key, True)
    match_version(c, body.version)
    active = (
        db.query(Run)
        .filter(Run.collector_id == key, Run.status.in_(["queued", "running"]))
        .first()
    )
    if action in ("publish", "rollback", "reset-cursor") and active:
        raise HTTPException(
            409, "Wait for the active run before changing the live revision or cursor"
        )
    if action == "publish":
        validate_definition(db, Definition.model_validate(c.draft))
        if not db.get(Revision, (key, c.version)):
            db.add(
                Revision(
                    collector_id=key,
                    revision=c.version,
                    definition=c.draft,
                    connection_id=c.connection_id,
                )
            )
        c.live_revision = c.version
        # Publishing is explicit but does not silently reset a exhausted failure budget.
        c.enabled = c.failures < 3
        c.next_run = utc_now_naive()
    elif action == "rollback":
        revision = db.get(Revision, (key, body.revision))
        if not revision:
            raise HTTPException(404, "Revision not found")
        c.draft, c.connection_id = revision.definition, revision.connection_id
        c.version += 1
        c.live_revision = revision.revision
    elif action == "pause":
        c.enabled = False
    elif action == "resume":
        if not c.live_revision:
            raise HTTPException(422, "Publish first")
        c.enabled, c.failures, c.next_run = True, 0, utc_now_naive()
    elif action == "reset-cursor":
        c.cursor = None
    elif action in ("preview", "run"):
        try:
            if action == "run" and c.failures >= 3:
                raise ValueError(
                    "Review the failure and explicitly resume before collecting again"
                )
            r = queue_run(db, c, preview=action == "preview")
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        db.commit()
        try:
            dispatch(r.id)
        except Exception:
            fail(db, r, "Could not queue run; check broker health.")
        return run_view(r)
    else:
        raise HTTPException(404, "Unknown action")
    db.commit()
    return collector_view(c)


@router.get("/runs/{key}")
def read_run(key: str, db: Session = Depends(get_db)):
    return run_view(require(db, Run, key))


class SampleInput(Strict):
    definition: Definition
    response: dict | list
    collector_id: str | None = None


@router.post("/preview-sample")
def preview_sample(body: SampleInput, db: Session = Depends(get_db)):
    from app.integrations.engine import normalize
    from app.integrations.schema import pointer
    from app.security.redaction import redact_obj

    if len(json.dumps(body.response)) > 1024 * 1024:
        raise HTTPException(422, "Sample response exceeds 1 MiB")
    source = "integration:" + (body.collector_id or "draft")
    if body.collector_id:
        require(db, Collector, body.collector_id)
    items = pointer(body.response, body.definition.records_path)
    if not isinstance(items, list):
        raise HTTPException(422, "Records path must select a JSON array")
    samples = []
    try:
        for item in items[:10]:
            f = normalize(item, body.definition)
            event = {
                k: f.get(k)
                for k in ("system", "actor", "action", "outcome", "severity", "summary")
            }
            event.update(
                source=source, normalized_payload=redact_obj(item), raw_pointer={}
            )
            samples.append(
                {
                    "evidence": redact_obj(event),
                    "matches": None,
                    "mapping_note": "Use the background Test draft action to evaluate framework rules.",
                    "measurement": {
                        k: f.get(k) for k in ("value", "period_start", "period_end")
                    }
                    if body.definition.output == "measurement"
                    else None,
                }
            )
    except Exception:
        raise HTTPException(
            422,
            "Sample could not be mapped. Check required field paths, timestamp timezone and field transformations.",
        )
    return {
        "status": "succeeded",
        "message": "Sample field mapping only: no network requests or evidence writes. Related requests and framework rules are not executed.",
        "result": {"samples": samples},
    }
