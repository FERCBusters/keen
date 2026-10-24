"""A fictional security awareness quiz with three months of results.

The six fictional people sat a monthly quiz on phishing, lost devices,
passwords and access approval. Each closed round's average is recorded on the
quiz's effectiveness measure, and so on the awareness controls it is linked
to. The fictional people have no Keen accounts, so the next round waits for
someone who can sign in: assign yourself on the quiz page to sit it.
"""

import random
import uuid
from datetime import datetime, time, timedelta

from app.db.models import (
    IsmsEffectivenessMeasure,
    IsmsPerson,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizAudience,
    QuizOption,
    QuizParticipant,
    QuizQuestion,
    QuizRound,
)
from app.services.quizzes.measure import DESCRIPTION, record_round_result
from app.services.quizzes.scoring import build_layout, mark, settle
from app.services.schedules import next_occurrence

PEOPLE = (
    "Alex Example",
    "Sam Example",
    "Riley Example",
    "Jordan Example",
    "Casey Example",
    "Morgan Example",
)
CONTROLS = ("A.6.3", "A.5.10", "A.5.17", "A.6.8", "A.8.7")
PASS_MARK = 70
QUESTIONS = (
    (
        "You receive an unexpected invoice by email with a link to view the document. "
        "What should you do first?",
        "Report it as suspected phishing without opening the link",
        (
            "Open the link to check whether it is genuine",
            "Forward it to colleagues to ask whether they expected it",
            "Reply and ask the sender to confirm",
        ),
        "Reporting first lets the security team warn others and block the sender.",
    ),
    (
        "How soon should you report a lost or stolen work laptop?",
        "Straight away, as soon as you notice",
        (
            "Within a week",
            "Only if it held confidential files",
            "At the next team meeting",
        ),
        "An early report lets the device be locked and wiped before anyone uses it.",
    ),
    (
        "Which password practice does the access control policy ask for?",
        "A unique passphrase kept in the approved password manager",
        (
            "One strong password reused across work systems",
            "A password on a note kept in your desk drawer",
            "Your username followed by the year",
        ),
        "Unique passphrases stop one leaked password opening other systems.",
    ),
    (
        "Who may approve your access to a production system?",
        "The system's asset owner",
        (
            "Any administrator",
            "You, if the need is urgent",
            "A colleague in the same role",
        ),
        "Asset owners approve access, so every grant has an accountable reviewer.",
    ),
)
WRITTEN = (
    "Describe two signs that an email may be a phishing attempt.",
    "Look for urgency or threats, a sender address that does not match, "
    "unexpected attachments or links, and requests for passwords or payment.",
)
REPLIES = (
    "It was urgent and the sender's address did not match the company name.",
    "Unexpected attachment, and it asked me to sign in on a strange page.",
    "Spelling mistakes and a threat to close my account.",
    "The link went somewhere other than the text said, and it wanted my password.",
)
# Rounds in the last three months: who sat each one (by position in PEOPLE).
ROUNDS = ((3, (0, 1, 2, 3, 4)), (2, (0, 1, 2, 3, 4, 5)), (1, (0, 1, 2, 3, 5)))


def _questions(quiz):
    rows = []
    for position, (prompt, right, wrong, explanation) in enumerate(QUESTIONS):
        question = QuizQuestion(
            quiz=quiz,
            position=position,
            kind="choice",
            prompt=prompt,
            explanation=explanation,
        )
        # The right answer sits at a different place in each question.
        texts = list(wrong)
        texts.insert(position % (len(wrong) + 1), right)
        question.options = [
            QuizOption(position=n, text=text, is_correct=text == right)
            for n, text in enumerate(texts)
        ]
        rows.append(question)
    rows.append(
        QuizQuestion(
            quiz=quiz,
            position=len(QUESTIONS),
            kind="text",
            prompt=WRITTEN[0],
            explanation=WRITTEN[1],
        )
    )
    return rows


def _sit(seeder, quiz, round_, person, index, months_back):
    """One person's attempt, with answers that improve over the months."""
    rng = random.Random(f"{seeder.prefix}-{months_back}-{index}")
    layout = build_layout(quiz.questions, count=None, seen=set(), rng=rng)
    submitted = datetime.combine(
        round_.opens_on + timedelta(days=index % 6), time(9 + index, 15)
    )
    attempt = QuizAttempt(
        quiz_id=quiz.id,
        round_id=round_.id,
        person_id=person.id,
        status="in_progress",
        layout=layout,
        page_size=quiz.page_size,
        pages_saved=2,
        started_at=submitted - timedelta(minutes=12),
        saved_at=submitted,
        submitted_at=submitted,
    )
    answers = []
    for position, item in enumerate(layout):
        answer = QuizAnswer(
            position=position,
            question_id=uuid.UUID(item["question_id"]),
            kind=item["kind"],
        )
        if item["kind"] == "choice":
            # Most answers are right, and fewer are wrong as the months pass.
            right = (index + position + months_back) % (2 + months_back) != 0
            wanted = [o for o in item["options"] if o["correct"] is right]
            answer.option_id = uuid.UUID(rng.choice(wanted)["id"])
        else:
            answer.text_answer = REPLIES[(index + months_back) % len(REPLIES)]
        answers.append(answer)
    attempt.answers = answers
    mark(attempt)
    for answer in answers:
        if answer.kind == "text":
            answer.grade = 3 + ((index + 3 - months_back) % 5) * 0.5
            answer.points = answer.grade / 5
            answer.feedback = "Good examples."
            answer.graded_by_user_id = seeder.user.id
            answer.graded_at = submitted + timedelta(days=1)
    settle(attempt, submitted + timedelta(days=1))
    return attempt


def seed_quizzes(seeder):
    """Seed the quiz once; a later run leaves it, and its results, as they are."""
    db = seeder.db
    title = seeder.label("Security awareness refresher")
    if db.query(Quiz.id).filter_by(title=title).first():
        seeder.stats["quizzes.unchanged"] += 1
        return
    people = [
        db.query(IsmsPerson).filter_by(name=seeder.label(name)).one() for name in PEOPLE
    ]
    measure = IsmsEffectivenessMeasure(
        framework_slug=seeder.framework,
        summary=f"Quiz: {title}",
        description=DESCRIPTION,
        source_type="other",
        target_value=float(PASS_MARK),
        target_unit="%",
        threshold_operator="gte",
        owner_user_id=seeder.user.id,
        frequency="Monthly",
        created_by_user_id=seeder.user.id,
    )
    db.add(measure)
    db.flush()
    seeder.link_isms_controls("effectiveness_measure", measure.id, CONTROLS)
    first_round = seeder.month_period(ROUNDS[0][0])[0]
    quiz = Quiz(
        title=title,
        description=(
            "A monthly check on spotting phishing, reporting lost devices, "
            "passwords and access approval."
        ),
        framework_slug=seeder.framework,
        status="active",
        pass_mark=PASS_MARK,
        page_size=3,
        show_scores=True,
        reveal_answers="after_close",
        due_days=14,
        start_date=first_round,
        schedule_recurrence="monthly",
        schedule_interval=1,
        schedule_date_rule="exact",
        measure_id=measure.id,
        created_by_user_id=seeder.user.id,
    )
    db.add(quiz)
    quiz.questions = _questions(quiz)
    quiz.audience = [QuizAudience(person_id=person.id) for person in people]
    db.flush()
    seeder.stats["quizzes.created"] += 1
    for months_back, sat in ROUNDS:
        opens = seeder.month_period(months_back)[0]
        round_ = QuizRound(
            quiz_id=quiz.id,
            opens_on=opens,
            due_on=opens + timedelta(days=quiz.due_days),
            status="closed",
            scheduled=True,
            created_at=datetime.combine(opens, time(0, 20)),
            closed_at=datetime.combine(
                opens + timedelta(days=quiz.due_days + 1), time(0, 20)
            ),
        )
        db.add(round_)
        db.flush()
        for person in people:
            db.add(
                QuizParticipant(
                    round_id=round_.id,
                    person_id=person.id,
                    invited_at=round_.created_at,
                )
            )
        for index in sat:
            db.add(_sit(seeder, quiz, round_, people[index], index, months_back))
        db.flush()
        record_round_result(db, quiz, round_)
        quiz.schedule_last_run_date = opens
        seeder.stats["quiz_rounds.created"] += 1
    quiz.schedule_next_run_date = next_occurrence(quiz, quiz.schedule_last_run_date)
    db.flush()
