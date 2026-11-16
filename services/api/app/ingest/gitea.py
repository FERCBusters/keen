"""Gitea repository activity collection (see gitea_compatible)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ingest.connections import connection_runs
from app.ingest.gitea_compatible import GiteaCompatibleCollector
from app.ingest.registry import HTTP_QUEUE, Source, register
from app.services.ingestion_pause import pausable

collector = GiteaCompatibleCollector("gitea", "Gitea")


@pausable("gitea")
@connection_runs("gitea")
def ingest_gitea_all(db: Session) -> list[dict[str, Any]]:
    return collector.ingest_all(db)


register(
    Source(
        name="gitea",
        module=__name__,
        queue=HTTP_QUEUE,
        schedule=5700.0,
        config_setting="gitea_config_path",
        public_fields=("base_url", "username", "auth_mode"),
        secret_fields=("token", "cookie"),
        input_sections=("organizations", "users", "feeds"),
        section="feeds",
        id_field="url",
        definition_sections=("feeds", "organizations", "users"),
        key_field="url",
    )
)
