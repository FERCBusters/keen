"""Named quiz assignments and history, restricted to quiz managers."""

from sqlalchemy import and_, or_, select

from app.db.models import (
    IsmsOrgNodeUser,
    IsmsPerson,
    Quiz,
    QuizAttempt,
    QuizAudience,
    QuizParticipant,
    QuizRound,
)
from app.services.errors import DomainError
from app.services.features import state as feature_state
from app.services.isms.access import _can_read_isms
from app.services.quizzes.access import require_manage
from app.services.quizzes.scoring import passed

PAGE_SIZE = 50


def person_quizzes(*, db, user, person_id, offset=0, assignments_offset=0):
    # quiz.read exposes cohort results, not named individual results. Do not
    # weaken that boundary by embedding them in a People page.
    if not _can_read_isms(db, user):
        raise DomainError(403, "ISMS read access required")
    require_manage(db, user)
    feature_state(db).require("people", "quizzes")
    if any(n < 0 or n > 100000 for n in (offset, assignments_offset)):
        raise DomainError(400, "Invalid offset")
    person = db.get(IsmsPerson, person_id)
    if person is None:
        raise DomainError(404, "Person not found")
    eligible = bool(person.user and person.user.is_active)
    roles = select(IsmsOrgNodeUser.org_node_id).where(
        IsmsOrgNodeUser.user_id == person.user_id
    )
    audience = [QuizAudience.person_id == person.id]
    if person.org_node_id:
        audience.append(QuizAudience.org_node_id == person.org_node_id)
    if eligible:
        audience.append(QuizAudience.org_node_id.in_(roles))
    assigned_ids = select(QuizAudience.quiz_id).where(or_(*audience))
    assigned = db.query(Quiz).filter(Quiz.id.in_(assigned_ids))
    assigned_total = assigned.count()
    assignments = (
        assigned.order_by(Quiz.title, Quiz.id)
        .offset(assignments_offset)
        .limit(PAGE_SIZE)
        .all()
    )

    rounds = (
        select(QuizParticipant.round_id)
        .where(QuizParticipant.person_id == person.id)
        .union(select(QuizAttempt.round_id).where(QuizAttempt.person_id == person.id))
    )
    history = (
        db.query(QuizRound, Quiz, QuizAttempt)
        .join(Quiz, Quiz.id == QuizRound.quiz_id)
        .outerjoin(
            QuizAttempt,
            and_(
                QuizAttempt.round_id == QuizRound.id, QuizAttempt.person_id == person.id
            ),
        )
        .filter(QuizRound.id.in_(rounds))
    )
    total = history.count()
    rows = (
        history.order_by(QuizRound.opens_on.desc(), QuizRound.id)
        .offset(offset)
        .limit(PAGE_SIZE)
        .all()
    )
    items = []
    for round_, quiz, attempt in rows:
        score = attempt.score if attempt and attempt.status == "complete" else None
        items.append(
            {
                "quiz_id": str(quiz.id),
                "title": quiz.title,
                "round_id": str(round_.id),
                "opens_on": round_.opens_on.isoformat(),
                "due_on": round_.due_on.isoformat(),
                "round_status": round_.status,
                "status": attempt.status if attempt else "not_started",
                "completed_at": attempt.completed_at.isoformat() + "Z"
                if attempt and attempt.completed_at
                else None,
                "score": score,
                "passed": passed(score, quiz.pass_mark),
            }
        )
    return {
        "eligible": eligible,
        "assignments": [{"id": str(q.id), "title": q.title} for q in assignments],
        "assignments_total": assigned_total,
        "assignments_offset": assignments_offset,
        "assignments_next_offset": assignments_offset + PAGE_SIZE
        if assignments_offset + PAGE_SIZE < assigned_total
        else None,
        "items": items,
        "total": total,
        "offset": offset,
        "next_offset": offset + PAGE_SIZE if offset + PAGE_SIZE < total else None,
    }
