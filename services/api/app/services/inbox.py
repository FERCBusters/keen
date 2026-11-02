"""Each person's inbox: records that concern them, changed by someone else.

Keen tells a person when an audit they attend starts, when they are asked to
sit a quiz round, when minutes appear for a meeting they attended, and when a
measure they own falls outside its threshold. The person who made the change
is not told, and nobody is told about the same record twice. An item names its record only: the title and link are read when
the inbox is shown, and an item is shown only while its record exists, its
feature is on and its reader can open it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.core.references import try_uuid
from app.db.models import (
    Audit,
    AuditAttendee,
    InboxItem,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    IsmsMeeting,
    IsmsMeetingAttendee,
    IsmsMeetingPerson,
    IsmsPerson,
    Quiz,
    QuizParticipant,
    QuizRound,
    User,
)
from app.services.audits.access import can_read_audits
from app.services.audits.validation import MUTABLE_AUDIT_STATUSES
from app.services.errors import DomainError
from app.services.features import state
from app.services.isms.access import can_read_isms, meeting_ids_for
from app.services.isms.metric_values import threshold_status
from app.services.mailer import smtp_configured, user_email
from app.services.schedules import today_for_schedule
from app.services.security_notifications import queue_email

AUDIT_STARTED = "audit_started"
QUIZ_ROUND = "quiz_round"
MEETING_MINUTES = "meeting_minutes"
MEASURE_OUTSIDE = "measure_outside"
# The inbox lists this many of a person's latest items it can show.
LIST_LIMIT = 100
# Read items are removed after this long, and unread ones after a year.
KEEP_READ_DAYS = 90
KEEP_UNREAD_DAYS = 365


def notify(
    db: Session,
    *,
    kind: str,
    subject_id: uuid.UUID,
    user_ids: Iterable[uuid.UUID | None],
    actor: User | None,
) -> int:
    """Tell each active account about a record, except the one who acted.

    Someone already told about the record is not told again, so a caller can
    pass everyone concerned. Each account's notification setting decides
    whether it is also emailed; an emailed notification is listed as read
    for someone who chose email only. Without mail set up, or an address to
    send to, the notification stays in Keen. Returns how many were told.
    """
    wanted = {uid for uid in user_ids if uid is not None}
    if actor is not None:
        wanted.discard(actor.id)
    if not wanted:
        return 0
    accounts = (
        db.query(User)
        .filter(User.id.in_(wanted), User.is_active.is_(True))
        .order_by(User.id)
        .all()
    )
    mail = smtp_configured()
    if mail:
        # An email shows the record as its reader can open it, so the caller's
        # pending changes, such as new attendees, must be visible first.
        db.flush()
    told = 0
    now = utc_now_naive()
    for account in accounts:
        delivery = account.pref_notification_delivery
        address = user_email(account) if mail and delivery != "app" else None
        item = {
            "id": uuid.uuid4(),
            "user_id": account.id,
            "kind": kind,
            "subject_id": subject_id,
            "actor_user_id": actor.id if actor is not None else None,
            "created_at": now,
            "read_at": now if address and delivery == "email" else None,
        }
        created = db.execute(
            insert(InboxItem)
            .values(**item)
            .on_conflict_do_nothing(index_elements=["user_id", "kind", "subject_id"])
            .returning(InboxItem.id)
        ).scalar_one_or_none()
        if created is None:
            continue
        told += 1
        if address:
            _email(db, account, address, InboxItem(**item))
    return told


def _email(db: Session, account: User, address: str, item: InboxItem) -> None:
    """Queue a notification's email, if its reader can open the record."""
    shown = _shown(db, account, [item])
    if not shown:
        return
    view = shown[0]
    line = f"{view['summary']}: {view['title']}"
    when = f"{view['date_label']} {view['date']} · " if view["date"] else ""
    by = f"by {view['actor']}" if view["actor"] else "by Keen"
    queue_email(
        db,
        user_id=account.id,
        recipient=address,
        subject=f"Keen: {line}",
        body=(
            f"{line}\n{when}{by}\n\n"
            f"Open it in Keen: {settings.public_base_url.rstrip('/')}{view['href']}\n\n"
            "You receive these emails because your Keen notification setting "
            "includes email. Change it under Account, Preferences.\n"
        ),
    )


def _person_accounts(db: Session, person_ids: Iterable[uuid.UUID | None]) -> set:
    ids = {pid for pid in person_ids if pid is not None}
    if not ids:
        return set()
    return {
        uid
        for (uid,) in db.query(IsmsPerson.user_id).filter(
            IsmsPerson.id.in_(ids), IsmsPerson.user_id.isnot(None)
        )
    }


def audit_started(
    db: Session, audit: Audit, attendees: Iterable[AuditAttendee], actor: User | None
) -> int:
    """Tell attendees, by their account or their People record's account,
    that an audit under way includes them."""
    if audit.status not in MUTABLE_AUDIT_STATUSES:
        return 0
    rows = list(attendees)
    accounts = {row.user_id for row in rows} | _person_accounts(
        db, (row.person_id for row in rows)
    )
    return notify(
        db, kind=AUDIT_STARTED, subject_id=audit.id, user_ids=accounts, actor=actor
    )


def quiz_participants_added(
    db: Session,
    round_: QuizRound,
    person_ids: Iterable[uuid.UUID],
    actor: User | None,
) -> int:
    """Tell the accounts of people who joined a round that they can sit it."""
    return notify(
        db,
        kind=QUIZ_ROUND,
        subject_id=round_.id,
        user_ids=_person_accounts(db, person_ids),
        actor=actor,
    )


def meeting_attendees(db: Session, meeting_id: uuid.UUID) -> set:
    """Accounts that attended a meeting, directly or through a People record."""
    direct = {
        uid
        for (uid,) in db.query(IsmsMeetingAttendee.user_id).filter(
            IsmsMeetingAttendee.meeting_id == meeting_id,
            IsmsMeetingAttendee.attendance_type == "attendee",
        )
    }
    people = [
        pid
        for (pid,) in db.query(IsmsMeetingPerson.person_id).filter(
            IsmsMeetingPerson.meeting_id == meeting_id,
            IsmsMeetingPerson.attendance_type == "attendee",
        )
    ]
    return direct | _person_accounts(db, people)


def meeting_minutes(
    db: Session,
    meeting: IsmsMeeting,
    attendees: Iterable[uuid.UUID],
    actor: User | None,
) -> int:
    """Tell attendees that a meeting's minutes are there to read."""
    return notify(
        db,
        kind=MEETING_MINUTES,
        subject_id=meeting.id,
        user_ids=attendees,
        actor=actor,
    )


def measure_result(
    db: Session, measure: IsmsEffectivenessMeasure, actor: User | None
) -> int:
    """Tell a measure's owner when its latest result falls outside the
    threshold after one that did not.

    Call after recording or revising a result. Each result that crosses the
    threshold is its own notification, so a measure that recovers and falls
    outside again is reported again.
    """
    if measure.owner_user_id is None:
        return 0
    latest = (
        db.query(IsmsEffectivenessMetricEntry)
        .filter(IsmsEffectivenessMetricEntry.measure_id == measure.id)
        .order_by(
            IsmsEffectivenessMetricEntry.recorded_at.desc(),
            IsmsEffectivenessMetricEntry.created_at.desc(),
            IsmsEffectivenessMetricEntry.id.desc(),
        )
        .limit(2)
        .all()
    )

    def outside(entry: IsmsEffectivenessMetricEntry) -> bool:
        status = threshold_status(
            entry.metric_value,
            measure.target_value,
            measure.threshold_operator,
            entry.metric_unit or "",
            measure.target_unit or "",
        )
        return status == "outside"

    if not latest or not outside(latest[0]) or len(latest) > 1 and outside(latest[1]):
        return 0
    return notify(
        db,
        kind=MEASURE_OUTSIDE,
        subject_id=latest[0].id,
        user_ids=[measure.owner_user_id],
        actor=actor,
    )


# --- Reading -------------------------------------------------------------------


def _audit_views(db: Session, user: User, ids: set, features) -> dict:
    if not ids or not features.enabled("audits"):
        return {}
    rows = db.query(Audit).filter(Audit.id.in_(ids), Audit.status != "template")
    readable = can_read_audits(db, user)
    attended = {
        aid
        for (aid,) in db.query(AuditAttendee.audit_id).filter(
            AuditAttendee.audit_id.in_(ids), AuditAttendee.user_id == user.id
        )
    }
    return {
        row.id: {
            "summary": "New audit",
            "title": row.title,
            "href": f"/audit.html?id={row.id}",
            "date_label": "Starts",
            "date": row.start_date.isoformat() if row.start_date else None,
        }
        for row in rows
        if readable or row.id in attended
    }


def _quiz_views(db: Session, user: User, ids: set, features) -> dict:
    if not ids or not features.enabled("quizzes"):
        return {}
    today = today_for_schedule()
    mine = select(IsmsPerson.id).where(IsmsPerson.user_id == user.id)
    rows = (
        db.query(QuizRound, Quiz)
        .join(Quiz, Quiz.id == QuizRound.quiz_id)
        .join(QuizParticipant, QuizParticipant.round_id == QuizRound.id)
        .filter(
            QuizRound.id.in_(ids),
            QuizRound.status == "open",
            QuizRound.opens_on <= today,
            QuizRound.due_on >= today,
            Quiz.status == "active",
            QuizParticipant.person_id.in_(mine),
        )
    )
    return {
        round_.id: {
            "summary": "Quiz to take",
            "title": quiz.title,
            "href": f"/quiz-take.html?round={round_.id}",
            "date_label": "Due",
            "date": round_.due_on.isoformat(),
        }
        for round_, quiz in rows
    }


def _meeting_views(db: Session, user: User, ids: set, features) -> dict:
    if not ids or not features.enabled("meetings"):
        return {}
    query = db.query(IsmsMeeting).filter(IsmsMeeting.id.in_(ids))
    if not can_read_isms(db, user):
        query = query.filter(IsmsMeeting.id.in_(meeting_ids_for(user)))
    return {
        row.id: {
            "summary": "Minutes added",
            "title": row.title,
            "href": f"/isms-meeting.html?id={row.id}",
            "date_label": "Meeting on",
            "date": row.date.isoformat() if row.date else None,
        }
        for row in query
    }


def _measure_views(db: Session, user: User, ids: set, features) -> dict:
    if not ids or not features.enabled("effectiveness") or not can_read_isms(db, user):
        return {}
    rows = (
        db.query(IsmsEffectivenessMetricEntry, IsmsEffectivenessMeasure)
        .join(
            IsmsEffectivenessMeasure,
            IsmsEffectivenessMeasure.id == IsmsEffectivenessMetricEntry.measure_id,
        )
        .filter(IsmsEffectivenessMetricEntry.id.in_(ids))
    )
    return {
        entry.id: {
            "summary": "Measure outside its threshold",
            "title": measure.summary or "Effectiveness measure",
            "href": f"/isms-effectiveness-measure.html?id={measure.id}",
            "date_label": "Recorded",
            "date": entry.recorded_at.date().isoformat(),
        }
        for entry, measure in rows
    }


VIEWS = {
    AUDIT_STARTED: _audit_views,
    QUIZ_ROUND: _quiz_views,
    MEETING_MINUTES: _meeting_views,
    MEASURE_OUTSIDE: _measure_views,
}


def _shown(db: Session, user: User, items: list[InboxItem]) -> list[dict[str, Any]]:
    """The items a person can open, with their records' current titles."""
    if not items:
        return []
    features = state(db)
    views: dict = {}
    for kind, view in VIEWS.items():
        ids = {item.subject_id for item in items if item.kind == kind}
        views[kind] = view(db, user, ids, features)
    actors = {
        uid: name
        for uid, name in db.query(User.id, User.username).filter(
            User.id.in_({item.actor_user_id for item in items if item.actor_user_id})
        )
    }
    shown = []
    for item in items:
        view = views[item.kind].get(item.subject_id)
        if view is None:
            continue
        shown.append(
            {
                "id": str(item.id),
                "kind": item.kind,
                **view,
                "actor": actors.get(item.actor_user_id),
                "created_at": item.created_at.isoformat(),
                "read_at": item.read_at.isoformat() if item.read_at else None,
            }
        )
    return shown


def _latest(db: Session, user: User, *, unread: bool, limit: int) -> list[InboxItem]:
    query = db.query(InboxItem).filter(InboxItem.user_id == user.id)
    if unread:
        query = query.filter(InboxItem.read_at.is_(None))
    return query.order_by(InboxItem.created_at.desc(), InboxItem.id).limit(limit).all()


def unread_count(db: Session, user: User) -> int:
    # Items a person cannot open are not counted; a few spare rows allow for them.
    return len(_shown(db, user, _latest(db, user, unread=True, limit=LIST_LIMIT * 2)))


def inbox_summary(*, db: Session, user: User) -> dict[str, int]:
    return {"unread_count": unread_count(db, user)}


def list_inbox(*, unread: bool = False, db: Session, user: User) -> dict[str, Any]:
    rows = _latest(db, user, unread=unread, limit=LIST_LIMIT * 2)
    items = _shown(db, user, rows)[:LIST_LIMIT]
    return {"items": items, "unread_count": unread_count(db, user)}


def mark_read(*, item_id: str, db: Session, user: User) -> dict[str, int]:
    key = try_uuid(item_id)
    row = (
        db.query(InboxItem)
        .filter(InboxItem.id == key, InboxItem.user_id == user.id)
        .one_or_none()
        if key is not None
        else None
    )
    if row is None:
        raise DomainError(status_code=404, detail="Notification not found")
    if row.read_at is None:
        row.read_at = utc_now_naive()
        db.commit()
    return {"unread_count": unread_count(db, user)}


def mark_all_read(*, db: Session, user: User) -> dict[str, int]:
    marked = (
        db.query(InboxItem)
        .filter(InboxItem.user_id == user.id, InboxItem.read_at.is_(None))
        .update({InboxItem.read_at: utc_now_naive()}, synchronize_session=False)
    )
    db.commit()
    return {"marked": marked, "unread_count": 0}


def remove_old_items(db: Session, *, now=None) -> int:
    """Remove read items after a while, and unread ones after a year."""
    now = now or utc_now_naive()
    removed = (
        db.query(InboxItem)
        .filter(
            or_(
                InboxItem.read_at < now - timedelta(days=KEEP_READ_DAYS),
                InboxItem.created_at < now - timedelta(days=KEEP_UNREAD_DAYS),
            )
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return removed
