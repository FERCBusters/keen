"""Read administrator overrides from Postgres, falling back to shipped YAML.

Each read opens its own short session so Celery workers, API workers and beat
see changes on their next run without touching the read-only config mount.
"""

from __future__ import annotations

from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ManagedConfiguration
from app.db.session import SessionLocal


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
    return _shipped(name, path)


def load_documents(paths: dict[str, str]) -> dict[str, dict[str, Any]]:
    """``load_document`` for several documents, read in one short session.

    ``paths`` maps each document name to its shipped YAML file.
    """
    with SessionLocal() as session:
        stored: dict[str, dict[str, Any]] = {
            name: document
            for name, document in session.execute(
                select(ManagedConfiguration.name, ManagedConfiguration.document).where(
                    ManagedConfiguration.name.in_(sorted(paths))
                )
            )
        }
    return {
        name: stored[name] if name in stored else _shipped(name, path)
        for name, path in paths.items()
    }


def _shipped(name: str, path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{name} configuration must be an object")
    return data
