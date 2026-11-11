"""Risks and PESTLE(E) factors name the people, vendors and assets they affect.

The separate interested-party register is removed: its names, natures,
control links and communications, the risks' links to it, its permissions,
feature switch, MCP scope, audit samples and change history. Risks and
PESTLE(E) items record who and what they affect in one link table, each link
naming one person, vendor or asset. Earlier risk links to people and vendors
are not carried over.

Diary entries already written for interested parties are evidence and stay;
they now follow the read rule of other ISMS link entries.

A downgrade restores the register's tables and permissions, empty: like other
downgrades it brings back the schema, not the records, grants, switch or
scopes the upgrade removed.
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0047_affected_links"
down_revision = "0046_asset_owner_only"
branch_labels = None
depends_on = None

_PARTY_TABLES = (
    "risk_interested_parties",
    "interested_party_communications",
    "interested_party_control_links",
    "interested_parties",
    "interested_party_names",
    "interested_party_natures",
)
_PARTY_PERMISSIONS = ("interested_parties.read", "interested_parties.manage")
_PARTY_SCOPE = "interested_parties.read"
# The permissions as the release seed described them.
_PARTY_PERMISSION_DESCRIPTIONS = {
    "interested_parties.read": "View Interested Parties assessments, communications"
    " and control mappings.",
    "interested_parties.manage": "Create, edit and delete Interested Parties, lookup"
    " values, communications and control mappings.",
}


def _one_of(column: str, values: tuple[str, ...]) -> str:
    """A check that ``column`` holds one of ``values``, as PostgreSQL stored it."""
    listed = ", ".join(f"('{value}'::character varying)::text" for value in values)
    return f"CHECK ((({column})::text = ANY (ARRAY[{listed}])))"


_EVENTS = ("Business as Usual", "Incident", "Alert", "Notifiable Event")
_WHEN = (
    "As Required",
    "Upon Identification",
    "Routine",
    "Within 24 hours",
    "Within 48 hours",
    "Within 72 hours",
    "Within a week",
    "Within a month",
)
_WITH_WHOM = (
    "Individual member",
    "Individual staff member",
    "Senior Supplier Point of Contact",
    "Supplier Point of Contact",
    "NCSC",
    "ICO",
    "Sender",
)
# The register's tables as 0046_asset_owner_only left them.
_PARTY_SCHEMA = """
CREATE TABLE interested_party_names (
    id uuid PRIMARY KEY,
    name varchar(256) NOT NULL CONSTRAINT uq_interested_party_names_name UNIQUE,
    created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at timestamp NOT NULL DEFAULT now(),
    updated_at timestamp NOT NULL DEFAULT now()
);
CREATE INDEX ix_interested_party_names_created_at ON interested_party_names (created_at);
CREATE INDEX ix_interested_party_names_name ON interested_party_names (name);

CREATE TABLE interested_party_natures (
    id uuid PRIMARY KEY,
    name varchar(256) NOT NULL CONSTRAINT uq_interested_party_natures_name UNIQUE,
    created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at timestamp NOT NULL DEFAULT now(),
    updated_at timestamp NOT NULL DEFAULT now()
);
CREATE INDEX ix_interested_party_natures_created_at ON interested_party_natures (created_at);
CREATE INDEX ix_interested_party_natures_name ON interested_party_natures (name);

CREATE TABLE interested_parties (
    id uuid PRIMARY KEY,
    name_id uuid NOT NULL REFERENCES interested_party_names(id),
    nature_id uuid NOT NULL REFERENCES interested_party_natures(id),
    created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at timestamp NOT NULL DEFAULT now(),
    updated_at timestamp NOT NULL DEFAULT now(),
    note text NOT NULL DEFAULT '',
    CONSTRAINT uq_interested_parties_name_nature UNIQUE (name_id, nature_id)
);
CREATE INDEX ix_interested_parties_created_at ON interested_parties (created_at);
CREATE INDEX ix_interested_parties_name_id ON interested_parties (name_id);
CREATE INDEX ix_interested_parties_nature_id ON interested_parties (nature_id);

CREATE TABLE interested_party_communications (
    id uuid PRIMARY KEY,
    interested_party_id uuid NOT NULL
        REFERENCES interested_parties(id) ON DELETE CASCADE,
    event varchar(64) NOT NULL,
    "when" varchar(64) NOT NULL,
    with_whom varchar(128) NOT NULL,
    methods jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamp NOT NULL DEFAULT now(),
    updated_at timestamp NOT NULL DEFAULT now(),
    CONSTRAINT ck_interested_party_communications_event {event},
    CONSTRAINT ck_interested_party_communications_when {when},
    CONSTRAINT ck_interested_party_communications_with_whom {with_whom}
);
CREATE INDEX ix_interested_party_communications_created_at
    ON interested_party_communications (created_at);
CREATE INDEX ix_interested_party_communications_event
    ON interested_party_communications (event);
CREATE INDEX ix_interested_party_communications_interested_party_id
    ON interested_party_communications (interested_party_id);
CREATE INDEX ix_interested_party_communications_when
    ON interested_party_communications ("when");
CREATE INDEX ix_interested_party_communications_with_whom
    ON interested_party_communications (with_whom);

CREATE TABLE interested_party_control_links (
    interested_party_id uuid NOT NULL
        REFERENCES interested_parties(id) ON DELETE CASCADE,
    framework_slug varchar(64) NOT NULL,
    control_item_id uuid NOT NULL REFERENCES control_items(id) ON DELETE CASCADE,
    created_at timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (interested_party_id, framework_slug, control_item_id)
);
CREATE INDEX ix_interested_party_control_links_framework_slug
    ON interested_party_control_links (framework_slug);

CREATE TABLE risk_interested_parties (
    id uuid PRIMARY KEY,
    risk_id uuid NOT NULL REFERENCES risks(id) ON DELETE CASCADE,
    interested_party_id uuid REFERENCES interested_parties(id) ON DELETE CASCADE,
    person_id uuid REFERENCES isms_people(id) ON DELETE CASCADE,
    vendor_id uuid REFERENCES isms_vendors(id) ON DELETE CASCADE,
    created_at timestamp NOT NULL,
    CONSTRAINT ck_risk_interested_parties_one_party CHECK (
        (CASE WHEN interested_party_id IS NULL THEN 0 ELSE 1 END)
        + (CASE WHEN person_id IS NULL THEN 0 ELSE 1 END)
        + (CASE WHEN vendor_id IS NULL THEN 0 ELSE 1 END) = 1)
);
CREATE INDEX ix_risk_interested_parties_interested_party_id
    ON risk_interested_parties (interested_party_id);
CREATE INDEX ix_risk_interested_parties_person_id ON risk_interested_parties (person_id);
CREATE INDEX ix_risk_interested_parties_risk_id ON risk_interested_parties (risk_id);
CREATE INDEX ix_risk_interested_parties_vendor_id ON risk_interested_parties (vendor_id);
""".format(
    event=_one_of("event", _EVENTS),
    when=_one_of('"when"', _WHEN),
    with_whom=_one_of("with_whom", _WITH_WHOM),
)


def _uuid_fk(name: str, target: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(f"{target}.id", ondelete="CASCADE"),
        nullable=True,
    )


def upgrade():
    op.create_table(
        "affected_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _uuid_fk("risk_id", "risks"),
        _uuid_fk("pestle_item_id", "pestle_items"),
        _uuid_fk("person_id", "isms_people"),
        _uuid_fk("vendor_id", "isms_vendors"),
        _uuid_fk("asset_id", "risk_assets"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(CASE WHEN risk_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN pestle_item_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_affected_links_one_owner",
        ),
        sa.CheckConstraint(
            "(CASE WHEN person_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN vendor_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN asset_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_affected_links_one_target",
        ),
    )
    for column in ("risk_id", "pestle_item_id", "person_id", "vendor_id", "asset_id"):
        op.create_index(f"ix_affected_links_{column}", "affected_links", [column])

    for table in _PARTY_TABLES:
        op.drop_table(table)

    # Holders, directly or through a group, refresh their permission snapshots
    # on their next request; their grants go with the permission rows.
    codes = sa.bindparam("codes", value=_PARTY_PERMISSIONS, expanding=True)
    op.execute(
        sa.text(
            """UPDATE users SET authz_version = authz_version + 1
               WHERE id IN (
                   SELECT up.user_id FROM user_permissions up
                   JOIN permissions p ON p.id = up.permission_id
                   WHERE p.code IN :codes
                   UNION
                   SELECT ug.user_id FROM user_groups ug
                   JOIN group_permissions gp ON gp.group_id = ug.group_id
                   JOIN permissions p ON p.id = gp.permission_id
                   WHERE p.code IN :codes
               )"""
        ).bindparams(codes)
    )
    op.execute(
        sa.text("DELETE FROM permissions WHERE code IN :codes").bindparams(codes)
    )
    # A stored switch the application no longer knows makes the whole
    # configuration unavailable.
    op.execute(
        "UPDATE managed_configurations SET document = document - 'interested_parties'"
        " WHERE name = 'features' AND document ? 'interested_parties'"
    )
    for table in ("mcp_grants", "mcp_tokens", "mcp_consent_requests"):
        op.execute(
            sa.text(
                f"UPDATE {table} SET scopes = scopes - :scope"  # noqa: S608 fixed table names
                " WHERE scopes ? :scope"
            ).bindparams(scope=_PARTY_SCOPE)
        )
    op.execute(
        "UPDATE users SET pref_landing_page = '/'"
        " WHERE pref_landing_page LIKE '/interested\\_part%'"
    )
    op.execute("DELETE FROM audit_evidence WHERE entity_type = 'interested_party'")
    op.execute("DELETE FROM entity_changelogs WHERE entity_type = 'interested_party'")


def downgrade():
    op.drop_table("affected_links")
    op.execute(_PARTY_SCHEMA)
    for code, description in _PARTY_PERMISSION_DESCRIPTIONS.items():
        op.execute(
            sa.text(
                """INSERT INTO permissions (id, code, description, created_at)
                   SELECT :id, :code, :description, CURRENT_TIMESTAMP
                   WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :code)"""
            ).bindparams(id=str(uuid.uuid4()), code=code, description=description)
        )
