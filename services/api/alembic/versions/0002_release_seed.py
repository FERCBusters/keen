"""Final starter data and installation-specific configuration for KEEN 1.0."""

import gzip
import json
import uuid
from pathlib import Path

import sqlalchemy as sa
import yaml
from alembic import op
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.core.evidence_migration import unify

revision = "0002_release_seed"
down_revision = "0001_release_schema"
branch_labels = None
depends_on = None
ROOT = Path(__file__).resolve().parents[1]

# Managed configuration documents seeded from the deployment's YAML files.
CONFIG_PATHS = {
    "jenkins": "jenkins_config_path",
    "loki": "loki_queries_path",
    "rss": "rss_config_path",
    "github": "github_config_path",
    "forgejo": "forgejo_config_path",
    "cloudwatch_logs": "cloudwatch_logs_config_path",
    "taiga": "taiga_config_path",
    "bookstack": "bookstack_config_path",
    "google_workspace": "google_workspace_config_path",
    "webhooks": "webhooks_path",
    "rules": "rules_path",
}


def _seed_managed_configurations(bind):
    """Store each deployment YAML document, converted to unified evidence rules.

    All writes occur in Alembic's transaction, so a conversion error rolls back
    the whole upgrade.
    """
    docs, old_versions = {}, {}
    for name, path_setting in CONFIG_PATHS.items():
        existing = (
            bind.execute(
                sa.text(
                    "SELECT document, version FROM managed_configurations "
                    "WHERE name=:name FOR UPDATE"
                ),
                {"name": name},
            )
            .mappings()
            .first()
        )
        if existing:
            docs[name] = existing["document"]
            old_versions[name] = int(existing["version"])
        else:
            path = Path(getattr(settings, path_setting))
            if not path.is_file():
                raise RuntimeError(
                    f"Cannot seed {name}: missing {path}. "
                    "Restore the config mount before migrating."
                )
            docs[name] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            old_versions[name] = 0
    converted, _ = unify(docs, settings.default_framework_slug)
    now = utc_now_naive()
    for name, document in converted.items():
        # Copy every source into Postgres so first boot and later restarts use
        # the same preset definitions regardless of mounted files.
        version = old_versions[name] + 1
        values = {
            "name": name,
            "document": json.dumps(document),
            "version": version,
            "at": now,
        }
        if old_versions[name]:
            bind.execute(
                sa.text(
                    "UPDATE managed_configurations SET "
                    "document=CAST(:document AS jsonb), version=:version, "
                    "updated_at=:at WHERE name=:name"
                ),
                values,
            )
        else:
            bind.execute(
                sa.text(
                    "INSERT INTO managed_configurations "
                    "(name,document,version,updated_at) "
                    "VALUES (:name,CAST(:document AS jsonb),:version,:at)"
                ),
                values,
            )
        bind.execute(
            sa.text(
                "INSERT INTO managed_configuration_revisions "
                "(id,name,version,document,updated_at) "
                "VALUES (:id,:name,:version,CAST(:document AS jsonb),:at)"
            ),
            {**values, "id": uuid.uuid4()},
        )


def upgrade():
    bind = op.get_bind()
    seeds = json.loads(gzip.decompress((ROOT / "release_seeds.json.gz").read_bytes()))
    # Parent tables precede their dependants; self-referencing control parents
    # are inserted in the same SQL statement, preserving their UUID links.
    order = [
        "frameworks",
        "framework_clauses",
        "control_items",
        "cross_framework_control_links",
        "osa_control_mappings",
        "permissions",
        "pestle_relevance_levels",
        "risk_categories",
        "risk_asset_subcategories",
        "risk_assets",
        "risk_library_entries",
        "isms_business_processes",
        "evidence_retention_policy",
        "global_event_stats",
    ]
    assert set(order) == set(seeds), (
        "Seed table inventory changed without an insertion order"
    )
    for table in order:
        bind.execute(
            sa.text(
                f"INSERT INTO {table} SELECT * FROM json_populate_recordset(NULL::{table}, :rows)"
            ),
            {"rows": json.dumps(seeds[table])},
        )
    # Convert this installation's deployment YAML at migration time rather than
    # freezing a developer machine's connector settings in the seed snapshot.
    _seed_managed_configurations(bind)
    # Rules files written before framework slugs were normalised name ISO/IEC
    # 27001 by its old slug.
    bind.execute(
        sa.text(
            "UPDATE managed_configurations SET document=replace(document::text, 'ISO27001:2022', 'iso_27001_2022')::jsonb WHERE name='rules'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE managed_configuration_revisions SET document=replace(document::text, 'ISO27001:2022', 'iso_27001_2022')::jsonb WHERE name='rules'"
        )
    )
    known = {row["slug"] for row in seeds["frameworks"]}
    permitted = {
        slug.strip() for slug in settings.enabled_frameworks.split(",") if slug.strip()
    }
    eligible = known & permitted if permitted else known
    preferred = settings.default_framework_slug
    default = next(
        (s for s in [preferred, "KEEN-AF:1.0"] if s in eligible),
        min(eligible) if eligible else None,
    )
    if default is None:
        raise RuntimeError("KEEN_ENABLED_FRAMEWORKS excludes every seeded framework")
    document = json.dumps({"enabled": [default], "default": default})
    now = utc_now_naive()
    bind.execute(
        sa.text(
            "INSERT INTO managed_configurations (name,document,version,updated_at) VALUES ('organisation-frameworks',cast(:doc AS jsonb),1,:at)"
        ),
        {"doc": document, "at": now},
    )
    bind.execute(
        sa.text(
            "INSERT INTO managed_configuration_revisions (id,name,document,version,updated_at) VALUES (:id,'organisation-frameworks',cast(:doc AS jsonb),1,:at)"
        ),
        {"id": uuid.uuid4(), "doc": document, "at": now},
    )


def downgrade():
    raise RuntimeError("Restore a database backup to undo release seed data.")
