"""Reading many events' stored payloads without holding them all at once.

An event's raw pointer and normalised payload hold the evidence it was stored
with: up to a webhook delivery's ten megabytes, decoded into Python objects
that cost several times their text. A page of events read whole would hold
every payload on the page together, in a process other requests share. Pages
of events are therefore read without their payloads, and the payloads are read
afterwards in groups whose stored text stays within PAYLOAD_TEXT_PER_READ, so
the memory a page needs follows the largest single event, not the page.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator
from typing import Any

from sqlalchemy import Text, cast, func, select
from sqlalchemy.orm import Session, defer

from app.db.models import Event

# The most stored payload text one read brings into the process. One event
# larger than this is read on its own.
PAYLOAD_TEXT_PER_READ = 1024 * 1024
PAYLOADS = ("raw_pointer", "normalized_payload")


def without_payloads():
    """Query options that leave each event's payload columns unread."""
    return (defer(Event.raw_pointer), defer(Event.normalized_payload))


def _text_length(column):
    return func.coalesce(func.octet_length(cast(column, Text)), 0)


def payloads(
    db: Session, ids: Iterable[uuid.UUID]
) -> Iterator[tuple[uuid.UUID, Any, Any]]:
    """Each event's ``(id, raw_pointer, normalized_payload)``, in ``ids`` order.

    Groups of events are read together while their stored text fits
    PAYLOAD_TEXT_PER_READ; a caller that keeps only what it derives from each
    payload holds one group at a time.
    """
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return
    sizes: dict[uuid.UUID, int] = {
        event_id: int(size)
        for event_id, size in db.execute(
            select(
                Event.id,
                _text_length(Event.raw_pointer)
                + _text_length(Event.normalized_payload),
            ).where(Event.id.in_(wanted))
        )
    }
    group: list[uuid.UUID] = []
    held = 0
    for event_id in wanted:
        size = sizes.get(event_id, 0)
        if group and held + size > PAYLOAD_TEXT_PER_READ:
            yield from _read(db, group)
            group, held = [], 0
        group.append(event_id)
        held += size
    # At least one event was wanted, so the last group holds one.
    yield from _read(db, group)


def _read(db: Session, ids: list[uuid.UUID]):
    found = {
        row.id: (row.raw_pointer, row.normalized_payload)
        for row in db.execute(
            select(Event.id, Event.raw_pointer, Event.normalized_payload).where(
                Event.id.in_(ids)
            )
        )
    }
    for event_id in ids:
        if event_id in found:
            yield (event_id, *found[event_id])
