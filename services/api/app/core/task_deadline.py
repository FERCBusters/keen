"""A worker task's soft time limit, which no exception handler can swallow.

The worker ends a task at its soft time limit by raising SoftTimeLimitExceeded
once, wherever the task happens to be. It is an ordinary Exception, so a
handler that describes one input's or one row's failure and carries on would
swallow it, and the task would run on past its budget until the hard limit
killed its process, losing what it had in hand. The worker records the same
deadline here as each task starts, and the work loops check it: the outbound
transports before each request, the shared event store before each record,
the record reader before each record, the connection fan-out before each
connection and mail delivery before each row. Once the deadline has passed,
each check raises the time limit again, so a run whose handler swallowed it
still stops at its next request, record or delivery. Outside a worker task
there is no deadline and a check does nothing.
"""

from __future__ import annotations

import time
from contextvars import ContextVar

from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded

# The task's time limit is not one input's failure: it ends the whole run.
TIME_LIMITS = (SoftTimeLimitExceeded, TimeLimitExceeded)

_deadline: ContextVar[float | None] = ContextVar("keen_task_deadline", default=None)


def start(seconds: float | None) -> None:
    """Begin a task that the worker ends after ``seconds``, or never."""
    _deadline.set(time.monotonic() + seconds if seconds else None)


def finish() -> None:
    _deadline.set(None)


def check() -> None:
    """Raise the task's time limit again once its deadline has passed."""
    deadline = _deadline.get()
    if deadline is not None and time.monotonic() >= deadline:
        raise SoftTimeLimitExceeded()
