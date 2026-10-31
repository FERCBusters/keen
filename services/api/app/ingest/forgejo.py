"""Forgejo repository activity collection (see gitea_compatible)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ingest.connections import connection_runs
from app.ingest.gitea_compatible import GiteaCompatibleCollector
from app.services.ingestion_pause import pausable

collector = GiteaCompatibleCollector("forgejo", "Forgejo")


@pausable("forgejo")
@connection_runs("forgejo")
def ingest_forgejo_all(db: Session) -> list[dict[str, Any]]:
    return collector.ingest_all(db)
