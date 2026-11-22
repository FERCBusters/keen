"""Where a worker task's time limit falls, and what the task does then.

Every SQL statement, outbound request and email a task asks for checks the
task's deadline first (myf.core.task_deadline.check). These helpers make the
deadline pass at a chosen one of those checks, so a test can place the time
limit at each point a task reaches in turn and see how the task ends.
"""

from contextlib import contextmanager

from celery.exceptions import SoftTimeLimitExceeded
from myf.core import task_deadline
from myf.core.task_deadline import TIME_LIMITS
from sqlalchemy import event as sa_event


@contextmanager
def deadline_at(position: int):
    """The deadline passes at the ``position``-th check (counting from 1).

    Checks made in the cleanup grace are not counted and are never refused.
    Yields the list of refused checks: a task that stops at the limit, as it
    should, is refused exactly once.
    """
    asked: list[int] = []
    refused: list[int] = []
    check = task_deadline.check

    def checking():
        if task_deadline._grace.get():
            return
        asked.append(len(asked) + 1)
        if len(asked) >= position:
            refused.append(len(asked))
            raise SoftTimeLimitExceeded()

    task_deadline.check = checking
    try:
        yield refused
    finally:
        task_deadline.check = check


def ending(run, position: int) -> str:
    """How ``run()`` ends with the deadline passing at check ``position``.

    "limit": the refused check raised the time limit out of it, with nothing
    asked for after. "finished": it ended before reaching that check.
    "swallowed": a check was refused and it ended as if nothing had happened.
    "continued": it caught a refusal and asked for more work before stopping.
    """
    with deadline_at(position) as refused:
        try:
            run()
        except TIME_LIMITS:
            return "limit" if len(refused) == 1 else "continued"
    return "swallowed" if refused else "finished"


def stops_wherever_the_deadline_falls(run, prepare, after_limit, most=400) -> int:
    """Place the deadline at each check ``run`` reaches, in turn.

    ``prepare()`` sets up the same work before each attempt; ``after_limit()``
    asserts what a stop at the limit must leave behind. Returns the number of
    checks a run that is never stopped makes, so a test can assert that the
    sweep reached the work it meant to.
    """
    for position in range(1, most):
        prepare()
        result = ending(run, position)
        if result == "finished":
            return position - 1
        assert result == "limit", f"deadline at check {position}: {result}"
        after_limit()
    raise AssertionError(f"still running after {most} checks")


@contextmanager
def checked_statements(engine):
    """``engine`` checks the deadline before each statement, as the worker's does."""
    from app.db import session

    sa_event.listen(engine, "before_cursor_execute", session._within_task_deadline)
    try:
        yield engine
    finally:
        sa_event.remove(engine, "before_cursor_execute", session._within_task_deadline)
