"""Final starter data and installation-specific configuration for KEEN 1.0."""

import gzip
import importlib.util
import json
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive

revision = "0002_release_seed"
down_revision = "0001_release_schema"
branch_labels = None
depends_on = None
ROOT = Path(__file__).resolve().parents[1]


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
    # Preserve the historical deployment-YAML conversion rather than freezing
    # the developer machine's connector settings in the release seed snapshot.
    spec = importlib.util.spec_from_file_location(
        "release_config_seed",
        ROOT / "legacy_versions/0068_unified_evidence_definitions.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.upgrade()
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
