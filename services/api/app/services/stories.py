"""Stories: a curated set of events, records and notes that explain what happened.

A story collects evidence that belongs together, such as the events of an
incident, the risk it realised, the policy that applied and the meeting that
reviewed it, with a note for each step. Items sit on a timeline and on a flow
chart whose boxes and links the author arranges. Each record's own page lists
the stories that include it. Stories are sampled into audits and questioned
like other records.

Permissions:

- ``stories.read`` views stories and adds comments.
- ``stories.manage`` creates stories and edits or deletes the stories its
  holder created.
- ``stories.admin`` edits or deletes any story.

A story is a container, so a reader sees each item only when they can read
the referenced record themselves: events need ``events.read`` and diary
visibility, risks their own read rule, and ISMS records (documents, assets,
objectives, SWOT items, people, vendors, effectiveness measures and meeting
minutes) ISMS read access.
Items a reader cannot see stay in the story as placeholders without the
record's identifier, label or note, so the shape of the story remains while
its restricted content does not. Adding an item needs the same read access.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.references import try_uuid
from app.db.models import (
    ControlItem,
    Event,
    Mapping,
    Story,
    StoryComment,
    StoryItem,
    StoryLink,
    User,
)
from app.security.diary_visibility import is_diary_event_visible
from app.security.permissions import has_permission
from app.services.context import ChangeContext
from app.services.entity_changelog import (
    list_entity_changelogs,
    record_entity_changelog,
)
from app.services.errors import DomainError
from app.services.isms.common import _by_id_or_404, _clean_framework, _utcnow
from app.services.isms.related_records import (
    resolve_target,
    search_targets,
    target_summary,
)

RECORD_KINDS = (
    "risk",
    "document",
    "asset",
    "objective",
    "swot",
    "person",
    "vendor",
    "effectiveness_measure",
    "meeting",
)
KINDS = ("event", *RECORD_KINDS, "note")
STATUSES = ("open", "closed")
MAX_ITEMS = 200
MAX_LINKS = 400
MAX_COMMENTS = 500
MAX_COORDINATE = 20000
# Boxes a new item lands on before the author arranges the flow chart.
COLUMNS, COLUMN_WIDTH, ROW_HEIGHT, MARGIN = 4, 240, 130, 40
KIND_LABELS = {
    "event": "Event",
    "risk": "Risk",
    "document": "Document",
    "asset": "Asset",
    "objective": "Objective",
    "swot": "SWOT item",
    "person": "Person",
    "vendor": "Vendor",
    "effectiveness_measure": "Effectiveness measure",
    "meeting": "Meeting minutes",
    "note": "Note",
}
# The stories that include one record are listed on its page, up to this many.
MAX_RELATED = 200

Kind = Literal[
    "event",
    "risk",
    "document",
    "asset",
    "objective",
    "swot",
    "person",
    "vendor",
    "effectiveness_measure",
    "meeting",
    "note",
]
Status = Literal["open", "closed"]


class DraftItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind
    entity_id: uuid.UUID | None = None
    label: str = Field("", max_length=256)
    note: str = Field("", max_length=4000)
    occurred_at: datetime | None = None


class StoryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=1, max_length=256)
    summary: str | None = Field(None, max_length=20000)
    status: Status | None = None
    items: list[DraftItem] | None = Field(None, max_length=MAX_ITEMS)
    expected_version: int | None = Field(None, ge=1)


class ItemsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[DraftItem] = Field(min_length=1, max_length=MAX_ITEMS)
    expected_version: int = Field(ge=1)


class ItemPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(None, max_length=256)
    note: str | None = Field(None, max_length=4000)
    occurred_at: datetime | None = None
    clear_occurred_at: bool = False
    expected_version: int = Field(ge=1)


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: uuid.UUID
    x: int = Field(ge=0, le=MAX_COORDINATE, strict=True)
    y: int = Field(ge=0, le=MAX_COORDINATE, strict=True)


class DraftLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_item_id: uuid.UUID
    to_item_id: uuid.UUID
    label: str = Field("", max_length=128)


class LayoutPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    positions: list[Position] = Field(default_factory=list, max_length=MAX_ITEMS)
    links: list[DraftLink] = Field(default_factory=list, max_length=MAX_LINKS)
    expected_version: int = Field(ge=1)


class CommentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=5000)


# --- Access -------------------------------------------------------------------


def can_read_stories(db: Session, user: User) -> bool:
    return any(
        has_permission(db, user, code)
        for code in ("stories.read", "stories.manage", "stories.admin")
    )


def can_manage_stories(db: Session, user: User) -> bool:
    return has_permission(db, user, "stories.manage") or has_permission(
        db, user, "stories.admin"
    )


def can_admin_stories(db: Session, user: User) -> bool:
    return has_permission(db, user, "stories.admin")


def require_read(db: Session, user: User) -> None:
    if not can_read_stories(db, user):
        raise DomainError(403, "stories.read permission required")


def require_manage(db: Session, user: User) -> None:
    if not can_manage_stories(db, user):
        raise DomainError(403, "stories.manage permission required")


def can_edit(db: Session, user: User, story: Story) -> bool:
    return can_admin_stories(db, user) or (
        can_manage_stories(db, user) and story.created_by_user_id == user.id
    )


def _story_or_404(db: Session, story_id, *, lock=False) -> Story:
    return _by_id_or_404(db, Story, story_id, "Story", lock=lock)


def _editable_story(db: Session, user: User, story_id) -> Story:
    """The story, row-locked, when the caller may change it.

    Every change holds the story's row lock until it commits, so overlapping
    edits run in turn and each checks the version the previous one left.
    """
    require_manage(db, user)
    row = _story_or_404(db, story_id, lock=True)
    if not can_edit(db, user, row):
        raise DomainError(
            403,
            "You can edit the stories you created; stories.admin covers every story",
        )
    return row


def _version(row: Story, expected) -> None:
    if expected is None or row.version != expected:
        raise DomainError(409, "This story changed. Reload before saving.")


def _text(value, *, max_len, label, required=False) -> str:
    text = " ".join(str(value or "").split()) if label == "title" else str(value or "")
    text = text.strip()
    if required and not text:
        raise DomainError(400, f"{label} is required")
    if len(text) > max_len:
        raise DomainError(400, f"{label} is too long")
    return text


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


# --- Item resolution ----------------------------------------------------------


def _visible_event(db: Session, user: User, entity_id) -> Event | None:
    from app.services.features import state

    if not state(db).enabled("evidence") or not has_permission(db, user, "events.read"):
        return None
    row = db.get(Event, entity_id)
    if row is None:
        return None
    if (row.source or "") == "diary" and not is_diary_event_visible(db, user, row.id):
        return None
    return row


def _resolve(db: Session, user: User, kind: str, entity_id):
    """The readable record behind an item, or ``None`` when withheld."""
    if kind == "event":
        return _visible_event(db, user, entity_id)
    return resolve_target(db, user, kind, entity_id, required=False)


def _require_record(db: Session, user: User, draft: DraftItem) -> None:
    """Adding an item needs the record, readable by the author."""
    if draft.kind == "note":
        if draft.entity_id is not None:
            raise DomainError(400, "A note has no record")
        if not draft.label.strip() and not draft.note.strip():
            raise DomainError(400, "A note needs a label or text")
        return
    if draft.entity_id is None:
        raise DomainError(400, f"{KIND_LABELS[draft.kind]} items need a record")
    if draft.kind == "event":
        from app.services.features import require_entity

        require_entity(db, "event")
        if not has_permission(db, user, "events.read"):
            raise DomainError(403, "events.read permission required")
        if _visible_event(db, user, draft.entity_id) is None:
            raise DomainError(404, "Event not found")
        return
    resolve_target(db, user, draft.kind, draft.entity_id)


def _event_controls(db: Session, event_ids: list, framework: str) -> dict:
    """Controls mapped to each event in ``framework``, keyed by event id."""
    if not event_ids:
        return {}
    rows = (
        db.query(Mapping.event_id, ControlItem.id, ControlItem.ref, ControlItem.title)
        .join(ControlItem, ControlItem.id == Mapping.control_item_id)
        .filter(
            Mapping.event_id.in_(event_ids), ControlItem.framework_slug == framework
        )
        .order_by(ControlItem.ref)
        .limit(MAX_ITEMS * 50)
        .all()
    )
    out: dict = {}
    for event_id, control_id, ref, title in rows:
        out.setdefault(event_id, []).append(
            {"id": str(control_id), "ref": ref, "title": title}
        )
    return out


def story_controls(db: Session, story_id, framework: str) -> list[dict]:
    """Every control the story's events map to in ``framework``."""
    event_ids = [
        row[0]
        for row in db.query(StoryItem.entity_id)
        .filter(StoryItem.story_id == story_id, StoryItem.kind == "event")
        .limit(MAX_ITEMS)
        .all()
    ]
    seen: dict = {}
    for controls in _event_controls(db, event_ids, framework).values():
        for control in controls:
            seen.setdefault(control["id"], control)
    return sorted(seen.values(), key=lambda c: (c["ref"], c["id"]))


def _item_out(db, user, item: StoryItem, framework: str, controls_by_event: dict):
    base = {
        "id": str(item.id),
        "kind": item.kind,
        "kind_label": KIND_LABELS[item.kind],
        "position": item.position,
        "x": item.x,
        "y": item.y,
    }
    if item.kind == "note":
        return {
            **base,
            "entity_id": None,
            "label": item.label,
            "note": item.note,
            "occurred_at": item.occurred_at.isoformat() if item.occurred_at else None,
            "title": item.label or "Note",
            "url": None,
            "withheld": False,
        }
    record = _resolve(db, user, item.kind, item.entity_id)
    if record is None:
        return {
            **base,
            "entity_id": None,
            "label": "",
            "note": "",
            "occurred_at": None,
            "title": f"{KIND_LABELS[item.kind]} you cannot view",
            "url": None,
            "withheld": True,
        }
    occurred = item.occurred_at
    if item.kind == "event":
        occurred = occurred or record.timestamp
        detail = {
            "title": record.summary,
            "url": f"/event.html?id={record.id}",
            "source": record.source,
            "system": record.system,
            "controls": controls_by_event.get(record.id, []),
        }
    else:
        summary = target_summary(item.kind, record)
        detail = {"title": summary["title"], "url": summary["url"]}
    return {
        **base,
        "entity_id": str(item.entity_id),
        "label": item.label,
        "note": item.note,
        "occurred_at": occurred.isoformat() if occurred else None,
        "withheld": False,
        **detail,
    }


def summary(row: Story, *, created_by: str | None = None, item_count=None) -> dict:
    return {
        "id": str(row.id),
        "title": row.title,
        "summary": row.summary,
        "status": row.status,
        "version": row.version,
        "created_by_user_id": str(row.created_by_user_id)
        if row.created_by_user_id
        else None,
        "created_by": created_by,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "item_count": item_count,
    }


def _username(db: Session, user_id) -> str | None:
    if not user_id:
        return None
    return db.query(User.username).filter(User.id == user_id).scalar()


def output(db: Session, user: User, row: Story, framework: str) -> dict:
    items = (
        db.query(StoryItem)
        .filter(StoryItem.story_id == row.id)
        .order_by(StoryItem.position, StoryItem.id)
        .limit(MAX_ITEMS)
        .all()
    )
    event_ids = [item.entity_id for item in items if item.kind == "event"]
    controls_by_event = _event_controls(db, event_ids, framework)
    order = {item.id: index for index, item in enumerate(items)}
    links = sorted(
        db.query(StoryLink).filter(StoryLink.story_id == row.id).limit(MAX_LINKS).all(),
        key=lambda link: (
            order.get(link.from_item_id, MAX_ITEMS),
            order.get(link.to_item_id, MAX_ITEMS),
        ),
    )
    comment_count = (
        db.query(func.count(StoryComment.id))
        .filter(StoryComment.story_id == row.id)
        .scalar()
    )
    out = summary(
        row, created_by=_username(db, row.created_by_user_id), item_count=len(items)
    )
    out["framework"] = framework
    out["can_edit"] = can_edit(db, user, row)
    out["items"] = [
        _item_out(db, user, item, framework, controls_by_event) for item in items
    ]
    out["links"] = [
        {
            "id": str(link.id),
            "from_item_id": str(link.from_item_id),
            "to_item_id": str(link.to_item_id),
            "label": link.label,
        }
        for link in links
    ]
    controls: dict = {}
    for item in out["items"]:
        for control in item.get("controls") or []:
            controls.setdefault(control["id"], control)
    out["controls"] = sorted(controls.values(), key=lambda c: (c["ref"], c["id"]))
    out["comment_count"] = int(comment_count or 0)
    return out


def _note(db, row, action, detail, user, context):
    """A changelog entry for a change inside the story, named by its title."""
    _record(
        db,
        row,
        action,
        {"title": row.title},
        {"title": row.title, **detail},
        user,
        context,
    )


def _record(db, row, action, before, after, user, context: ChangeContext | None):
    """Story changes are evidence of how the story was assembled."""
    record_entity_changelog(
        db,
        entity_type="story",
        entity_id=row.id,
        action=action,
        before=before,
        after=after,
        user=user,
        request_method=context.method if context else None,
        request_path=context.path if context else None,
    )


# --- Reads --------------------------------------------------------------------


def _listing(db: Session, user: User, rows: list[Story]) -> list[dict]:
    """Stories as list entries, with their item counts and authors."""
    ids = [row.id for row in rows]
    counts = dict(
        db.query(StoryItem.story_id, func.count(StoryItem.id))
        .filter(StoryItem.story_id.in_(ids))
        .group_by(StoryItem.story_id)
        .all()
        if ids
        else []
    )
    names = dict(
        db.query(User.id, User.username)
        .filter(
            User.id.in_(
                {row.created_by_user_id for row in rows if row.created_by_user_id}
            )
        )
        .all()
        if rows
        else []
    )
    return [
        {
            **summary(
                row,
                created_by=names.get(row.created_by_user_id),
                item_count=int(counts.get(row.id, 0)),
            ),
            "can_edit": can_edit(db, user, row),
        }
        for row in rows
    ]


def get_story(*, story_id, framework, user, db):
    require_read(db, user)
    return output(db, user, _story_or_404(db, story_id), _clean_framework(framework))


def list_stories(*, user, db, q="", status="", limit=50, offset=0):
    require_read(db, user)
    qry = db.query(Story)
    if status:
        if status not in STATUSES:
            raise DomainError(400, "Unknown status")
        qry = qry.filter(Story.status == status)
    if q:
        needle = f"%{q[:256]}%"
        qry = qry.filter(or_(Story.title.ilike(needle), Story.summary.ilike(needle)))
    total = qry.count()
    limit, offset = min(max(limit, 1), 200), max(offset, 0)
    rows = (
        qry.order_by(Story.updated_at.desc(), Story.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "items": _listing(db, user, rows),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def related_stories(*, kind, entity_id, user, db):
    """The stories that include one record, for the record's own page.

    The caller must be able to read the record itself, as they can on its
    page, so the list never confirms that a record they cannot see belongs to
    a story.
    """
    require_read(db, user)
    if kind not in KINDS or kind == "note":
        raise DomainError(400, "Unknown story item kind")
    record_id = try_uuid(entity_id)
    record = _resolve(db, user, kind, record_id) if record_id else None
    if record is None:
        raise DomainError(404, f"{KIND_LABELS[kind]} not found")
    rows = (
        db.query(Story)
        .join(StoryItem, StoryItem.story_id == Story.id)
        .filter(StoryItem.kind == kind, StoryItem.entity_id == record.id)
        .order_by(Story.updated_at.desc(), Story.id)
        .limit(MAX_RELATED)
        .all()
    )
    return {
        "kind": kind,
        "entity_id": str(record.id),
        "items": _listing(db, user, rows),
    }


def choices(*, kind, q, user, db):
    """Readable ISMS records and risks to add to a story."""
    require_manage(db, user)
    if kind not in RECORD_KINDS:
        raise DomainError(400, "Unknown story item kind")
    return {"items": search_targets(db, user, kind, q, limit=50)}


def changelog(*, story_id, limit, offset, user, db):
    require_read(db, user)
    row = _story_or_404(db, story_id)
    return list_entity_changelogs(
        db, entity_type="story", entity_id=row.id, limit=limit, offset=offset
    )


# --- Writes -------------------------------------------------------------------


def _next_position(db: Session, story_id) -> int:
    current = (
        db.query(func.max(StoryItem.position))
        .filter(StoryItem.story_id == story_id)
        .scalar()
    )
    return int(current) + 1 if current is not None else 0


def _add_items(db: Session, user: User, row: Story, drafts: list[DraftItem]) -> int:
    """Add the drafts the story lacks; returns how many were added."""
    for draft in drafts:
        _require_record(db, user, draft)
    existing = {
        (kind, entity_id)
        for kind, entity_id in db.query(StoryItem.kind, StoryItem.entity_id)
        .filter(StoryItem.story_id == row.id, StoryItem.kind != "note")
        .all()
    }
    count = (
        db.query(func.count(StoryItem.id)).filter(StoryItem.story_id == row.id).scalar()
    )
    position = _next_position(db, row.id)
    added = 0
    for draft in drafts:
        key = (draft.kind, draft.entity_id)
        if draft.kind != "note":
            if key in existing:
                continue
            existing.add(key)
        if count + added >= MAX_ITEMS:
            raise DomainError(400, f"A story holds up to {MAX_ITEMS} items")
        db.add(
            StoryItem(
                story_id=row.id,
                kind=draft.kind,
                entity_id=draft.entity_id,
                label=_text(draft.label, max_len=256, label="label"),
                note=_text(draft.note, max_len=4000, label="note"),
                occurred_at=_naive(draft.occurred_at),
                position=position,
                x=MARGIN + (position % COLUMNS) * COLUMN_WIDTH,
                y=MARGIN + (position // COLUMNS) * ROW_HEIGHT,
            )
        )
        position += 1
        added += 1
    return added


def create_story(*, payload: StoryPayload, framework, user, db, context):
    require_manage(db, user)
    now = _utcnow()
    row = Story(
        title=_text(payload.title, max_len=256, label="title", required=True),
        summary=_text(payload.summary, max_len=20000, label="summary"),
        status=payload.status or "open",
        version=1,
        created_by_user_id=user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    added = _add_items(db, user, row, payload.items or [])
    after = summary(row, item_count=added)
    _record(db, row, "created", None, after, user, context)
    db.commit()
    return output(db, user, row, _clean_framework(framework))


def update_story(*, story_id, payload: StoryPayload, framework, user, db, context):
    row = _editable_story(db, user, story_id)
    _version(row, payload.expected_version)
    if payload.items is not None:
        raise DomainError(400, "Use the items action to add items to a story")
    before = summary(row)
    if payload.title is not None:
        row.title = _text(payload.title, max_len=256, label="title", required=True)
    if payload.summary is not None:
        row.summary = _text(payload.summary, max_len=20000, label="summary")
    if payload.status is not None:
        row.status = payload.status
    row.version += 1
    row.updated_at = _utcnow()
    _record(db, row, "updated", before, summary(row), user, context)
    db.commit()
    return output(db, user, row, _clean_framework(framework))


def delete_story(*, story_id, expected_version, user, db, context):
    row = _editable_story(db, user, story_id)
    _version(row, expected_version)
    _record(db, row, "deleted", summary(row), None, user, context)
    # Items, links and comments cascade with the story.
    db.delete(row)
    db.commit()
    return {"ok": True}


def add_items(*, story_id, payload: ItemsPayload, framework, user, db, context):
    row = _editable_story(db, user, story_id)
    _version(row, payload.expected_version)
    added = _add_items(db, user, row, payload.items)
    row.version += 1
    row.updated_at = _utcnow()
    _note(
        db,
        row,
        "items_added",
        {"items_added": added, "kinds": sorted({d.kind for d in payload.items})},
        user,
        context,
    )
    db.commit()
    return output(db, user, row, _clean_framework(framework))


def _item_or_404(db: Session, row: Story, item_id) -> StoryItem:
    iid = try_uuid(item_id)
    item = (
        db.query(StoryItem)
        .filter(StoryItem.story_id == row.id, StoryItem.id == iid)
        .one_or_none()
        if iid
        else None
    )
    if item is None:
        raise DomainError(404, "Story item not found")
    return item


def update_item(
    *, story_id, item_id, payload: ItemPayload, framework, user, db, context
):
    row = _editable_story(db, user, story_id)
    _version(row, payload.expected_version)
    item = _item_or_404(db, row, item_id)
    if payload.label is not None:
        item.label = _text(payload.label, max_len=256, label="label")
    if payload.note is not None:
        item.note = _text(payload.note, max_len=4000, label="note")
    if payload.clear_occurred_at:
        item.occurred_at = None
    elif payload.occurred_at is not None:
        item.occurred_at = _naive(payload.occurred_at)
    if item.kind == "note" and not item.label and not item.note:
        raise DomainError(400, "A note needs a label or text")
    row.version += 1
    row.updated_at = _utcnow()
    _note(
        db,
        row,
        "item_updated",
        {"item_id": str(item.id), "kind": item.kind},
        user,
        context,
    )
    db.commit()
    return output(db, user, row, _clean_framework(framework))


def remove_item(*, story_id, item_id, expected_version, framework, user, db, context):
    row = _editable_story(db, user, story_id)
    _version(row, expected_version)
    item = _item_or_404(db, row, item_id)
    db.query(StoryLink).filter(
        StoryLink.story_id == row.id,
        or_(StoryLink.from_item_id == item.id, StoryLink.to_item_id == item.id),
    ).delete(synchronize_session=False)
    kind = item.kind
    db.delete(item)
    row.version += 1
    row.updated_at = _utcnow()
    _note(db, row, "item_removed", {"item_id": item_id, "kind": kind}, user, context)
    db.commit()
    return output(db, user, row, _clean_framework(framework))


def save_layout(*, story_id, payload: LayoutPayload, framework, user, db, context):
    """Replace box positions and links; every id must belong to this story."""
    row = _editable_story(db, user, story_id)
    _version(row, payload.expected_version)
    items = {
        item.id: item
        for item in db.query(StoryItem).filter(StoryItem.story_id == row.id).all()
    }
    for position in payload.positions:
        item = items.get(position.item_id)
        if item is None:
            raise DomainError(400, "Position refers to an item outside this story")
        item.x, item.y = position.x, position.y
    seen = set()
    for link in payload.links:
        if link.from_item_id == link.to_item_id:
            raise DomainError(400, "A link joins two different items")
        if link.from_item_id not in items or link.to_item_id not in items:
            raise DomainError(400, "Link refers to an item outside this story")
        key = (link.from_item_id, link.to_item_id)
        if key in seen:
            raise DomainError(400, "Links between the same items are listed once")
        seen.add(key)
    db.query(StoryLink).filter(StoryLink.story_id == row.id).delete(
        synchronize_session=False
    )
    for link in payload.links:
        db.add(
            StoryLink(
                story_id=row.id,
                from_item_id=link.from_item_id,
                to_item_id=link.to_item_id,
                label=_text(link.label, max_len=128, label="link label"),
            )
        )
    row.version += 1
    row.updated_at = _utcnow()
    _note(
        db,
        row,
        "layout_saved",
        {"positions": len(payload.positions), "links": len(payload.links)},
        user,
        context,
    )
    db.commit()
    return output(db, user, row, _clean_framework(framework))


# --- Comments -----------------------------------------------------------------


def _comment_out(comment: StoryComment, names: dict, user: User, db) -> dict:
    return {
        "id": str(comment.id),
        "body": comment.body,
        "created_by_user_id": str(comment.created_by_user_id)
        if comment.created_by_user_id
        else None,
        "created_by": names.get(comment.created_by_user_id),
        "created_at": comment.created_at.isoformat(),
        "can_delete": comment.created_by_user_id == user.id
        or can_admin_stories(db, user),
    }


def list_comments(*, story_id, user, db):
    require_read(db, user)
    row = _story_or_404(db, story_id)
    comments = (
        db.query(StoryComment)
        .filter(StoryComment.story_id == row.id)
        .order_by(StoryComment.created_at, StoryComment.id)
        .limit(MAX_COMMENTS)
        .all()
    )
    names = dict(
        db.query(User.id, User.username)
        .filter(
            User.id.in_(
                {c.created_by_user_id for c in comments if c.created_by_user_id}
            )
        )
        .all()
        if comments
        else []
    )
    return {"items": [_comment_out(c, names, user, db) for c in comments]}


def add_comment(*, story_id, payload: CommentPayload, user, db, context):
    """Readers comment; the story's version is untouched by discussion."""
    require_read(db, user)
    row = _story_or_404(db, story_id, lock=True)
    count = (
        db.query(func.count(StoryComment.id))
        .filter(StoryComment.story_id == row.id)
        .scalar()
    )
    if int(count or 0) >= MAX_COMMENTS:
        raise DomainError(400, f"A story holds up to {MAX_COMMENTS} comments")
    comment = StoryComment(
        story_id=row.id,
        body=_text(payload.body, max_len=5000, label="comment", required=True),
        created_by_user_id=user.id,
        created_at=_utcnow(),
    )
    db.add(comment)
    db.flush()
    _note(db, row, "comment_added", {"comment_id": str(comment.id)}, user, context)
    db.commit()
    return _comment_out(comment, {user.id: user.username}, user, db)


def delete_comment(*, story_id, comment_id, user, db, context):
    require_read(db, user)
    row = _story_or_404(db, story_id, lock=True)
    cid = try_uuid(comment_id)
    comment = (
        db.query(StoryComment)
        .filter(StoryComment.story_id == row.id, StoryComment.id == cid)
        .one_or_none()
        if cid
        else None
    )
    if comment is None:
        raise DomainError(404, "Comment not found")
    if comment.created_by_user_id != user.id and not can_admin_stories(db, user):
        raise DomainError(403, "You can delete your own comments")
    db.delete(comment)
    _note(db, row, "comment_removed", {"comment_id": str(comment.id)}, user, context)
    db.commit()
    return {"ok": True}


# --- Other features -----------------------------------------------------------


def readable_story(db: Session, user: User, story_id) -> Story | None:
    """The story when the caller may read it; sampling and questions use this."""
    if not can_read_stories(db, user):
        return None
    sid = try_uuid(story_id) if not isinstance(story_id, uuid.UUID) else story_id
    return db.get(Story, sid) if sid else None
