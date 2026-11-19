"""A worker task's soft time limit, which no exception handler can swallow.

The worker ends a task at its soft time limit by raising SoftTimeLimitExceeded
once, wherever the task happens to be. It is an ordinary Exception, so a
handler that describes one input's or one row's failure and carries on would
swallow it, and the task would run on past its budget until the hard limit
killed its process, losing what it had in hand, or would record the limit as
a failure of its own.

The worker records the task's deadline here as each task starts, a moment
before the soft limit itself, and every SQL statement the application engine
runs checks it, as do the outbound transports and mail delivery before each
request. Once the deadline has passed, each check raises the time limit
again: a run whose handler swallowed it stops at its next statement, request
or email, and a handler that would write the limit down as a failure cannot.
Work that must still be written once the limit has struck, such as releasing
a claimed row, runs inside ``grace()``. Outside a worker task there is no
deadline and a check does nothing.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded

# The task's time limit is not one input's failure: it ends the whole run.
TIME_LIMITS = (SoftTimeLimitExceeded, TimeLimitExceeded)

# The deadline falls this long before the worker's own soft limit, so a
# handler that catches the worker's raise finds the deadline already passed.
AHEAD_SECONDS = 1.0

_deadline: ContextVar[float | None] = ContextVar("keen_task_deadline", default=None)
_grace: ContextVar[bool] = ContextVar("keen_task_deadline_grace", default=False)


def start(seconds: float | None) -> None:
    """Begin a task that the worker ends after ``seconds``, or never."""
    _deadline.set(
        time.monotonic() + max(seconds - AHEAD_SECONDS, 0.0) if seconds else None
    )


def finish() -> None:
    _deadline.set(None)


def passed() -> bool:
    deadline = _deadline.get()
    return deadline is not None and time.monotonic() >= deadline


def check() -> None:
    """Raise the task's time limit again once its deadline has passed."""
    if passed() and not _grace.get():
        raise SoftTimeLimitExceeded()


@contextmanager
def grace() -> Iterator[None]:
    """Let the statements that tidy up after the time limit run past it.

    The hard limit still ends the task, a minute after the soft one.
    """
    token = _grace.set(True)
    try:
        yield
    finally:
        _grace.reset(token)
