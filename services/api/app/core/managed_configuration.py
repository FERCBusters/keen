"""Read administrator overrides from Postgres, falling back to shipped YAML.

Each read opens its own short session so Celery workers, API workers and beat
see changes on their next run without touching the read-only config mount.
"""
from __future__ import annotations

from typing import Any
import yaml
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.db.models import ManagedConfiguration


def load_document(name: str, path: str, *, db: Session | None = None) -> dict[str, Any]:
    if db is None:
        with SessionLocal() as session:
            row = session.get(ManagedConfiguration, name)
            if row is not None:
                return row.document
    else:
        row = db.get(ManagedConfiguration, name)
        if row is not None:
            return row.document
    with open(path, encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    if not isinstance(data, dict) and not (name == "rules" and isinstance(data, list)):
        raise ValueError(f"{name} configuration must be an object")
    return data
