"""tools/keen_perf_report.py: summarising a keen-perf-trace.sh capture."""

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "keen_perf_report", ROOT / "tools" / "keen_perf_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


report_tool = _load_tool()

EVENTS = "req=aaaaaaaaaaaa GET /v1/events page=/events.html"
CONTROL = "req=cccccccccccc GET /v1/controls/{id} page=/control.html"
TASK = "task=bbbbbbbbbbbb keen.refresh"


def log_line(second, pid, text, level="LOG"):
    lines = text.split("\n")
    first = f"2026-10-31 10:00:{second:06.3f} UTC [{pid}] {level}:  {lines[0]}"
    return "\n".join([first] + ["\t" + line for line in lines[1:]])


def capture(tmp_path, statements=True, labels=True):
    def label(text):
        return f"/* keen {text} */ " if labels else ""

    lines = [
        # Before the window: left out.
        log_line(1, 9, "duration: 5.000 ms  statement: SELECT 'early'"),
        log_line(10.1, 10, "duration: 0.050 ms  statement: BEGIN"),
    ]
    for n in range(12):
        lines.append(
            log_line(
                10.2 + n / 100,
                10,
                f"duration: 0.500 ms  statement: {label(EVENTS)}SELECT events.connection_id "
                f"FROM events WHERE events.id = '{n:08d}-0000-0000-0000-000000000000'::uuid",
            )
        )
    lines += [
        log_line(
            11.0,
            10,
            "duration: 812.400 ms  plan:\n"
            f"Query Text: {label(EVENTS)}SELECT count(*)\n  FROM events\n WHERE source = 'gh'\n"
            "Aggregate  (cost=10.00..10.01 rows=1 width=8) (actual time=812.1..812.1 rows=1 loops=1)\n"
            "  ->  Seq Scan on events  (cost=0.00..9.00 rows=400 width=0) (actual time=0.1..800.0 rows=250000 loops=1)\n"
            "        Filter: ((source)::text = 'gh'::text)",
        ),
        log_line(
            11.0,
            10,
            f"duration: 812.900 ms  statement: {label(EVENTS)}SELECT count(*)\n  FROM events\n WHERE source = 'gh'",
        ),
        log_line(11.1, 10, 'temporary file: path "base/pgsql_tmp/a.0", size 2097152'),
        log_line(11.1, 10, f"{label(EVENTS)}SELECT count(*) FROM events", "STATEMENT"),
        log_line(11.2, 10, 'temporary file: path "base/pgsql_tmp/a.1", size 1048576'),
        log_line(11.2, 10, f"{label(EVENTS)}SELECT count(*) FROM events", "STATEMENT"),
        log_line(11.3, 10, "duration: 1.250 ms  statement: COMMIT"),
        # The connection check before the next request on this connection.
        log_line(12.0, 11, "duration: 0.010 ms  statement: SELECT 1"),
        log_line(
            12.1,
            11,
            f"duration: 3.000 ms  statement: {label(CONTROL)}SELECT * FROM control_items WHERE id = 'x'",
        ),
        log_line(
            13.0,
            11,
            "process 11 still waiting for ShareLock on transaction 77 after 1000.072 ms",
        ),
        log_line(13.0, 11, "Process holding the lock: 12. Wait queue: 11.", "DETAIL"),
        log_line(
            13.0, 11, f"{label(CONTROL)}UPDATE control_items SET x = 1", "STATEMENT"
        ),
        log_line(
            14.0,
            12,
            f"duration: 2.000 ms  statement: {label(TASK)}DELETE FROM sessions",
        ),
        log_line(14.5, 13, "duration: 0.100 ms  statement: LISTEN keen_events"),
        log_line(
            14.6,
            14,
            "duration: 4.000 ms  statement: /* keen-perf-trace */ SELECT relname FROM pg_stat_user_tables",
        ),
        log_line(
            15.0, 15, 'relation "missing" does not exist at character 15', "ERROR"
        ),
        log_line(15.0, 15, "SELECT * FROM missing", "STATEMENT"),
        log_line(16.0, 15, "checkpoint starting: time"),
    ]
    (tmp_path / "postgres.log").write_text(
        "\n".join(lines if statements else []) + "\n"
    )
    records = [
        {
            "kind": "request",
            "id": "aaaaaaaaaaaa",
            "method": "GET",
            "route": "/v1/events",
            "page": "/events.html",
            "status": 200,
            "ms": 1200.0,
            "end": "2026-10-31T10:00:11.400+00:00",
        },
        {
            "kind": "request",
            "id": "cccccccccccc",
            "method": "GET",
            "route": "/v1/controls/{id}",
            "page": "/control.html",
            "status": 200,
            "ms": 2000.0,
            "end": "2026-10-31T10:00:13.900+00:00",
        },
        {
            "kind": "request",
            "id": "dddddddddddd",
            "method": "GET",
            "route": "/health",
            "page": "-",
            "status": 200,
            "ms": 1.0,
            "end": "2026-10-31T10:00:12.000+00:00",
        },
        {
            "kind": "request",
            "id": "eeeeeeeeeeee",
            "method": "GET",
            "route": "/v1/old",
            "page": "/old.html",
            "status": 200,
            "ms": 1.0,
            "end": "2026-10-31T09:00:00.000+00:00",
        },
    ]
    lines = [json.dumps(r) for r in records]
    lines.append(
        "[2026-10-31 10:00:14,100: WARNING/ForkPoolWorker-1] KEEN_SQL_TRACE "
        + json.dumps(
            {
                "kind": "task",
                "id": "bbbbbbbbbbbb",
                "name": "keen.refresh",
                "ms": 30.0,
                "state": "SUCCESS",
                "end": "2026-10-31T10:00:14.100+00:00",
            }
        )
    )
    lines.append("not json")
    (tmp_path / "trace.jsonl").write_text("\n".join(lines) + "\n")
    (tmp_path / "notes.tsv").write_text(
        "2026-10-31T10:00:09.000Z\topen the events page\nbroken line\n"
    )
    (tmp_path / "capture.tsv").write_text(
        "start\t2026-10-31T10:00:08.000Z\nend\t2026-10-31T10:00:20.000Z\nslow_ms\t50\n"
    )
    (tmp_path / "tables-before.csv").write_text(
        "events,1,100,5,50,250000\nusers,0,0,1,1,3\n"
    )
    (tmp_path / "tables-after.csv").write_text(
        "events,3,500100,9,90,250000\nusers,0,0,1,1,3\n"
    )
    return tmp_path


def render(path, **kwargs):
    out = io.StringIO()
    report_tool.render(path, out, **kwargs)
    return out.getvalue()


def section(text, title):
    rule = "=" * 78
    body = text.split(f"\n{rule}\n{title}\n{rule}\n", 1)[1]
    return body.split(f"\n{rule}\n", 1)[0]


def test_the_report_ties_each_statement_to_its_page_and_call(tmp_path):
    text = render(capture(tmp_path))
    assert "API requests:      2" in text
    assert "Worker tasks:      1" in text
    # The tool's own query and the statement before the window are left out.
    assert "SQL statements:    19 (18 labelled)" in text
    assert "keen-perf-trace" not in text and "'early'" not in text

    visits = section(text, "Page visits")
    assert visits.index("NOTE: open the events page") < visits.index("/events.html")
    # BEGIN and COMMIT count with the call that ran between them.
    assert "! GET /v1/events" in visits
    assert "GET /v1/events" in visits and "  15 sql" in visits
    assert "/control.html  (1 call," in visits
    assert "/health" not in text and "/v1/old" not in text

    repeated = section(text, "Repeated statements within one call")
    assert "12x" in repeated and "GET /v1/events  page /events.html" in repeated
    assert (
        "SELECT events.connection_id FROM events WHERE events.id = ?::uuid" in repeated
    )

    statements = section(text, "Statements by total time")
    assert statements.startswith("Grouped")
    assert "#1  1 call, 813 ms total" in statements
    assert "from: GET /v1/events (1)" in statements
    assert "SELECT count(*) FROM events WHERE source = ?" in statements
    assert "Seq Scan on events" in statements and "plan (812 ms):" in statements

    slowest = section(text, "Slowest API calls")
    assert slowest.index("GET /v1/controls/{id}") < slowest.index("GET /v1/events")
    assert "1997 ms outside PostgreSQL" in slowest

    assert "keen.refresh" in section(text, "Worker tasks")
    unlabelled = section(text, "Unlabelled statements")
    assert "LISTEN keen_events" in unlabelled and "SELECT ?" not in unlabelled
    waits = section(text, "Lock waits")
    assert "1000 ms so far  GET /v1/controls/{id}" in waits
    assert "Process holding the lock: 12" in waits
    assert "UPDATE control_items SET x = ?" in waits
    assert "2 files       3.0 MiB  GET /v1/events" in section(text, "Temporary files")
    errors = section(text, "Errors and warnings")
    assert '1x ERROR: relation "missing" does not exist' in errors
    reads = section(text, "Table reads during the trace")
    assert "events" in reads and "500000" in reads and "users" not in reads


def test_a_capture_without_labels_or_statements_says_so(tmp_path):
    (tmp_path / "unlabelled").mkdir()
    text = render(capture(tmp_path / "unlabelled", labels=False))
    assert "WARNING: no statement carries a Keen label" in text
    (tmp_path / "empty").mkdir()
    empty = capture(tmp_path / "empty", statements=False)
    for name in (
        "trace.jsonl",
        "notes.tsv",
        "capture.tsv",
        "tables-before.csv",
        "tables-after.csv",
    ):
        (empty / name).unlink()
    text = render(empty)
    assert "WARNING: the PostgreSQL log holds no statements" in text
    assert "No table statistics captured." in text
    assert "None found." in text


@pytest.mark.parametrize(
    "sql,expected",
    [
        (
            "SELECT a FROM t WHERE id IN (1, 2, 3) AND name = 'it''s' AND t1.c = 5",
            "SELECT a FROM t WHERE id IN (?, ...) AND name = ? AND t1.c = ?",
        ),
        (
            "INSERT INTO t (a, b) VALUES ('a', 1), ('b', NULL), (E'c', 3) RETURNING t.id",
            "INSERT INTO t (a, b) VALUES (?, ...), ... RETURNING t.id",
        ),
        (
            "SELECT count(*), coalesce(a, 0)\n  FROM t LIMIT 10 OFFSET 20",
            "SELECT count(*), coalesce(a, ?) FROM t LIMIT ? OFFSET ?",
        ),
        (
            "SELECT x FROM t WHERE x = -1.5e3 AND y = $1",
            "SELECT x FROM t WHERE x = ? AND y = $1",
        ),
    ],
)
def test_values_are_replaced_so_statements_group_together(sql, expected):
    assert report_tool.normalise(sql) == expected


def test_the_command_line_needs_a_capture(tmp_path, capsys):
    with pytest.raises(SystemExit) as stopped:
        report_tool.main([str(tmp_path)])
    assert stopped.value.code == 2
    capture(tmp_path)
    assert (
        report_tool.main(
            [str(tmp_path), "--top", "3", "--repeat", "20", "--visits", "1"]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "Keen performance trace" in out
    assert "... 1 more visit; raise --visits to list them." in out
    assert "None found." in section(out, "Repeated statements within one call")


def test_cursor_reads_and_hostile_lines_stay_with_the_right_statement(tmp_path):
    lines = [
        log_line(
            10.0,
            20,
            'duration: 1.000 ms  statement: DECLARE "c_7f3a2b_1" CURSOR WITHOUT HOLD FOR '
            f"/* keen {EVENTS} */ SELECT id FROM events",
        ),
        log_line(
            10.1,
            20,
            'duration: 4.000 ms  statement: FETCH FORWARD 1000 FROM "c_7f3a2b_1"',
        ),
        log_line(10.2, 20, 'duration: 0.100 ms  statement: CLOSE "c_7f3a2b_1"'),
        # A line with an impossible time continues the entry before it.
        "2026-10-31 10:00:60.000 UTC [20] LOG:  duration: 9999.000 ms  statement: SELECT 2",
        # A carriage return inside a logged value cannot start a forged entry.
        log_line(
            10.3,
            21,
            f"duration: 2.000 ms  statement: /* keen {CONTROL} */ SELECT 'note\r"
            "2026-10-31 10:00:10.400 UTC [99] LOG:  duration: 100000.000 ms  statement: SELECT 1'",
        ),
    ]
    (tmp_path / "postgres.log").write_text("\n".join(lines) + "\n", newline="")
    (tmp_path / "trace.jsonl").write_text(
        '{"id": "aaaaaaaaaaaa", "ms": "not a number"}\n{"id": 5}\n[1, 2]\n'
        '{"id": "cccccccccccc", "end": "yesterday"}\n'
    )
    text = render(tmp_path)
    assert "SQL statements:    4 (4 labelled), 7.1 ms in PostgreSQL" in text
    statements = section(text, "Statements by total time")
    assert 'FETCH FORWARD ? FROM "c_?"' in statements
    assert "DECLARE CURSOR FOR SELECT id FROM events" in statements
    assert "from: GET /v1/events (1)" in statements
    assert "100000" not in text and "9999" not in text
