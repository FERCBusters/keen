#!/usr/bin/env python3
"""Rebuild durable control/evidence aggregate stats.

Run inside the API container after applying migrations, or whenever aggregate
counter drift is suspected:

  python scripts/rebuild_control_evidence_stats.py

The normal runtime path is maintained by Postgres triggers on the mappings and
events tables; this script is a repair/backfill convenience.
"""

from __future__ import annotations

from app.db.session import SessionLocal
from app.services.control_evidence_stats import (
    clear_stats_caches,
    rebuild_control_evidence_stats,
    rebuild_framework_event_stats,
)


def main() -> None:
    db = SessionLocal()
    try:
        rows = rebuild_control_evidence_stats(db, clear_cache=False)
        event_rows = rebuild_framework_event_stats(db, clear_cache=False)
        db.commit()
        cache_deleted = clear_stats_caches()
        print(
            {
                "rebuilt_control_evidence_stats": rows,
                **event_rows,
                **cache_deleted,
            }
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
