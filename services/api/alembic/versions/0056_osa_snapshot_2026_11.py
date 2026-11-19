"""Bring the Open Security Architecture catalogues up to OSA's 12 November 2026 data.

The catalogues were seeded from OSA's data of 8 October 2026. Since then OSA
has added frameworks, reissued some (PCI DSS as its 257 numbered requirements,
CIS Controls as v8.1.2), retired one and revised many mapping notes. This
applies ``catalogues/osa-2026-11-12.json`` to every framework OSA publishes.
Frameworks that are not OSA's are never read or written.

What happens to a control:

* One OSA still publishes keeps its id, scope decision, tags and everything
  attached to it, and takes OSA's current title, NIST mappings, mapping note,
  gaps and coverage estimate.
* One OSA publishes for the first time is added, in scope.
* One OSA no longer publishes is deleted when nothing depends on it. When
  something does (evidence, a risk, an owner, a link, a scope decision, tags, a
  description an administrator wrote, or a clause that stays filed under it) it
  is kept and marked as superseded.

Clause parents stay as the installation has them. OSA files 6.1.3 under 6.1
but publishes nothing else under 6.1, and audit scope treats a clause with
children as a heading only, so taking OSA's parent would stop 6.1 itself being
scoped.

What an administrator wrote stays theirs: a description, and a title that no
longer reads as the earlier snapshot gave it.

``KEEP_TITLES`` names the frameworks whose existing controls keep the titles
they have. On 1 November 2026 OSA replaced those standards' own control titles
with short descriptions of its own, and Keen keeps the standard's wording. OSA's
title is still recorded, as ``osa_title``.

A framework OSA has retired is treated control by control in the same way, and
its own row goes once no control is left and nothing names the framework.

Downgrading leaves the data as it is: the earlier snapshot is not restored.
"""

import json
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from app.core.datetime_utils import utc_now_naive
from sqlalchemy.dialects.postgresql import JSONB

revision = "0056_osa_snapshot_2026_11"
down_revision = "0055_osa_crosswalk_notes"
branch_labels = None
depends_on = None

DATA = Path(__file__).resolve().parents[1] / "catalogues/osa-2026-11-12.json"
OSA = "https://www.opensecurityarchitecture.org/"
KEEP_TITLES = ("iso_27001_2022", "iso_27002_2022", "bio2", "anssi")
CREDIT = (
    "\n\nOpen Security Architecture, CC BY-SA 4.0. "
    "Coverage is OSA's assessment; consult the framework's normative text. "
    "Offline snapshot "
)
# Tables whose rows belong to the catalogue itself, so do not count as a use.
CATALOGUE = ("control_items", "framework_clauses", "osa_control_mappings")

metadata = sa.MetaData()
frameworks = sa.Table(
    "frameworks",
    metadata,
    sa.Column("id", sa.Uuid),
    sa.Column("slug", sa.String),
    sa.Column("name", sa.String),
    sa.Column("version", sa.String),
    sa.Column("description", sa.Text),
    sa.Column("upstream_url", sa.Text),
    sa.Column("created_at", sa.DateTime),
)
controls = sa.Table(
    "control_items",
    metadata,
    sa.Column("id", sa.Uuid),
    sa.Column("framework_slug", sa.String),
    sa.Column("type", sa.String),
    sa.Column("ref", sa.String),
    sa.Column("title", sa.String),
    sa.Column("in_scope", sa.Boolean),
    sa.Column("tags", JSONB),
    sa.Column("metadata", JSONB),
    sa.Column("created_at", sa.DateTime),
)
clauses = sa.Table(
    "framework_clauses",
    metadata,
    sa.Column("id", sa.Uuid),
    sa.Column("framework_slug", sa.String),
    sa.Column("ref", sa.String),
    sa.Column("title", sa.String),
    sa.Column("parent_clause_id", sa.Uuid),
    sa.Column("sort_order", sa.Integer),
    sa.Column("metadata", JSONB),
    sa.Column("created_at", sa.DateTime),
    sa.Column("updated_at", sa.DateTime),
)
memberships = sa.Table(
    "osa_control_mappings",
    metadata,
    sa.Column("control_id", sa.Uuid),
    sa.Column("nist_ref", sa.String),
)
configurations = sa.Table(
    "managed_configurations",
    metadata,
    sa.Column("name", sa.String),
    sa.Column("document", sa.JSON),
)


def _referenced(bind, table: str) -> set:
    """Ids of ``table`` that a row outside the catalogue points at."""
    inspector = sa.inspect(bind)
    used: set = set()
    for name in inspector.get_table_names():
        if name in CATALOGUE:
            continue
        for key in inspector.get_foreign_keys(name):
            if key["referred_table"] == table and key["referred_columns"] == ["id"]:
                column = sa.column(key["constrained_columns"][0])
                used.update(
                    bind.execute(
                        sa.select(column)
                        .select_from(sa.table(name))
                        .where(column.is_not(None))
                        .distinct()
                    ).scalars()
                )
    return used


def _named(bind, slug: str) -> bool:
    """Whether anything outside the catalogue names the framework."""
    inspector = sa.inspect(bind)
    for name in inspector.get_table_names():
        if name in CATALOGUE:
            continue
        if "framework_slug" in {
            column["name"] for column in inspector.get_columns(name)
        }:
            column = sa.column("framework_slug")
            found = bind.execute(
                sa.select(column)
                .select_from(sa.table(name))
                .where(column == slug)
                .limit(1)
            ).first()
            if found:
                return True
    chosen = bind.execute(
        sa.select(configurations.c.document).where(
            configurations.c.name == "organisation-frameworks"
        )
    ).scalar()
    return bool(chosen) and (
        slug in (chosen.get("enabled") or []) or chosen.get("default") == slug
    )


def _in_use(row, used: set) -> bool:
    """Whether a control OSA dropped has anything of the installation's on it."""
    meta = row["metadata"] or {}
    return bool(
        row["id"] in used
        or not row["in_scope"]
        or row["tags"]
        or meta.get("description")
    )


def _retire(bind, table, rows, used: set, snapshot: str, in_use) -> None:
    """Delete the rows nothing depends on and mark the rest as superseded."""
    kept = [row for row in rows if in_use(row, used)]
    _update(
        bind,
        table,
        [
            {
                "row": row["id"],
                "metadata": {**(row["metadata"] or {}), "superseded": snapshot},
            }
            for row in kept
        ],
    )
    unused = {row["id"] for row in rows} - {row["id"] for row in kept}
    if unused:
        bind.execute(table.delete().where(table.c.id.in_(unused)))


def _update(bind, table, changes: list[dict]) -> None:
    """Apply each change's columns to the row it names, in one round trip."""
    if changes:
        columns = [name for name in changes[0] if name != "row"]
        bind.execute(
            table.update()
            .where(table.c.id == sa.bindparam("row"))
            .values({name: sa.bindparam(name) for name in columns}),
            changes,
        )


def _parented(bind, slug: str, surviving: set) -> set:
    """Clauses of the framework that a surviving clause sits under.

    A clause's parent link deletes its children with it, so a clause OSA no
    longer publishes stays while a clause that stays is filed under it, however
    many levels down.
    """
    parent_of = dict(
        bind.execute(
            sa.select(clauses.c.id, clauses.c.parent_clause_id).where(
                clauses.c.framework_slug == slug
            )
        ).all()
    )
    ancestors: set = set()
    for clause in surviving:
        parent = parent_of.get(clause)
        while parent is not None and parent not in ancestors:
            ancestors.add(parent)
            parent = parent_of.get(parent)
    return ancestors


def _merged(old: dict, new: dict, title: str) -> dict:
    """Current OSA metadata over what the row held, keeping the row's own keys."""
    meta = {**old, **new}
    meta.pop("superseded", None)
    if title != new["title"][:256]:
        meta["title"] = title
        meta["osa_title"] = new["title"]
    return meta


def _title(slug: str, row, new: str) -> str:
    """The title an existing row takes: OSA's, unless the row's is to be kept."""
    seeded = ((row["metadata"] or {}).get("title") or "")[:256]
    if slug in KEEP_TITLES or row["title"] != seeded:
        return row["title"]
    return new[:256]


def apply_snapshot(bind) -> None:
    data = json.loads(DATA.read_text())
    snapshot = data["retrieved"]
    now = utc_now_naive()
    osa_rows = controls.c.metadata["source"].astext == OSA
    # A database that never held the OSA catalogue is not given it here: the
    # release seed is what installs it, and this only brings it up to date.
    if bind.execute(sa.select(controls.c.id).where(osa_rows).limit(1)).first() is None:
        return
    used_controls = _referenced(bind, "control_items")
    used_clauses = _referenced(bind, "framework_clauses")

    for framework in data["frameworks"]:
        slug = framework["id"]
        url = OSA + "frameworks/" + framework["slug"] + "/"
        described = framework["description"] + CREDIT + snapshot + "."
        row = (
            bind.execute(sa.select(frameworks).where(frameworks.c.slug == slug))
            .mappings()
            .first()
        )
        if row is None:
            bind.execute(
                frameworks.insert().values(
                    id=uuid.uuid4(),
                    slug=slug,
                    name=framework["name"],
                    version=framework["metadata"].get("version"),
                    description=described,
                    upstream_url=url,
                    created_at=now,
                )
            )
        else:
            # A description an administrator rewrote no longer carries the credit.
            credited = CREDIT in (row["description"] or "")
            bind.execute(
                frameworks.update()
                .where(frameworks.c.slug == slug)
                .values(
                    name=framework["name"],
                    version=framework["metadata"].get("version"),
                    description=described if credited else row["description"],
                    upstream_url=url,
                )
            )

        held = {
            (item["type"], item["ref"]): item
            for item in bind.execute(
                sa.select(controls).where(controls.c.framework_slug == slug)
            ).mappings()
        }
        held_clauses = {
            item["ref"]: item
            for item in bind.execute(
                sa.select(clauses).where(clauses.c.framework_slug == slug)
            ).mappings()
        }
        refs = {item["id"] for item in framework["clauses"]}
        published, published_clauses, nist = set(), set(), []
        # Each published clause's row.
        clause_rows = {}
        added, changed, added_clauses, changed_clauses = [], [], [], []
        for order, item in enumerate(framework["clauses"]):
            ref = item["id"]
            # Only an ancestor OSA publishes is a parent; none is invented.
            parents = [
                r for r in refs if ref.startswith(r + ".") or ref.startswith(r + "-")
            ]
            new = {
                **item,
                "parent_ref": max(parents, key=len) if parents else None,
                "sort_order": order,
                "upstream_url": url,
                "source": data["source"],
                "license": data["license"],
                "license_url": data["license_url"],
                "source_commit": data["source_commit"],
                "osa_nist_controls": item["controls"],
                "osa_framework_metadata": framework["metadata"],
            }
            is_clause = slug == "iso_27001_2022" and not ref.startswith("A.")
            kind = "clause" if is_clause else "annex_control"
            existing = held.get((kind, ref))
            if existing is None:
                ident = uuid.uuid5(uuid.NAMESPACE_URL, OSA + slug + "/" + ref)
                title = item["title"][:256]
                added.append(
                    {
                        "id": ident,
                        "framework_slug": slug,
                        "type": kind,
                        "ref": ref,
                        "title": title,
                        "in_scope": True,
                        "tags": {},
                        "metadata": new,
                        "created_at": now,
                    }
                )
            else:
                ident = existing["id"]
                title = _title(slug, existing, item["title"])
                changed.append(
                    {
                        "row": ident,
                        "title": title,
                        "metadata": _merged(existing["metadata"] or {}, new, title),
                    }
                )
            published.add(ident)
            nist.extend(
                {"control_id": ident, "nist_ref": code}
                for code in sorted(set(item["controls"]))
            )
            if is_clause:
                published_clauses.add(ref)
                clause = held_clauses.get(ref)
                clause_rows[ref] = ident if clause is None else clause["id"]
                if clause is None:
                    added_clauses.append(
                        {
                            "id": ident,
                            "framework_slug": slug,
                            "ref": ref,
                            "title": title,
                            "parent_clause_id": None,
                            "sort_order": order,
                            "metadata": new,
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                else:
                    changed_clauses.append(
                        {
                            "row": clause["id"],
                            "title": title,
                            "sort_order": order,
                            "metadata": _merged(clause["metadata"] or {}, new, title),
                            "updated_at": now,
                        }
                    )

        if added:
            bind.execute(controls.insert(), added)
        _update(bind, controls, changed)
        if added_clauses:
            bind.execute(clauses.insert(), added_clauses)
        _update(bind, clauses, changed_clauses)
        bind.execute(
            memberships.delete().where(memberships.c.control_id.in_(published))
        )
        if nist:
            bind.execute(memberships.insert(), nist)
        dropped = [
            item
            for item in held.values()
            if item["id"] not in published
            and (item["metadata"] or {}).get("source") == OSA
        ]
        _retire(bind, controls, dropped, used_controls, snapshot, _in_use)
        dropped_clauses = [
            item
            for ref, item in held_clauses.items()
            if ref not in published_clauses
            and (item["metadata"] or {}).get("source") == OSA
        ]
        dropped_ids = {item["id"] for item in dropped_clauses}
        # Published clauses, clauses that are not OSA's, and dropped ones in use.
        surviving = (
            set(clause_rows.values())
            | ({row["id"] for row in held_clauses.values()} - dropped_ids)
            | (dropped_ids & used_clauses)
        )
        kept_clauses = used_clauses | _parented(bind, slug, surviving)
        _retire(
            bind,
            clauses,
            dropped_clauses,
            kept_clauses,
            snapshot,
            lambda item, used: item["id"] in used,
        )

    # A framework OSA has retired: its controls go the same way, then its row.
    current = {framework["id"] for framework in data["frameworks"]}
    retired = set(
        bind.execute(
            sa.select(controls.c.framework_slug)
            .where(osa_rows, controls.c.framework_slug.not_in(current))
            .distinct()
        ).scalars()
    )
    for slug in sorted(retired):
        rows = (
            bind.execute(
                sa.select(controls).where(controls.c.framework_slug == slug, osa_rows)
            )
            .mappings()
            .all()
        )
        _retire(bind, controls, rows, used_controls, snapshot, _in_use)
        left = bind.execute(
            sa.select(controls.c.id).where(controls.c.framework_slug == slug).limit(1)
        ).first()
        if left is None and not _named(bind, slug):
            bind.execute(frameworks.delete().where(frameworks.c.slug == slug))


def upgrade() -> None:
    apply_snapshot(op.get_bind())


def downgrade() -> None:
    """Nothing to undo in the schema, and the earlier snapshot is not restored."""
