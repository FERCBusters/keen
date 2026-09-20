#!/usr/bin/env python3
"""Find or delete rule mappings no longer produced by current rules.yml.

This is intended for safe cleanup after tightening rules. It only considers
Mapping.method == "rule"; manual/import mappings are never touched.

Examples:

  # Preview Jenkins rule mappings that current rules.yml would no longer create.
  python scripts/prune_rule_mappings.py --source jenkins --limit 0

  # Delete the previewed stale Jenkins rule mappings and clear aggregate cache.
  python scripts/prune_rule_mappings.py --source jenkins --limit 0 --delete

  # Preview a specific source/action/control combination.
  python scripts/prune_rule_mappings.py --source rss --control-ref A.5.7 --limit 0
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timezone
from typing import Any

from sqlalchemy import desc, text
from sqlalchemy.orm import Session

from app.api.utils import parse_iso_dt
from app.core.config import settings
from app.db.models import ControlItem, Event, Mapping
from app.db.session import SessionLocal
from app.mapping.rules import evaluate_by_framework, load_rules
from app.services.control_evidence_stats import (
    clear_stats_caches,
    rebuild_framework_event_stats,
)


def _clean(value: str | None) -> str | None:
    txt = str(value or "").strip()
    return txt or None


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


def _event_dict(ev: Event) -> dict[str, Any]:
    return {
        "source": ev.source,
        "system": ev.system,
        "actor": ev.actor,
        "action": ev.action,
        "outcome": ev.outcome,
        "severity": ev.severity,
        "summary": ev.summary,
        "raw_pointer": ev.raw_pointer,
        "normalized_payload": ev.normalized_payload,
    }


def _expected_pairs(ev: Event, rules) -> set[tuple[str, str]]:
    by_fw = evaluate_by_framework(_event_dict(ev), rules)
    return {
        (str(framework_slug), str(ref))
        for framework_slug, refs in by_fw.items()
        for ref in refs
    }


def _build_query(db: Session, args: argparse.Namespace):
    qry = (
        db.query(Mapping, Event, ControlItem)
        .join(Event, Mapping.event_id == Event.id)
        .join(ControlItem, Mapping.control_item_id == ControlItem.id)
        .filter(Mapping.method == "rule")
    )

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
    if args.framework:
        qry = qry.filter(ControlItem.framework_slug == args.framework)
    if args.control_ref:
        qry = qry.filter(ControlItem.ref == args.control_ref)

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

    qry = qry.order_by(desc(Event.timestamp), Event.id, ControlItem.ref)
    if args.limit and args.limit > 0:
        qry = qry.limit(args.limit)
    return qry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find/delete rule mappings that current rules.yml would no longer create"
        )
    )
    parser.add_argument("--source", type=_clean, help="Filter Event.source")
    parser.add_argument("--system", type=_clean, help="Filter Event.system")
    parser.add_argument("--action", type=_clean, help="Filter Event.action")
    parser.add_argument("--outcome", type=_clean, help="Filter Event.outcome")
    parser.add_argument(
        "--framework", type=_clean, help="Filter ControlItem.framework_slug"
    )
    parser.add_argument("--control-ref", type=_clean, help="Filter ControlItem.ref")
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
        help="Maximum rule mappings to scan; 0 means no limit",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=20,
        help="Maximum stale mapping samples to print",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete stale rule mappings. Omit for dry-run/preview mode.",
    )
    parser.add_argument(
        "--no-cache-clear",
        action="store_true",
        help="Do not clear aggregate cache after deleting mappings",
    )
    return parser.parse_args()


def _delete_mapping_ids(db: Session, mapping_ids: list[Any]) -> int:
    deleted = 0
    batch_size = 1000
    for i in range(0, len(mapping_ids), batch_size):
        batch = mapping_ids[i : i + batch_size]
        deleted += (
            db.query(Mapping)
            .filter(Mapping.id.in_(batch))
            .delete(synchronize_session=False)
        )
    return deleted


def main() -> None:
    args = parse_args()
    rules = load_rules(settings.rules_path)
    db = SessionLocal()
    try:
        qry = _build_query(db, args)
        expected_cache: dict[Any, set[tuple[str, str]]] = {}
        stale_ids: list[Any] = []
        samples: list[dict[str, Any]] = []
        scanned = 0

        for mapping, event, control in qry.all():
            scanned += 1
            expected = expected_cache.get(event.id)
            if expected is None:
                expected = _expected_pairs(event, rules)
                expected_cache[event.id] = expected

            pair = (str(control.framework_slug), str(control.ref))
            if pair in expected:
                continue

            stale_ids.append(mapping.id)
            if len(samples) < max(0, args.sample):
                samples.append(
                    {
                        "mapping_id": str(mapping.id),
                        "event_id": str(event.id),
                        "timestamp": (
                            event.timestamp.isoformat() if event.timestamp else None
                        ),
                        "source": event.source,
                        "system": event.system,
                        "action": event.action,
                        "outcome": event.outcome,
                        "control": f"{control.framework_slug} {control.ref}",
                        "summary": (event.summary or "")[:180],
                    }
                )

        deleted = 0
        cache_deleted = 0
        stats_rebuilt: dict[str, int] = {}
        if args.delete and stale_ids:
            deleted = _delete_mapping_ids(db, stale_ids)
            stats_rebuilt = rebuild_framework_event_stats(db, clear_cache=False)
            db.commit()
            if not args.no_cache_clear:
                cache_deleted = clear_stats_caches()
        elif args.delete:
            db.commit()
        else:
            db.rollback()

        print(
            {
                "scanned_rule_mappings": scanned,
                "events_evaluated": len(expected_cache),
                "stale_rule_mappings": len(stale_ids),
                "deleted_rule_mappings": deleted,
                "dry_run": not bool(args.delete),
                "stats_rebuilt": stats_rebuilt,
                "cache_keys_deleted": cache_deleted,
                "filters": {
                    "source": args.source,
                    "system": args.system,
                    "action": args.action,
                    "outcome": args.outcome,
                    "framework": args.framework,
                    "control_ref": args.control_ref,
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

        if samples:
            print("\nSample stale mappings:")
            for item in samples:
                print(item)

        if stale_ids and not args.delete:
            print(
                "\nDry run only. Re-run with --delete to remove these stale "
                "Mapping.method='rule' rows. Manual/import mappings are not touched."
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
