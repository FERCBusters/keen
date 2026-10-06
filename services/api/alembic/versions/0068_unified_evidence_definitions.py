"""Seed unified evidence definitions from existing database overrides or YAML.

Revision ID: 0068_unified_evidence
Revises: 0067_managed_configurations

All writes occur in Alembic's transaction. A conversion error rolls back the
entire upgrade; previously imported events and mappings are never rewritten.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
import yaml
from alembic import op

from app.core.config import settings
from app.core.evidence_migration import unify

revision = "0068_unified_evidence"
down_revision = "0067_managed_configurations"
branch_labels = None
depends_on = None

PATHS = {
    "jenkins": "jenkins_config_path", "loki": "loki_queries_path",
    "rss": "rss_config_path", "github": "github_config_path",
    "forgejo": "forgejo_config_path", "cloudwatch_logs": "cloudwatch_logs_config_path",
    "taiga": "taiga_config_path", "bookstack": "bookstack_config_path",
    "google_workspace": "google_workspace_config_path", "webhooks": "webhooks_path",
    "rules": "rules_path",
}


def upgrade():
    bind = op.get_bind()
    docs, old_versions = {}, {}
    for name, path_setting in PATHS.items():
        existing = bind.execute(sa.text(
            "SELECT document, version FROM managed_configurations WHERE name=:name FOR UPDATE"
        ), {"name": name}).mappings().first()
        if existing:
            docs[name] = existing["document"]
            old_versions[name] = int(existing["version"])
        else:
            path = Path(getattr(settings, path_setting))
            if not path.is_file():
                raise RuntimeError(f"Cannot seed {name}: missing {path}. Restore the config mount before migrating.")
            docs[name] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            old_versions[name] = 0
    converted, _ = unify(docs, settings.default_framework_slug)
    now = datetime.utcnow()
    for name, document in converted.items():
        # Copy every source into Postgres so first boot and subsequent restarts
        # use exactly the same preset definitions regardless of mounted files.
        version = old_versions[name] + 1
        if old_versions[name]:
            bind.execute(sa.text(
                "UPDATE managed_configurations SET document=CAST(:document AS jsonb), "
                "version=:version, updated_at=:at WHERE name=:name"
            ), {"name": name, "document": json.dumps(document), "version": version, "at": now})
        else:
            bind.execute(sa.text(
                "INSERT INTO managed_configurations (name,document,version,updated_at) "
                "VALUES (:name,CAST(:document AS jsonb),:version,:at)"
            ), {"name": name, "document": json.dumps(document), "version": version, "at": now})
        bind.execute(sa.text(
            "INSERT INTO managed_configuration_revisions "
            "(id,name,version,document,updated_at) "
            "VALUES (:id,:name,:version,CAST(:document AS jsonb),:at)"
        ), {"id": uuid.uuid4(), "name": name, "version": version,
            "document": json.dumps(document), "at": now})


def downgrade():
    # Event mappings and administrator edits must never be destroyed by a
    # rollback of schema version metadata. Keep managed documents intact.
    pass
