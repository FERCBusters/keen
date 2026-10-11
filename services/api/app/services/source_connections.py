"""Source connections application operations."""

from __future__ import annotations

import re
import uuid
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.source_meta import get_source_meta
from app.db.models import SourceConnection
from app.ingest.connections import FIELDS, connections, deployment_settings
from app.integrations.errors import IntegrationError
from app.integrations.transport import decrypt, encrypt
from app.services.errors import DomainError


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    name: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    configuration: dict[str, str] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)
    clear_credentials: list[str] = Field(default_factory=list)
    inputs: dict = Field(default_factory=dict)
    version: int = 0


def view(row):
    return {
        k: getattr(row, k)
        for k in (
            "id",
            "source",
            "name",
            "enabled",
            "configuration",
            "inputs",
            "version",
        )
    } | {"managed_by": "database", "has_credentials": bool(row.encrypted_credentials)}


def validate(payload):
    if payload.source not in FIELDS:
        raise DomainError(422, "Unknown source type")
    public, secret = (set(v.split()) for v in FIELDS[payload.source])
    if (
        set(payload.configuration) - public
        or (set(payload.credentials) | set(payload.clear_credentials)) - secret
    ):
        raise DomainError(422, "Unsupported connection field")
    from app.services.managed_configurations import _safe_document, _validate_connector

    _validate_connector(payload.source, payload.inputs)
    _safe_document(payload.inputs)
    url = payload.configuration.get("base_url")
    if url:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise DomainError(
                422,
                "Use an endpoint URL without embedded credentials, query or fragment",
            )
    if payload.source == "elasticsearch" and (
        not url or urlsplit(url).scheme != "https"
    ):
        raise DomainError(422, "Elasticsearch requires an HTTPS endpoint")
    if payload.configuration.get("auth_mode", "token") not in (
        "",
        "token",
        "bearer",
        "basic",
        "query",
        "cookie",
        "none",
    ):
        raise DomainError(422, "Choose a supported authentication mode")
    for key in ("header_name", "secret_header"):
        value = payload.configuration.get(key)
        if value and not re.fullmatch(r"[A-Za-z0-9-]{1,128}", value):
            raise DomainError(422, "Enter a valid HTTP header name")
    if payload.source == "webhooks" and not re.fullmatch(
        r"[A-Za-z0-9_-]{1,100}", payload.configuration.get("provider", "")
    ):
        raise DomainError(
            422,
            "Enter a webhook provider name using letters, numbers, hyphens or underscores",
        )
    if not payload.name.strip():
        raise DomainError(422, "A connection name is required")


def listing(*, db):
    rows = db.scalars(
        select(SourceConnection).order_by(
            SourceConnection.source, SourceConnection.name
        )
    ).all()
    managed = [
        {
            "id": c.id,
            "source": c.source,
            "name": c.name,
            "enabled": c.enabled,
            "configuration": c.configuration,
            "inputs": c.inputs or {},
            "version": 0,
            "managed_by": c.managed_by,
            "has_credentials": bool(c.credential_references),
        }
        for c in connections(None)
        if c.managed_by == "file"
    ]
    return {
        "connections": [view(row) for row in rows] + managed,
        "types": [
            {
                "source": s,
                "label": get_source_meta(s).label,
                "enabled": bool(getattr(deployment_settings, s + "_enabled", False)),
                "fields": public.split(),
                "credential_fields": secret.split(),
            }
            for s, (public, secret) in FIELDS.items()
        ],
    }


def create(*, payload: ConnectionInput, db):
    validate(payload)
    row = SourceConnection(id=str(uuid.uuid4()), source=payload.source)
    return save(row, payload, db)


def save(row, payload, db):
    try:
        secret = decrypt(row.encrypted_credentials)
    except (IntegrationError, InvalidToken):
        raise DomainError(
            503,
            "Check KEEN_INTEGRATION_SECRET_KEY on API and worker before changing credentials",
        ) from None
    for key in payload.clear_credentials:
        secret.pop(key, None)
    secret.update({k: v for k, v in payload.credentials.items() if v})
    row.name = payload.name.strip()
    row.enabled = payload.enabled
    row.configuration = payload.configuration
    row.inputs = payload.inputs
    try:
        row.encrypted_credentials = encrypt(secret) if secret else None
    except IntegrationError:
        raise DomainError(
            503,
            "Set KEEN_INTEGRATION_SECRET_KEY on API and worker before storing credentials",
        ) from None
    row.version = (row.version or 0) + 1
    db.add(row)
    db.commit()
    return view(row)


def require(db, key, version):
    row = db.scalar(
        select(SourceConnection).where(SourceConnection.id == key).with_for_update()
    )
    if row is None:
        raise DomainError(404, "Connection not found")
    if row.version != version:
        raise DomainError(409, "Connection changed; reload before saving")
    return row


def update(*, key: str, payload: ConnectionInput, db):
    validate(payload)
    row = require(db, key, payload.version)
    if row.source != payload.source:
        raise DomainError(422, "The source type of a connection is fixed")
    return save(row, payload, db)


def delete(*, key: str, version: int, db):
    row = require(db, key, version)
    db.delete(row)
    db.commit()
    return {"deleted": key, "evidence_retained": True}


def run(*, key: str, db):
    connection = next((c for c in connections(db) if c.id == key), None)
    if not connection:
        raise DomainError(404, "Connection not found")
    if connection.source == "webhooks":
        raise DomainError(422, "Webhooks receive events at their callback URL")
    if not connection.enabled or not getattr(
        deployment_settings, connection.source + "_enabled", False
    ):
        raise DomainError(409, "Connection or source type is disabled")
    from app.worker.tasks import ingest_source_connection_task

    task = ingest_source_connection_task.delay(connection.source, key)
    return {"task_id": task.id}
