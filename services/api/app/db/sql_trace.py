"""Label each SQL statement with the API request or worker task that ran it.

With KEEN_SQL_TRACE on, every statement starts with a comment such as
``/* keen req=3f2a9c1e0b7d GET /v1/controls/{id}/evidence page=/control.html */``
and each request or task writes one ``KEEN_SQL_TRACE`` JSON line to standard
error when it ends. tools/keen-perf-trace.sh reads these alongside the
PostgreSQL statement log to show which page caused which queries.

Labels hold the method, the request path with numeric and UUID segments
replaced by ``{id}``, the path of the page that sent the request and the task
name, without query strings. They are reduced to a fixed set of characters so
request headers cannot end the comment. The HTTP middleware lives in
``app.api.sql_trace`` and the task hooks in ``app.worker.sql_trace``.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from contextvars import ContextVar, Token
from datetime import datetime, timezone

from sqlalchemy import event

_label: ContextVar[str | None] = ContextVar("keen_sql_label", default=None)
_UNSAFE = re.compile(r"[^A-Za-z0-9/_.{}:-]")
_TRACE_LINE = "KEEN_SQL_TRACE "


def clean(value: str, limit: int) -> str:
    """Reduce a label part to characters that cannot end an SQL comment."""
    return _UNSAFE.sub("", value)[:limit] or "-"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def begin(kind: str, ident: str, description: str) -> Token:
    """Label the statements run in this context until ``end`` is called."""
    return _label.set(f"{kind}={ident} {description}")


def end(token: Token, record: dict) -> None:
    """Stop labelling and write the request or task record to standard error."""
    _label.reset(token)
    record["end"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    sys.stderr.write(_TRACE_LINE + json.dumps(record, sort_keys=True) + "\n")
    sys.stderr.flush()


def _label_statement(conn, cursor, statement, parameters, context, executemany):
    label = _label.get()
    if label:
        statement = f"/* keen {label} */ {statement}"
    return statement, parameters


def install_statement_labels(engine) -> None:
    event.listen(engine, "before_cursor_execute", _label_statement, retval=True)
