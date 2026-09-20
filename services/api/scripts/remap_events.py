#!/usr/bin/env python3
"""Re-apply rules to existing Keen events with optional filters.

Run inside the API container, for example:

  python scripts/remap_events.py --source jenkins --limit 0
  python scripts/remap_events.py --source loki --loki-query-name use_of_testbed_vpn_remote --limit 0

By default only events with no mappings are processed. Add --include-mapped to also
process already-mapped events; apply_rules() is idempotent and only adds missing
mapping pairs.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timezone
from typing import Any

from sqlalchemy import desc, text
from sqlalchemy.orm import Session

from app.api.utils import parse_iso_dt
from app.db.models import Event, Mapping
from app.db.session import SessionLocal
from app.ingest.common import apply_rules
from app.ingest.bookstack import apply_bookstack_config_mappings
from app.services.control_evidence_stats import (
    clear_stats_caches,
    rebuild_framework_event_stats,
)


def _coerce_range_dt(value: str | None, *, end: bool) -> datetime | None:
    if not value:
        return None
    txt = str(value).strip()
    if not txt:
        return None

    try:
        d = date.fromisoformat(txt)
        return datetime.combine(d, time.max if end else time.min)
    except Exception:
        pass

    dt = parse_iso_dt(txt)
    if not dt:
        raise SystemExit(f"Invalid date/datetime: {value!r}")
    try:
        dt = dt.astimezone(timezone.utc)
    except Exception:
        pass
    return dt.replace(tzinfo=None)


def _clean(value: str | None) -> str | None:
    txt = str(value or "").strip()
    return txt or None


def _build_query(db: Session, args: argparse.Namespace):
    qry = db.query(Event.id)

    if not args.include_mapped:
        has_mapping = db.query(Mapping.id).filter(Mapping.event_id == Event.id).exists()
        qry = qry.filter(~has_mapping)

    dt_from = _coerce_range_dt(args.from_date, end=False)
    dt_to = _coerce_range_dt(args.to_date, end=True)
    if dt_from is not None:
        qry = qry.filter(Event.timestamp >= dt_from)
    if dt_to is not None:
        qry = qry.filter(Event.timestamp <= dt_to)

    if args.source:
        qry = qry.filter(Event.source == args.source)
    if args.system:
        qry = qry.filter(Event.system == args.system)
    if args.action:
        qry = qry.filter(Event.action == args.action)
    if args.outcome:
        qry = qry.filter(Event.outcome == args.outcome)

    if args.jenkins_job:
        qry = qry.filter(
            text("(events.raw_pointer #>> '{jenkins,job}') = :jenkins_job")
        ).params(jenkins_job=args.jenkins_job)
    if args.loki_query_name:
        qry = qry.filter(
            text("(events.raw_pointer #>> '{loki,query_name}') = :loki_query_name")
        ).params(loki_query_name=args.loki_query_name)
    if args.cloudwatch_name:
        qry = qry.filter(
            text("(events.raw_pointer #>> '{cloudwatch_logs,name}') = :cloudwatch_name")
        ).params(cloudwatch_name=args.cloudwatch_name)

    if args.summary_contains:
        qry = qry.filter(Event.summary.ilike(f"%{args.summary_contains}%"))

    qry = qry.order_by(desc(Event.timestamp))
    if args.limit and args.limit > 0:
        qry = qry.limit(args.limit)
    return qry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-apply rules to existing events")
    parser.add_argument(
        "--source", type=_clean, help="Filter Event.source, e.g. jenkins"
    )
    parser.add_argument("--system", type=_clean, help="Filter Event.system")
    parser.add_argument("--action", type=_clean, help="Filter Event.action")
    parser.add_argument("--outcome", type=_clean, help="Filter Event.outcome")
    parser.add_argument(
        "--from-date", type=_clean, help="Inclusive YYYY-MM-DD or ISO datetime"
    )
    parser.add_argument(
        "--to-date", type=_clean, help="Inclusive YYYY-MM-DD or ISO datetime"
    )
    parser.add_argument(
        "--jenkins-job", type=_clean, help="Filter raw_pointer.jenkins.job"
    )
    parser.add_argument(
        "--loki-query-name", type=_clean, help="Filter raw_pointer.loki.query_name"
    )
    parser.add_argument(
        "--cloudwatch-name", type=_clean, help="Filter raw_pointer.cloudwatch_logs.name"
    )
    parser.add_argument(
        "--summary-contains",
        type=_clean,
        help="Case-insensitive summary substring filter",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum events to process; 0 means no limit",
    )
    parser.add_argument(
        "--include-mapped",
        action="store_true",
        help="Also process events that already have mappings; only missing mappings are added",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show candidate count without writing mappings",
    )
    parser.add_argument(
        "--no-cache-clear",
        action="store_true",
        help="Do not clear aggregate cache after new mappings are created",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db = SessionLocal()
    try:
        qry = _build_query(db, args)
        event_ids = []
        for row in qry.all():
            # SQLAlchemy ORM query results for a single selected column are
            # usually one-item tuples/Rows, but keep this tolerant across
            # SQLAlchemy minor versions.
            if isinstance(row, tuple):
                event_ids.append(row[0])
            elif hasattr(row, "_mapping"):
                event_ids.append(row[0])
            else:
                event_ids.append(row)
        print(f"Found {len(event_ids)} event(s) to remap")

        if args.dry_run:
            print("Dry run: no mappings were written")
            return

        processed = 0
        created_total = 0
        rule_mappings_created = 0
        bookstack_config_mappings_created = 0
        failed: list[tuple[Any, str]] = []

        for event_id in event_ids:
            ev = db.get(Event, event_id)
            if ev is None:
                continue
            processed += 1
            try:
                rule_created = int(apply_rules(db, ev) or 0)
                bookstack_created = int(apply_bookstack_config_mappings(db, ev) or 0)
                rule_mappings_created += rule_created
                bookstack_config_mappings_created += bookstack_created
                created_total += rule_created + bookstack_created
                db.commit()
            except Exception as exc:
                db.rollback()
                failed.append((event_id, str(exc)))

        cache_deleted: dict[str, int] | int = 0
        stats_rebuilt: dict[str, int] = {}
        if created_total:
            # Remapping is an administrative repair operation. Recompute the
            # small distinct-event summary table so the controls.html KPI cannot
            # drift if previous migrations/scripts left it stale. Per-control
            # aggregates are still maintained by triggers.
            stats_rebuilt = rebuild_framework_event_stats(db, clear_cache=False)
            db.commit()
            if not args.no_cache_clear:
                cache_deleted = clear_stats_caches()

        print(
            {
                "processed_events": processed,
                "created_mappings": created_total,
                "rule_mappings_created": rule_mappings_created,
                "bookstack_config_mappings_created": bookstack_config_mappings_created,
                "stats_rebuilt": stats_rebuilt,
                "failed_events": len(failed),
                "include_mapped": bool(args.include_mapped),
                "dry_run": False,
                "cache_keys_deleted": cache_deleted,
                "filters": {
                    "source": args.source,
                    "system": args.system,
                    "action": args.action,
                    "outcome": args.outcome,
                    "from_date": args.from_date,
                    "to_date": args.to_date,
                    "jenkins_job": args.jenkins_job,
                    "loki_query_name": args.loki_query_name,
                    "cloudwatch_name": args.cloudwatch_name,
                    "summary_contains": args.summary_contains,
                    "limit": args.limit,
                },
            }
        )

        for event_id, err in failed[:20]:
            print(f"Failed event {event_id}: {err}")
        if len(failed) > 20:
            print(f"... {len(failed) - 20} additional failure(s) omitted")
    finally:
        db.close()


if __name__ == "__main__":
    main()
