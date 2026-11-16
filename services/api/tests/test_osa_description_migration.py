"""Migration 0055: a seeded OSA crosswalk note is no longer a control's description."""

import json
import uuid

from alembic import command
from sqlalchemy import text

from tests.test_link_migrations import release

BEFORE = "0054_drop_unused_columns"
AFTER = "0055_osa_crosswalk_notes"
OSA = "https://www.opensecurityarchitecture.org/"
SEEDED = (
    "Information security in supplier relationships\n\n"
    "SA-04 acquisition security requirements.\n\n"
    "Coverage gaps: Minor: ISO emphasizes ongoing supplier review.\n\n"
    "OSA estimated coverage: 85%"
)


def osa(**changes):
    meta = {
        "source": OSA,
        "title": "Information security in supplier relationships",
        "rationale": "SA-04 acquisition security requirements.",
        "gaps": "Minor: ISO emphasizes ongoing supplier review.",
        "coverage_pct": 85,
        "osa_nist_controls": ["SA-04"],
        "description": SEEDED,
    }
    meta.update(changes)
    return {key: value for key, value in meta.items() if value is not None}


def add_control(conn, ref, meta):
    ident = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO control_items"
            " (id, framework_slug, type, ref, title, in_scope, tags, metadata, created_at)"
            " VALUES (:id, 'iso', 'annex_control', :ref, 'Title', true, '{}',"
            " CAST(:meta AS jsonb), now())"
        ),
        {"id": ident, "ref": ref, "meta": json.dumps(meta)},
    )
    return ident


def add_clause(conn, ref, meta):
    ident = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO framework_clauses"
            " (id, framework_slug, ref, title, sort_order, metadata, created_at, updated_at)"
            " VALUES (:id, 'iso', :ref, 'Title', 0, CAST(:meta AS jsonb), now(), now())"
        ),
        {"id": ident, "ref": ref, "meta": json.dumps(meta)},
    )
    return ident


def meta_of(conn, table, ident):
    return conn.execute(
        text(f"SELECT metadata FROM {table} WHERE id = :id"),  # noqa: S608
        {"id": ident},
    ).scalar_one()


def test_only_a_description_still_as_seeded_is_removed_and_downgrade_restores_it():
    with release(BEFORE) as (conn, config):
        conn.execute(
            text(
                "INSERT INTO frameworks (id, slug, name, created_at)"
                " VALUES (:id, 'iso', 'ISO', now())"
            ),
            {"id": uuid.uuid4()},
        )
        seeded = add_control(conn, "A.5.19", osa())
        # The import left out the parts OSA did not supply.
        title_only = add_control(
            conn,
            "A.5.20",
            osa(rationale=None, gaps=None, description="Addressing security"),
        )
        conn.execute(
            text(
                "UPDATE control_items SET metadata = metadata - 'coverage_pct'"
                ' || \'{"title": "Addressing security"}\' WHERE id = :id'
            ),
            {"id": title_only},
        )
        rewritten = add_control(
            conn, "A.5.21", osa(description="Our suppliers sign the security schedule.")
        )
        blanked = add_control(conn, "A.5.22", osa(description=""))
        ours = add_control(
            conn, "K.1", {"source": "Keen", "title": "T", "description": "T"}
        )
        clause = add_clause(conn, "4.1", osa())
        conn.commit()

        command.upgrade(config, AFTER)
        after = meta_of(conn, "control_items", seeded)
        assert "description" not in after
        # What OSA said stays, each part in its own field.
        assert (after["rationale"], after["gaps"], after["coverage_pct"]) == (
            "SA-04 acquisition security requirements.",
            "Minor: ISO emphasizes ongoing supplier review.",
            85,
        )
        assert after["osa_nist_controls"] == ["SA-04"]
        assert "description" not in meta_of(conn, "control_items", title_only)
        assert "description" not in meta_of(conn, "framework_clauses", clause)
        # Wording an administrator wrote, or cleared, is theirs.
        assert (
            meta_of(conn, "control_items", rewritten)["description"]
            == "Our suppliers sign the security schedule."
        )
        assert meta_of(conn, "control_items", blanked)["description"] == ""
        assert meta_of(conn, "control_items", ours)["description"] == "T"

        command.downgrade(config, BEFORE)
        assert meta_of(conn, "control_items", seeded)["description"] == SEEDED
        assert meta_of(conn, "framework_clauses", clause)["description"] == SEEDED
        assert (
            meta_of(conn, "control_items", title_only)["description"]
            == "Addressing security"
        )
        assert meta_of(conn, "control_items", blanked)["description"] == SEEDED
        assert (
            meta_of(conn, "control_items", rewritten)["description"]
            == "Our suppliers sign the security schedule."
        )
        assert meta_of(conn, "control_items", ours)["description"] == "T"
