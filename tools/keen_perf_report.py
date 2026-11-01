#!/usr/bin/env python3
"""Summarise a tools/keen-perf-trace.sh capture: which page ran which queries.

Usage:
  keen_perf_report.py CAPTURE_DIR [--top N] [--repeat N] [--slow-ms N] [--visits N]

CAPTURE_DIR holds the files keen-perf-trace.sh collects:

  postgres.log        PostgreSQL's log for the trace window: every statement
                      with its duration, and auto_explain plans for slow ones.
  trace.jsonl         One record per API request or worker task, written by
                      Keen with KEEN_SQL_TRACE on (route, page, status, time).
  notes.tsv           Optional notes typed during the trace (time, text).
  tables-before.csv,  pg_stat_user_tables counters at the start and end.
  tables-after.csv
  capture.tsv         The trace window (start and end, UTC).

With KEEN_SQL_TRACE on, each statement starts with a comment naming its
request or task, so statements are grouped exactly. BEGIN, COMMIT and the
connection check that SQLAlchemy sends around them carry no comment; they are
given to the neighbouring labelled statement on the same connection.

The report goes to standard output. Statement text is shown with literal
values replaced by ``?``. Plans keep their values, as PostgreSQL logs them.
Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENTRY = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?) (\S+) \[(\d+)\] ([A-Z]+):  (.*)$"
)
DURATION = re.compile(
    r"^duration: ([\d.]+) ms  (?:statement|execute [^:]*): (.*)$", re.S
)
PLAN = re.compile(r"^duration: ([\d.]+) ms  plan:\n(.*)$", re.S)
LOCK_WAIT = re.compile(r"still waiting for (.+?) after ([\d.]+) ms")
TEMP_FILE = re.compile(r"^temporary file: .*size (\d+)")
LABEL = re.compile(r"^\s*/\* keen (req|task)=([0-9a-f]+) (.*?) \*/ ?", re.S)
# SQLAlchemy's yield_per runs a query through a named server-side cursor.
CURSOR = re.compile(
    r'^\s*DECLARE\s+"?([\w$]+)"?\s+(?:[A-Z ]+\s+)?CURSOR\b.*?\bFOR\s+', re.I | re.S
)
CURSOR_NAME = re.compile(r'"c_\w+"')
STRING = re.compile(r"(?<![\w'])[EeXxBb]?'(?:[^']|'')*'")
NUMBER = re.compile(r"(?<![\w$.?])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w.])")
VALUE = r"\s*(?:\?|NULL|DEFAULT|true|false)(?:::[\w\[\]]+)?\s*"
TUPLE = rf"\((?:{VALUE},)*{VALUE}\)"
ROWS = re.compile(rf"({TUPLE})(?:\s*,\s*{TUPLE})+", re.I)
LIST = re.compile(rf"\((?:{VALUE},)+{VALUE}\)", re.I)
PLAN_NODE = re.compile(r"^\S.*  \(cost=[\d.]+\.\.")
BEFORE_WORK = re.compile(r"^(BEGIN|SAVEPOINT\b|SELECT \?$)", re.I)
AFTER_WORK = re.compile(r"^(COMMIT|ROLLBACK|RELEASE SAVEPOINT\b|FETCH\b|CLOSE\b)", re.I)
NEIGHBOUR = timedelta(seconds=5)
UNLABELLED = "(unlabelled)"
# The trace script's own queries, and the health check it polls.
TOOL_QUERY = "/* keen-perf-trace */"
IGNORED_ROUTES = {"GET /health"}


def parse_time(value: str) -> datetime:
    value = value.strip().replace("Z", "+00:00")
    if " UTC" in value:
        value = value.replace(" UTC", "+00:00")
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def normalise(sql: str) -> str:
    """Statement text with literal values replaced, for grouping."""
    text = CURSOR_NAME.sub('"c_?"', sql)
    text = STRING.sub("?", text)
    text = NUMBER.sub("?", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = ROWS.sub(r"\1, ...", text)
    return LIST.sub("(?, ...)", text)


def short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass
class Statement:
    ts: datetime
    pid: str
    ms: float
    sql: str
    norm: str
    kind: str | None = None
    ident: str | None = None
    label: str = ""
    inherited: bool = False


@dataclass
class Plan:
    ts: datetime
    ms: float
    ident: str | None
    norm: str
    lines: list[str]


@dataclass
class Unit:
    """An API request or worker task, from trace.jsonl or its statements."""

    kind: str
    ident: str
    label: str
    page: str = "-"
    status: str = ""
    ms: float | None = None
    end: datetime | None = None
    statements: list[Statement] = field(default_factory=list)

    @property
    def db_ms(self) -> float:
        return sum(s.ms for s in self.statements)

    @property
    def start(self) -> datetime | None:
        if self.end is not None and self.ms is not None:
            return self.end - timedelta(milliseconds=self.ms)
        return self.statements[0].ts if self.statements else None


def read_log(path: Path):
    """Log entries, each with the tab-indented lines that continue it.

    Lines end at newlines only: a carriage return inside a logged value stays
    part of that value.
    """
    entries: list[dict] = []
    lines: list[str] = []

    def finish():
        if entries:
            entries[-1]["message"] = "\n".join(lines)

    with path.open(encoding="utf-8", errors="replace", newline="\n") as log:
        for raw in log:
            line = raw.removesuffix("\n")
            match = ENTRY.match(line)
            stamp = None
            if match:
                ts, zone, pid, level, message = match.groups()
                try:
                    stamp = parse_time(f"{ts} {zone}" if zone == "UTC" else ts)
                except ValueError:
                    stamp = None
            if stamp is not None:
                finish()
                entries.append(
                    {"ts": stamp, "pid": pid, "level": level, "message": message}
                )
                lines = [message]
            elif entries:
                lines.append(line[1:] if line.startswith("\t") else line)
    finish()
    return entries


def split_label(sql: str):
    """The label match (or None) and the statement text without the label."""
    cursor = CURSOR.match(sql)
    body = sql[cursor.end() :] if cursor else sql
    label = LABEL.match(body)
    if label:
        body = body[label.end() :]
    return label, ("DECLARE CURSOR FOR " + body if cursor else body)


def parse_log(path: Path):
    statements, plans, events = [], [], []
    last_event = None
    for entry in read_log(path):
        message, level = entry["message"], entry["level"]
        if level in ("STATEMENT", "DETAIL", "CONTEXT", "HINT"):
            # Follow-up lines belong to the entry just before them.
            if last_event is not None and last_event["pid"] == entry["pid"]:
                last_event.setdefault(level.lower(), message)
            continue
        last_event = None
        if level in ("ERROR", "FATAL", "PANIC", "WARNING"):
            last_event = {"kind": level.lower(), **entry}
        elif level != "LOG":
            continue
        elif match := PLAN.match(message):
            lines = match[2].split("\n")
            start = next(
                (i for i, line in enumerate(lines) if i and PLAN_NODE.match(line)),
                len(lines),
            )
            query = "\n".join(lines[:start]).removeprefix("Query Text: ")
            label, sql = split_label(query)
            plans.append(
                Plan(
                    entry["ts"],
                    float(match[1]),
                    label[2] if label else None,
                    normalise(sql),
                    lines[start:],
                )
            )
        elif match := DURATION.match(message):
            label, sql = split_label(match[2])
            statements.append(
                Statement(
                    entry["ts"],
                    entry["pid"],
                    float(match[1]),
                    sql,
                    normalise(sql),
                    label[1] if label else None,
                    label[2] if label else None,
                    label[3] if label else "",
                )
            )
        elif match := LOCK_WAIT.search(message):
            last_event = {"kind": "lock wait", "ms": float(match[2]), **entry}
        elif match := TEMP_FILE.match(message):
            last_event = {"kind": "temp file", "bytes": int(match[1]), **entry}
        if last_event is not None:
            events.append(last_event)
    return statements, plans, events


def attribute_neighbours(statements: list[Statement]) -> None:
    """Give unlabelled transaction control to the labelled work beside it.

    BEGIN, savepoints and the connection check go to the next statement on the
    same connection; COMMIT, ROLLBACK and cursor reads to the previous one.
    Other unlabelled transaction control in between is passed over.
    """
    by_pid: dict[str, list[Statement]] = defaultdict(list)
    for statement in statements:
        by_pid[statement.pid].append(statement)

    def control(statement: Statement) -> bool:
        return not statement.ident and bool(
            BEFORE_WORK.match(statement.norm) or AFTER_WORK.match(statement.norm)
        )

    for items in by_pid.values():
        # The nearest statement on each side that is not unlabelled control.
        following: list[Statement | None] = [None] * len(items)
        nearest = None
        for i in range(len(items) - 1, -1, -1):
            following[i] = nearest
            if not control(items[i]):
                nearest = items[i]
        nearest = None
        for i, statement in enumerate(items):
            preceding, nearest = nearest, (nearest if control(statement) else statement)
            if statement.ident:
                continue
            if BEFORE_WORK.match(statement.norm):
                other = following[i]
            elif AFTER_WORK.match(statement.norm):
                other = preceding
            else:
                continue
            if other and other.ident and abs(other.ts - statement.ts) <= NEIGHBOUR:
                statement.kind, statement.ident = other.kind, other.ident
                statement.inherited = True


def read_units(path: Path) -> dict[str, Unit]:
    units: dict[str, Unit] = {}
    if not path.exists():
        return units
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line[line.find("{") :] if "{" in line else ""
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            continue
        kind = record.get("kind", "request")
        label = (
            f"{record.get('method', '?')} {record.get('route', '?')}"
            if kind == "request"
            else str(record.get("name", "?"))
        )
        try:
            units[record["id"]] = Unit(
                kind=kind,
                ident=record["id"],
                label=label,
                page=str(record.get("page", "-")),
                status=str(record.get("status", "")),
                ms=float(record["ms"]) if "ms" in record else None,
                end=parse_time(record["end"]) if record.get("end") else None,
            )
        except (TypeError, ValueError):
            continue  # a line from elsewhere in the logs that only looks like one
    return units


def build_units(statements: list[Statement], units: dict[str, Unit]) -> None:
    for statement in statements:
        if not statement.ident:
            continue
        unit = units.get(statement.ident)
        if unit is None:
            text, page = statement.label, "-"
            if " page=" in text:
                text, page = text.rsplit(" page=", 1)
            unit = units[statement.ident] = Unit(
                "request" if statement.kind == "req" else "task",
                statement.ident,
                text,
                page,
            )
        unit.statements.append(statement)


def read_window(path: Path) -> tuple[datetime | None, datetime | None]:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("\t")
            try:
                values[key] = parse_time(value)
            except ValueError:
                continue
    return values.get("start"), values.get("end")


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def read_notes(path: Path) -> list[tuple[datetime, str]]:
    notes = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stamp, _, text = line.partition("\t")
            try:
                notes.append((parse_time(stamp), text.strip()))
            except ValueError:
                continue
    return notes


def read_tables(path: Path) -> dict[str, list[int]]:
    tables = {}
    if path.exists():
        for row in csv.reader(path.read_text(encoding="utf-8").splitlines()):
            if len(row) >= 5:
                try:
                    tables[row[0]] = [int(v or 0) for v in row[1:]]
                except ValueError:
                    continue
    return tables


def ms(value: float | None) -> str:
    if value is None:
        return "-"
    return (
        f"{value / 1000:.2f} s"
        if value >= 10000
        else f"{value:.0f} ms"
        if value >= 10
        else f"{value:.1f} ms"
    )


def clock(value: datetime | None) -> str:
    return value.strftime("%H:%M:%S.%f")[:-3] if value else "--:--:--"


class Report:
    def __init__(self, out):
        self.out = out

    def heading(self, title: str, note: str = "") -> None:
        self.out.write(f"\n{'=' * 78}\n{title}\n{'=' * 78}\n")
        if note:
            self.out.write(note.rstrip() + "\n\n")

    def line(self, text: str = "") -> None:
        self.out.write(text + "\n")


def statement_groups(statements: list[Statement]):
    groups: dict[str, list[Statement]] = defaultdict(list)
    for statement in statements:
        groups[statement.norm].append(statement)
    return sorted(groups.items(), key=lambda item: -sum(s.ms for s in item[1]))


def page_visits(requests: list[Unit], gap: float = 10.0):
    visits: list[list[Unit]] = []
    for unit in sorted(
        requests, key=lambda u: u.start or datetime.min.replace(tzinfo=timezone.utc)
    ):
        last = visits[-1] if visits else None
        if (
            last
            and last[0].page == unit.page
            and unit.start
            and last[-1].start
            and (unit.start - last[-1].start).total_seconds() <= gap
        ):
            last.append(unit)
        else:
            visits.append([unit])
    return visits


def render(
    capture: Path,
    out,
    top: int = 20,
    repeat: int = 10,
    slow_ms: float = 500,
    visits: int = 300,
) -> None:
    statements, plans, events = parse_log(capture / "postgres.log")
    start, finish = read_window(capture / "capture.tsv")

    def in_window(moment: datetime | None) -> bool:
        return moment is None or (
            (start is None or moment >= start - NEIGHBOUR)
            and (finish is None or moment <= finish + NEIGHBOUR)
        )

    statements = [
        s for s in statements if in_window(s.ts) and not s.sql.startswith(TOOL_QUERY)
    ]
    plans = [p for p in plans if in_window(p.ts)]
    events = [e for e in events if in_window(e["ts"])]
    attribute_neighbours(statements)
    units = {
        ident: unit
        for ident, unit in read_units(capture / "trace.jsonl").items()
        if in_window(unit.end) and unit.label not in IGNORED_ROUTES
    }
    build_units(statements, units)
    notes = read_notes(capture / "notes.tsv")
    requests = [u for u in units.values() if u.kind == "request"]
    tasks = [u for u in units.values() if u.kind == "task"]
    unlabelled = [s for s in statements if not s.ident]
    labelled = len(statements) - len(unlabelled)
    report = Report(out)

    times = [s.ts for s in statements] + [u.end for u in units.values() if u.end]
    report.heading("Keen performance trace")
    if times:
        report.line(
            f"Window:            {min(times):%Y-%m-%d %H:%M:%S} to {max(times):%H:%M:%S} UTC"
        )
    report.line(f"API requests:      {len(requests)}")
    report.line(f"Worker tasks:      {len(tasks)}")
    report.line(
        f"SQL statements:    {len(statements)} ({labelled} labelled), "
        f"{ms(sum(s.ms for s in statements))} in PostgreSQL"
    )
    report.line(f"Plans captured:    {len(plans)}")
    if statements and not labelled:
        report.line(
            "\nWARNING: no statement carries a Keen label. Is the API image built with "
            "KEEN_SQL_TRACE support, and was it running during the trace?"
        )
    if not statements:
        report.line(
            "\nWARNING: the PostgreSQL log holds no statements for this window."
        )

    report.heading(
        "Page visits",
        "Each visit groups the API calls one page made within a few seconds, in order.\n"
        f"Calls taking {ms(slow_ms)} or more are marked '!'. Notes typed during the trace "
        "appear in place.",
    )
    pending = sorted(notes)
    all_visits = page_visits(requests)
    for visit in all_visits[:visits]:
        first = visit[0]
        while pending and first.start and pending[0][0] <= first.start:
            stamp, text = pending.pop(0)
            report.line(f"{clock(stamp)}  NOTE: {text}")
        ends = [u.end or u.start for u in visit if u.end or u.start]
        span = max(ends) - first.start if first.start and ends else None
        report.line(
            f"{clock(first.start)}  {first.page}  ({plural(len(visit), 'call')}, "
            f"{ms(span.total_seconds() * 1000) if span else '-'} span, "
            f"{plural(sum(len(u.statements) for u in visit), 'statement')}, "
            f"{ms(sum(u.db_ms for u in visit))} in PostgreSQL)"
        )
        for unit in visit:
            mark = "!" if (unit.ms or 0) >= slow_ms else " "
            report.line(
                f"   {mark} {unit.label:<58} {unit.status:>3} {ms(unit.ms):>9} "
                f"db {ms(unit.db_ms):>9} {len(unit.statements):>5} sql"
            )
    if len(all_visits) > visits:
        report.line(
            f"... {plural(len(all_visits) - visits, 'more visit')}; "
            "raise --visits to list them."
        )
    for stamp, text in pending:
        report.line(f"{clock(stamp)}  NOTE: {text}")

    by_route: dict[str, list[Unit]] = defaultdict(list)
    for unit in requests:
        by_route[unit.label].append(unit)
    report.heading(
        "API calls by route", "Sorted by total time. db = time inside PostgreSQL."
    )
    report.line(
        f"{'route':<58} {'calls':>5} {'avg':>9} {'max':>9} {'db avg':>9} {'sql avg':>7} {'sql max':>7}"
    )
    for label, group in sorted(
        by_route.items(), key=lambda item: -sum(u.ms or u.db_ms for u in item[1])
    ):
        walls = [u.ms for u in group if u.ms is not None]
        counts = [len(u.statements) for u in group]
        report.line(
            f"{short(label, 58):<58} {len(group):>5} "
            f"{ms(statistics.mean(walls)) if walls else '-':>9} {ms(max(walls)) if walls else '-':>9} "
            f"{ms(statistics.mean(u.db_ms for u in group)):>9} "
            f"{statistics.mean(counts):>7.1f} {max(counts):>7}"
        )

    report.heading(
        "Slowest API calls",
        "Outside PostgreSQL = the call's time less its statements' time: Python work, "
        "waiting for a connection, or other services.",
    )
    for unit in sorted(requests, key=lambda u: -(u.ms or u.db_ms))[:top]:
        outside = (unit.ms - unit.db_ms) if unit.ms is not None else None
        status = f"  [{unit.status}]" if unit.status else ""
        report.line(f"{clock(unit.start)}  {unit.label}{status}  page {unit.page}")
        report.line(
            f"    {ms(unit.ms)} total, {ms(unit.db_ms)} in PostgreSQL over "
            f"{len(unit.statements)} statements, {ms(outside)} outside PostgreSQL"
        )
        for norm, group in statement_groups(unit.statements)[:6]:
            report.line(
                f"    {len(group):>4}x {ms(sum(s.ms for s in group)):>9}  {short(norm, 110)}"
            )
        report.line()

    report.heading(
        "Repeated statements within one call",
        f"The same statement run {repeat} or more times by one call usually means a "
        "query per row (N+1) that one query could replace.",
    )
    found = False
    for unit in units.values():
        for norm, group in statement_groups(unit.statements):
            if len(group) >= repeat and not (
                BEFORE_WORK.match(norm) or AFTER_WORK.match(norm)
            ):
                found = True
                report.line(
                    f"{len(group):>5}x {ms(sum(s.ms for s in group)):>9}  {unit.label}  page {unit.page}"
                )
                report.line(f"        {short(norm, 300)}")
    if not found:
        report.line("None found.")

    report.heading(
        "Statements by total time",
        "Grouped by text with values removed. 'from' lists the routes or tasks that ran\n"
        "each one. The slowest captured plan follows each statement that has one.",
    )
    best_plan: dict[str, Plan] = {}
    for plan in plans:
        if plan.norm not in best_plan or plan.ms > best_plan[plan.norm].ms:
            best_plan[plan.norm] = plan
    for rank, (norm, group) in enumerate(statement_groups(statements)[:top], 1):
        durations = [s.ms for s in group]
        sources = Counter(
            (units[s.ident].label if s.ident in units else UNLABELLED) for s in group
        )
        report.line(
            f"#{rank}  {plural(len(group), 'call')}, {ms(sum(durations))} total, "
            f"{ms(statistics.mean(durations))} avg, {ms(max(durations))} max"
        )
        report.line(
            "    from: "
            + "; ".join(f"{label} ({n})" for label, n in sources.most_common(4))
        )
        report.line("    " + short(norm, 1500))
        plan = best_plan.get(norm)
        if plan:
            report.line(f"    plan ({ms(plan.ms)}):")
            for plan_line in plan.lines[:70]:
                report.line("      " + plan_line)
            if len(plan.lines) > 70:
                report.line(f"      ... {len(plan.lines) - 70} more lines")
        report.line()

    report.heading("Worker tasks", "Background work that ran during the trace.")
    by_task: dict[str, list[Unit]] = defaultdict(list)
    for unit in tasks:
        by_task[unit.label].append(unit)
    if not by_task:
        report.line("None recorded.")
    for label, group in sorted(
        by_task.items(), key=lambda item: -sum(u.db_ms for u in item[1])
    ):
        report.line(
            f"{short(label, 58):<58} {plural(len(group), 'run'):>8}, max {ms(max((u.ms or 0) for u in group)):>9}, "
            f"db {ms(sum(u.db_ms for u in group)):>9}, {sum(len(u.statements) for u in group)} sql"
        )

    report.heading(
        "Unlabelled statements",
        "Statements outside any traced request or task: startup, connection set-up,\n"
        "listeners, or services running without KEEN_SQL_TRACE.",
    )
    if not unlabelled:
        report.line("None.")
    for norm, group in statement_groups(unlabelled)[:top]:
        report.line(
            f"{len(group):>5}x {ms(sum(s.ms for s in group)):>9}  {short(norm, 120)}"
        )

    def source_of(event: dict) -> str:
        label, _ = split_label(event.get("statement") or "")
        if not label:
            return UNLABELLED
        return units[label[2]].label if label[2] in units else label[3]

    def statement_of(event: dict) -> str:
        _, text = split_label(event.get("statement") or "")
        return short(normalise(text), 200) if text else ""

    report.heading(
        "Lock waits",
        "Statements that waited a second or more for another transaction's lock.",
    )
    waits = [e for e in events if e["kind"] == "lock wait"]
    if not waits:
        report.line("None.")
    for event in waits:
        report.line(
            f"{clock(event['ts'])}  {ms(event['ms'])} so far  {source_of(event)}"
        )
        report.line(f"    {short(event['message'].splitlines()[0], 200)}")
        if event.get("detail"):
            report.line(f"    {short(event['detail'].splitlines()[0], 200)}")
        if statement_of(event):
            report.line(f"    {statement_of(event)}")

    report.heading(
        "Temporary files",
        "Sorts, hashes and other work too large for work_mem, written to disk.",
    )
    spills: dict[tuple[str, str], list[int]] = defaultdict(list)
    for event in events:
        if event["kind"] == "temp file":
            spills[(source_of(event), statement_of(event))].append(event["bytes"])
    if not spills:
        report.line("None.")
    for (source, statement), sizes in sorted(spills.items(), key=lambda i: -sum(i[1])):
        report.line(
            f"{plural(len(sizes), 'file'):>9} {sum(sizes) / 1048576:>9.1f} MiB  {source}"
        )
        if statement:
            report.line(f"    {statement}")

    report.heading("Errors and warnings")
    problems: dict[tuple[str, str, str], int] = Counter()
    for event in events:
        if event["kind"] not in ("lock wait", "temp file"):
            first = short(event["message"].splitlines()[0], 200)
            problems[(event["kind"], first, source_of(event))] += 1
    if not problems:
        report.line("None.")
    for (kind, message, source), count in problems.most_common(top):
        report.line(f"{count:>5}x {kind.upper()}: {message}  ({source})")

    before, after = (
        read_tables(capture / "tables-before.csv"),
        read_tables(capture / "tables-after.csv"),
    )
    report.heading(
        "Table reads during the trace",
        "Rows read by sequential scans are rows PostgreSQL read in full; large numbers on\n"
        "big tables point at a missing index or a query that cannot use one.",
    )
    rows = []
    for table, now in after.items():
        then = before.get(table, [0] * len(now))
        delta = [a - b for a, b in zip(now, then)]
        if any(delta[:4]):
            rows.append((table, delta, now))
    if not rows:
        report.line("No table statistics captured.")
    else:
        report.line(
            f"{'table':<40} {'seq scans':>10} {'seq rows':>14} {'idx scans':>10} {'idx rows':>14} {'live rows':>12}"
        )
        for table, delta, now in sorted(rows, key=lambda r: -r[1][1])[:top]:
            live = now[4] if len(now) > 4 else 0
            report.line(
                f"{short(table, 40):<40} {delta[0]:>10} {delta[1]:>14} {delta[2]:>10} {delta[3]:>14} {live:>12}"
            )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("capture", type=Path)
    parser.add_argument(
        "--top", type=int, default=20, help="rows per section (default 20)"
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=10,
        help="repeats that flag a statement (default 10)",
    )
    parser.add_argument(
        "--slow-ms",
        type=float,
        default=500,
        help="mark calls at least this slow (default 500)",
    )
    parser.add_argument(
        "--visits", type=int, default=300, help="page visits to list (default 300)"
    )
    args = parser.parse_args(argv)
    if not (args.capture / "postgres.log").exists():
        parser.error(f"{args.capture / 'postgres.log'} not found")
    render(args.capture, sys.stdout, args.top, args.repeat, args.slow_ms, args.visits)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
