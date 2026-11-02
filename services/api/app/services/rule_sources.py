"""Which event sources a mapping rule reads.

A rule names one source, or reads several through a source pattern. A pattern
is matched against the sources of the events Keen holds, so it reaches a new
source once that source has delivered events.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, aliased

from app.db.models import Event
from app.security.regex import compile_pattern
from app.services.errors import DomainError

# A source pattern is matched against at most this many sources.
MAX_SOURCES = 10_000


def distinct_sources(
    db: Session, condition: Any = None, limit: int | None = None
) -> list[str]:
    """Distinct sources of the events Keen holds, in order.

    A recursive skip scan reads one index entry per source, so the cost follows
    the number of sources and never the number of events. With ``condition``, a
    source is listed when at least one of its events meets it.
    """
    step = aliased(Event)
    sources = select(func.min(Event.source).label("source")).cte(
        "event_sources", recursive=True
    )
    sources = sources.union_all(
        select(
            select(func.min(step.source))
            .where(step.source > sources.c.source)
            .scalar_subquery()
        ).where(sources.c.source.is_not(None))
    )
    query = select(sources.c.source).where(sources.c.source.is_not(None))
    if condition is not None and condition is not True:
        query = query.where(exists().where(Event.source == sources.c.source, condition))
    query = query.order_by(sources.c.source)
    if limit is not None:
        query = query.limit(limit)
    return list(db.execute(query).scalars())


def reads_source(when: dict, source: str) -> bool:
    """Whether a rule's source conditions accept events from ``source``."""
    exact = str(when.get("source") or "").strip()
    if exact and exact != source:
        return False
    pattern = when.get("source_regex")
    return not pattern or bool(compile_pattern(pattern).search(source))


def rule_sources(db: Session, when: dict) -> list[str]:
    """The sources whose events a rule can match.

    A rule naming a source reads that source, so a preview can still explain
    why its events miss the rest of the rule. Otherwise each observed source
    its pattern accepts, or every observed source for a rule with no source
    condition.
    """
    exact = str(when.get("source") or "").strip()
    if exact:
        return [exact]
    observed = distinct_sources(db, limit=MAX_SOURCES + 1)
    if len(observed) > MAX_SOURCES:
        raise DomainError(
            409,
            f"Keen holds events from more than {MAX_SOURCES:,} sources, too many to "
            "match a source pattern against; name the source instead",
        )
    return [source for source in observed if reads_source(when, source)]
