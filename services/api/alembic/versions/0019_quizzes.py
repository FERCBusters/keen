"""Quizzes, and reminders for audit attendees.

A quiz holds multiple-choice and free-text questions, the people and
organisation roles it is assigned to, a recurring schedule and the
effectiveness measure its round averages are recorded on. Each round is a
dated sitting; each attempt keeps the questions as shown with the person's
answers and marks.

Two permissions arrive with the tables: quiz.read views quizzes and their
anonymised results, and quiz.manage creates, schedules and grades quizzes and
sees each person's answers and scores. Audit attendees gain the time they were
reminded that an audit starts soon.
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0019_quizzes"
down_revision = "0018_keen_seed_wording"
branch_labels = None
depends_on = None

PERMISSIONS = (
    ("quiz.read", "View quizzes and their anonymised results."),
    (
        "quiz.manage",
        "Create, schedule and grade quizzes, and see each person's answers and scores.",
    ),
)

RECURRENCES = "'once','weekly','monthly','quarterly','yearly'"
DATE_RULES = (
    "'exact','first_weekday_of_month','first_monday_of_month',"
    "'first_tuesday_of_month','first_wednesday_of_month',"
    "'first_thursday_of_month','first_friday_of_month'"
)


def upgrade():
    op.add_column(
        "audit_attendees", sa.Column("reminded_at", sa.DateTime(), nullable=True)
    )
    op.execute(f"""CREATE TABLE quizzes (
        id uuid PRIMARY KEY, title varchar(256) NOT NULL,
        description text NOT NULL DEFAULT '',
        framework_slug varchar(64) NOT NULL,
        status varchar(16) NOT NULL DEFAULT 'draft',
        pass_mark integer, questions_per_attempt integer,
        page_size integer NOT NULL DEFAULT 5,
        show_scores boolean NOT NULL DEFAULT false,
        reveal_answers varchar(16) NOT NULL DEFAULT 'after_close',
        due_days integer NOT NULL DEFAULT 14,
        start_date date,
        schedule_recurrence varchar(32) NOT NULL DEFAULT 'once',
        schedule_interval integer NOT NULL DEFAULT 1,
        schedule_date_rule varchar(32) NOT NULL DEFAULT 'exact',
        schedule_anchor_month integer,
        schedule_next_run_date date,
        schedule_until_date date,
        schedule_last_run_date date,
        measure_id uuid NOT NULL UNIQUE
            REFERENCES isms_effectiveness_measures(id) ON DELETE RESTRICT,
        created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
        created_at timestamp NOT NULL, updated_at timestamp NOT NULL,
        CONSTRAINT ck_quizzes_status CHECK (status IN ('draft','active','archived')),
        CONSTRAINT ck_quizzes_reveal_answers
            CHECK (reveal_answers IN ('never','after_close','after_submit')),
        CONSTRAINT ck_quizzes_pass_mark
            CHECK (pass_mark IS NULL OR (pass_mark >= 0 AND pass_mark <= 100)),
        CONSTRAINT ck_quizzes_questions_per_attempt
            CHECK (questions_per_attempt IS NULL OR questions_per_attempt >= 1),
        CONSTRAINT ck_quizzes_page_size CHECK (page_size >= 1 AND page_size <= 50),
        CONSTRAINT ck_quizzes_due_days CHECK (due_days >= 1 AND due_days <= 365),
        CONSTRAINT ck_quizzes_schedule_recurrence
            CHECK (schedule_recurrence IN ({RECURRENCES})),
        CONSTRAINT ck_quizzes_schedule_date_rule
            CHECK (schedule_date_rule IN ({DATE_RULES})),
        CONSTRAINT ck_quizzes_schedule_anchor_month CHECK (
            schedule_anchor_month IS NULL
            OR (schedule_anchor_month >= 1 AND schedule_anchor_month <= 12)
        ),
        CONSTRAINT ck_quizzes_schedule_interval CHECK (schedule_interval >= 1)
    )""")
    op.create_index("ix_quizzes_framework_slug", "quizzes", ["framework_slug"])
    op.create_index("ix_quizzes_status", "quizzes", ["status"])
    op.create_index(
        "ix_quizzes_schedule_next_run_date", "quizzes", ["schedule_next_run_date"]
    )
    op.execute("""CREATE TABLE quiz_questions (
        id uuid PRIMARY KEY,
        quiz_id uuid NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
        position integer NOT NULL DEFAULT 0,
        kind varchar(16) NOT NULL DEFAULT 'choice',
        prompt text NOT NULL, explanation text NOT NULL DEFAULT '',
        created_at timestamp NOT NULL, updated_at timestamp NOT NULL,
        CONSTRAINT ck_quiz_questions_kind CHECK (kind IN ('choice','text'))
    )""")
    op.create_index("ix_quiz_questions_quiz_id", "quiz_questions", ["quiz_id"])
    op.execute("""CREATE TABLE quiz_options (
        id uuid PRIMARY KEY,
        question_id uuid NOT NULL REFERENCES quiz_questions(id) ON DELETE CASCADE,
        position integer NOT NULL DEFAULT 0, text text NOT NULL,
        is_correct boolean NOT NULL DEFAULT false
    )""")
    op.create_index("ix_quiz_options_question_id", "quiz_options", ["question_id"])
    op.execute("""CREATE TABLE quiz_audience (
        id uuid PRIMARY KEY,
        quiz_id uuid NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
        person_id uuid REFERENCES isms_people(id) ON DELETE CASCADE,
        org_node_id uuid REFERENCES isms_org_nodes(id) ON DELETE CASCADE,
        created_at timestamp NOT NULL,
        CONSTRAINT ck_quiz_audience_member
            CHECK ((person_id IS NULL) <> (org_node_id IS NULL)),
        CONSTRAINT uq_quiz_audience_person UNIQUE (quiz_id, person_id),
        CONSTRAINT uq_quiz_audience_org_node UNIQUE (quiz_id, org_node_id)
    )""")
    op.create_index("ix_quiz_audience_quiz_id", "quiz_audience", ["quiz_id"])
    op.create_index("ix_quiz_audience_person_id", "quiz_audience", ["person_id"])
    op.create_index("ix_quiz_audience_org_node_id", "quiz_audience", ["org_node_id"])
    op.execute("""CREATE TABLE quiz_rounds (
        id uuid PRIMARY KEY,
        quiz_id uuid NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
        opens_on date NOT NULL, due_on date NOT NULL,
        status varchar(16) NOT NULL DEFAULT 'open',
        scheduled boolean NOT NULL DEFAULT false,
        created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
        created_at timestamp NOT NULL, closed_at timestamp,
        CONSTRAINT ck_quiz_rounds_status CHECK (status IN ('open','closed')),
        CONSTRAINT ck_quiz_rounds_dates CHECK (due_on >= opens_on)
    )""")
    op.create_index("ix_quiz_rounds_quiz_id", "quiz_rounds", ["quiz_id"])
    op.create_index("ix_quiz_rounds_status_due_on", "quiz_rounds", ["status", "due_on"])
    op.execute("""CREATE TABLE quiz_participants (
        id uuid PRIMARY KEY,
        round_id uuid NOT NULL REFERENCES quiz_rounds(id) ON DELETE CASCADE,
        person_id uuid NOT NULL REFERENCES isms_people(id) ON DELETE CASCADE,
        created_at timestamp NOT NULL, invited_at timestamp, reminded_at timestamp,
        CONSTRAINT uq_quiz_participant UNIQUE (round_id, person_id)
    )""")
    op.create_index("ix_quiz_participants_round_id", "quiz_participants", ["round_id"])
    op.create_index(
        "ix_quiz_participants_person_id", "quiz_participants", ["person_id"]
    )
    op.execute("""CREATE TABLE quiz_attempts (
        id uuid PRIMARY KEY,
        quiz_id uuid NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
        round_id uuid NOT NULL REFERENCES quiz_rounds(id) ON DELETE CASCADE,
        person_id uuid REFERENCES isms_people(id) ON DELETE SET NULL,
        user_id uuid REFERENCES users(id) ON DELETE SET NULL,
        status varchar(16) NOT NULL DEFAULT 'in_progress',
        layout jsonb NOT NULL DEFAULT '[]'::jsonb,
        page_size integer NOT NULL DEFAULT 5,
        pages_saved integer NOT NULL DEFAULT 0,
        started_at timestamp NOT NULL, saved_at timestamp,
        submitted_at timestamp, completed_at timestamp,
        score double precision,
        CONSTRAINT ck_quiz_attempts_status
            CHECK (status IN ('in_progress','submitted','complete')),
        CONSTRAINT ck_quiz_attempts_score
            CHECK (score IS NULL OR (score >= 0 AND score <= 100)),
        CONSTRAINT uq_quiz_attempt_person UNIQUE (round_id, person_id)
    )""")
    op.create_index("ix_quiz_attempts_quiz_id", "quiz_attempts", ["quiz_id"])
    op.create_index("ix_quiz_attempts_round_id", "quiz_attempts", ["round_id"])
    op.create_index("ix_quiz_attempts_person_id", "quiz_attempts", ["person_id"])
    op.execute("""CREATE TABLE quiz_answers (
        id uuid PRIMARY KEY,
        attempt_id uuid NOT NULL REFERENCES quiz_attempts(id) ON DELETE CASCADE,
        position integer NOT NULL,
        question_id uuid REFERENCES quiz_questions(id) ON DELETE SET NULL,
        kind varchar(16) NOT NULL DEFAULT 'choice',
        option_id uuid, text_answer text NOT NULL DEFAULT '',
        is_correct boolean, grade double precision,
        feedback text NOT NULL DEFAULT '',
        graded_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
        graded_at timestamp, points double precision,
        CONSTRAINT ck_quiz_answers_kind CHECK (kind IN ('choice','text')),
        CONSTRAINT ck_quiz_answers_grade CHECK (
            grade IS NULL OR (grade >= 1 AND grade <= 5 AND grade * 2 = round(grade * 2))
        ),
        CONSTRAINT ck_quiz_answers_points
            CHECK (points IS NULL OR (points >= 0 AND points <= 1)),
        CONSTRAINT uq_quiz_answer_position UNIQUE (attempt_id, position)
    )""")
    op.create_index("ix_quiz_answers_attempt_id", "quiz_answers", ["attempt_id"])
    op.create_index("ix_quiz_answers_question_id", "quiz_answers", ["question_id"])
    for code, description in PERMISSIONS:
        op.execute(
            sa.text("""
            INSERT INTO permissions (id, code, description, created_at)
            SELECT :id, :code, :description, CURRENT_TIMESTAMP
            WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :code)
        """).bindparams(id=str(uuid.uuid4()), code=code, description=description)
        )


def downgrade():
    for code, _ in PERMISSIONS:
        op.execute(
            sa.text("DELETE FROM permissions WHERE code = :code").bindparams(code=code)
        )
    op.drop_table("quiz_answers")
    op.drop_table("quiz_attempts")
    op.drop_table("quiz_participants")
    op.drop_table("quiz_rounds")
    op.drop_table("quiz_audience")
    op.drop_table("quiz_options")
    op.drop_table("quiz_questions")
    op.drop_table("quizzes")
    op.drop_column("audit_attendees", "reminded_at")
