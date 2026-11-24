"""One record never stops the writing of the records after it.

Everything in Keen that collects events, whether a collector, a custom API
integration or a cloud provider, reads each record through
``app.ingest.common.RecordReader``: a record whose shape or values cannot be
read is counted as invalid, and the records after it are still written.
Upstream, storage and time-limit failures still fail the input with its
cursor retained. The receivers of Keen Agent evidence are the exception, and
the only one: an agent keeps its evidence queued until Keen accepts it, so
they store a request whole or refuse it.

``test_every_record_loop_reads_through_the_reader`` finds every loop in the
application that writes events and fails when one does not iterate
``RecordReader.each``. An ingester added later is held to the same rule.
"""

import ast
import json
import pathlib
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx2 as httpx
import pytest
from app.ingest import common
from app.ingest import jenkins as jk
from celery.exceptions import SoftTimeLimitExceeded
from mosp.security import json_shape

from tests.source_tree import SOURCES, name_in

API = pathlib.Path(__file__).resolve().parents[1]
APP = SOURCES
STORE = "store_event_with_artifact"

# Storing loops that do not read through RecordReader, and why each stores a
# request whole instead. Every other loop that writes events, including one
# added later, reads its records through the reader.
CONTAINED_OTHERWISE = {
    ("services/ueu_evidence.py", "persist"): (
        "Keen Agent UEU batches are verified whole and stored whole or refused. "
        "The agent keeps a refused batch queued and reports itself blocked, so "
        "nothing is lost. Skipping an envelope would acknowledge evidence that "
        "is not stored, so the agent would discard it, and would record a chain "
        "gap that blames the agent for Keen's omission. An envelope Keen can "
        "never store is purged on the agent, which sends a discard record that "
        "explains the gap."
    ),
    ("services/agents.py", "persist_batch"): (
        "Keen Agent requests are validated, then stored in one transaction or "
        "refused; the agent keeps a refused request queued and retries it, "
        "where skipping an event would acknowledge evidence that is not stored."
    ),
}


def _name(call: ast.Call) -> str | None:
    return getattr(call.func, "id", None) or getattr(call.func, "attr", None)


def _loops(node):
    return [n for n in ast.walk(node) if isinstance(n, (ast.For, ast.While))]


def _stores_alone(function, storers) -> bool:
    """Whether the function stores a record outside any loop of its own."""
    in_loops = {id(n) for loop in _loops(function) for n in ast.walk(loop)}
    return any(
        isinstance(n, ast.Call) and _name(n) in storers and id(n) not in in_loops
        for n in ast.walk(function)
    )


def _reads_through_reader(loop) -> bool:
    return (
        isinstance(loop, ast.For)
        and isinstance(loop.iter, ast.Call)
        and isinstance(loop.iter.func, ast.Attribute)
        and loop.iter.func.attr == "each"
    )


def record_loops(source: str, filename: str):
    """(file, function, contained) for each function with a loop that stores.

    A loop stores when it calls store_event_with_artifact, or a function of the
    same module that stores one record outside a loop of its own. The innermost
    loop around each such call must iterate ``RecordReader.each``.
    """
    tree = ast.parse(source)
    functions = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    storers = {STORE}
    while True:
        more = {f.name for f in functions if _stores_alone(f, storers)} - storers
        if not more:
            break
        storers |= more
    found = {}
    for function in functions:
        nested = {
            id(n)
            for inner in ast.walk(function)
            if inner is not function
            and isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            for n in ast.walk(inner)
        }
        loops = [loop for loop in _loops(function) if id(loop) not in nested]
        for call in ast.walk(function):
            if not isinstance(call, ast.Call) or _name(call) not in storers:
                continue
            if id(call) in nested:
                continue
            around = [loop for loop in loops if call in ast.walk(loop)]
            if not around:
                continue
            innermost = min(around, key=lambda loop: len(list(ast.walk(loop))))
            key = (filename, function.name)
            found[key] = found.get(key, True) and _reads_through_reader(innermost)
    return {(file, name, contained) for (file, name), contained in found.items()}


def _handed_read(call: ast.AST):
    """The read function a call hands to a RecordReader, directly or through
    ``event_api.read_windows``, or None."""
    if not isinstance(call, ast.Call):
        return None
    if _name(call) == "read_windows":
        return next((kw.value for kw in call.keywords if kw.arg == "read"), None)
    if isinstance(call.func, ast.Attribute) and call.func.attr in {"each", "read"}:
        if call.args:
            return call.args[-1] if call.func.attr == "each" else call.args[0]
    return None


def _read_functions(source: str):
    """(name, names used) for each function handed to a RecordReader."""
    tree = ast.parse(source)
    defined = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for call in ast.walk(tree):
        handed = _handed_read(call)
        if handed is None:
            continue
        if isinstance(handed, ast.Name) and handed.id in defined:
            handed = defined[handed.id]
        elif not isinstance(handed, ast.Lambda):
            continue
        yield (
            getattr(handed, "name", "lambda"),
            {n.id for n in ast.walk(handed) if isinstance(n, ast.Name)},
        )


def _scanned_sources():
    for path in sorted(APP.rglob("*.py")):
        yield name_in(path, APP).as_posix(), path.read_text()


def test_every_record_loop_reads_through_the_reader():
    found = set()
    for filename, source in _scanned_sources():
        found |= record_loops(source, filename)
    uncontained = sorted(
        (file, name)
        for file, name, contained in found
        if not contained and (file, name) not in CONTAINED_OTHERWISE
    )
    assert not uncontained, (
        "These loops store collected records without reading each through "
        "app.ingest.common.RecordReader.each, so one record Keen cannot read "
        f"stops the run: {uncontained}"
    )
    flagged = {(file, name) for file, name, contained in found if not contained}
    assert not set(CONTAINED_OTHERWISE) - flagged, "remove entries no longer found"


def test_every_event_writer_is_scanned():
    # The inventory covers the whole application, and finds the loops of the
    # push receivers as well as the collectors'.
    found = set()
    for filename, source in _scanned_sources():
        found |= {(file, name) for file, name, _ in record_loops(source, filename)}
    assert {
        ("ingest/jenkins.py", "ingest_jenkins_job"),
        ("services/ueu_evidence.py", "persist"),
        ("services/agents.py", "persist_batch"),
        ("integrations/runtime.py", "run_job"),
    } <= found


def test_read_functions_do_no_database_work():
    # A read function runs inside the reader's handler, where a failed
    # database statement would be counted as one unreadable record.
    seen = 0
    for filename, source in _scanned_sources():
        for name, names in _read_functions(source):
            seen += 1
            assert not names & {"db", "session"}, (filename, name)
    assert seen >= 30  # the readers of every collector are inspected


def test_the_read_check_detects_database_work():
    source = (
        "def collect(db, rows):\n"
        "    def read(row):\n"
        "        return db.get(Event, row['id'])\n"
        "    for event in reader.each(rows, read):\n"
        "        store_event_with_artifact(db, **event)\n"
    )
    assert [("read", True)] == [
        (name, "db" in names) for name, names in _read_functions(source)
    ]
    # A read function handed to the shared reader of windowed logs is held to
    # the same rule.
    windowed = (
        "def collect(db, rows):\n"
        "    return read_windows(\n"
        "        db, name='x', request=request,\n"
        "        read=lambda item, collected: db.get(Event, item['id']),\n"
        "    )\n"
    )
    assert [("lambda", True)] == [
        (name, "db" in names) for name, names in _read_functions(windowed)
    ]


ORIGINAL_JENKINS_LOOP = """
def ingest_jenkins_job(db, job_name, builds):
    for b in reversed(builds[:50]):
        ts = utc_from_timestamp_naive(b.get("timestamp") / 1000.0)
        store_event_with_artifact(db, timestamp=ts)
    db.commit()
"""

ORIGINAL_REDMINE_SHAPE = """
def _store(db, issue):
    return store_event_with_artifact(db, timestamp=_timestamp(issue["updated_on"]))

def _project(db, issues):
    for issue in issues:
        _store(db, issue)
"""


def test_the_inventory_detects_the_reported_loops():
    assert record_loops(ORIGINAL_JENKINS_LOOP, "ingest/x.py") == {
        ("ingest/x.py", "ingest_jenkins_job", False)
    }
    # A loop that stores through a helper of its own module is found too.
    assert ("ingest/x.py", "_project", False) in record_loops(
        ORIGINAL_REDMINE_SHAPE, "ingest/x.py"
    )
    contained = ORIGINAL_JENKINS_LOOP.replace(
        "for b in reversed(builds[:50]):", "for b in reader.each(builds, read):"
    )
    assert record_loops(contained, "ingest/x.py") == {
        ("ingest/x.py", "ingest_jenkins_job", True)
    }


# --- Decoding upstream JSON ----------------------------------------------------

# json.loads in the ingesters reads only what is listed here; every response
# from an upstream system is decoded with json_shape.decode_json, so one value
# nested too deeply never leaves the rest of a page unreadable.
DECODED_OTHERWISE = {
    ("ingest/cloud/google.py", "key_info"): "the operator's service account key",
    ("ingest/google_workspace.py", "_load_credentials"): (
        "the operator's service account key"
    ),
    ("ingest/imap.py", "fetch"): "the reply of Keen's own IMAP worker process",
    ("ingest/imap_support/worker.py", "main"): "the request of Keen's own API process",
    ("ingest/log_pipeline.py", "try_parse_json_line"): (
        "one log line; a line that cannot be parsed is kept as text"
    ),
    ("ingest/webhooks.py", "_store_webhook_event"): (
        "one delivered body; a body that cannot be parsed is kept as text"
    ),
    ("integrations/transport.py", "decrypt"): "Keen's own encrypted settings",
}


def json_decodes(source: str, filename: str):
    """(file, function, how) for each JSON decode in a module."""
    found = set()
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(function):
            if not (
                isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            ):
                continue
            if call.func.attr == "json" and not call.args:
                found.add((filename, function.name, "response.json()"))
            elif (
                call.func.attr == "loads"
                and getattr(call.func.value, "id", "") == "json"
            ):
                found.add((filename, function.name, "json.loads"))
    return found


def test_every_ingester_decodes_upstream_json_without_a_depth_limit():
    found = set()
    for filename, source in _scanned_sources():
        if filename.startswith(("ingest/", "integrations/")):
            found |= json_decodes(source, filename)
    undecoded = sorted(
        entry for entry in found if (entry[0], entry[1]) not in DECODED_OTHERWISE
    )
    assert not undecoded, (
        f"Decode upstream JSON with mosp.security.json_shape.decode_json: {undecoded}"
    )
    assert set(DECODED_OTHERWISE) <= {(f, n) for f, n, _ in found}


def test_the_decode_inventory_detects_the_reported_decodes():
    source = (
        "def _window(client):\n"
        "    data = client.post('_search').json()\n"
        "def _json(body):\n"
        "    return json.loads(body)\n"
    )
    assert json_decodes(source, "ingest/x.py") == {
        ("ingest/x.py", "_window", "response.json()"),
        ("ingest/x.py", "_json", "json.loads"),
    }


def test_a_page_with_one_value_nested_too_deeply_is_still_read():
    deep = "[" * 100_000 + "]" * 100_000
    page = '{"hits": [{"_id": "1"}, {"_id": "2", "deep": ' + deep + "}]}"
    with pytest.raises(RecursionError):
        json.loads(page)
    hits = json_shape.decode_json(page.encode())["hits"]
    assert [hit["_id"] for hit in hits] == ["1", "2"]
    # The document, "hits", the hit and "deep" are four levels; the container
    # that would be level MAX_DEPTH + 1 is replaced, as storing replaces it.
    value, depth = hits[1]["deep"], 4
    while isinstance(value, list):
        value, depth = value[0], depth + 1
    assert (value, depth) == (json_shape.NESTED_TOO_DEEPLY, json_shape.MAX_DEPTH + 1)


@pytest.mark.parametrize(
    "text",
    [
        "[" * 100_000,
        '{"a": ' + "[" * 100_000 + '"',
        "]" + "[" * 100_000,
        "[" + "[" * 100_000 + "]" * 100_000 + "\\]",
        "not json",
    ],
)
def test_text_that_is_not_json_still_fails_to_decode(text):
    with pytest.raises(json.JSONDecodeError):
        json_shape.decode_json(text)


def test_strings_and_escapes_are_not_read_as_structure():
    deep = "[" * 100_000 + "]" * 100_000
    text = '{"s": "a\\"[{\\\\", "t": "]]}}", "deep": ' + deep + "}"
    value = json_shape.decode_json(text)
    assert (value["s"], value["t"]) == ('a"[{\\', "]]}}")
    assert json_shape.decode_json(json.dumps({"a": [1]}).encode("utf-16")) == {"a": [1]}


# --- The reader -----------------------------------------------------------


def test_reader_counts_an_unreadable_record_and_reads_the_rest():
    reader = common.RecordReader()

    def read(record):
        if record == "skip":
            return None
        return 1000 // record["n"]

    # A missing member and a record of another type are each counted; a
    # record read as None is passed over without being counted.
    records = [{"n": 1}, {"m": 2}, "x", "skip", {"n": 4}]
    assert list(reader.each(records, read)) == [1000, 250]
    assert reader.invalid == 2


@pytest.mark.parametrize(
    "failure",
    [
        RecursionError(),
        OverflowError(),
        ValueError(),
        AttributeError(),
        IndexError(),
        common.UnusableRecord("refused"),
    ],
)
def test_reader_counts_each_shape_failure(failure):
    reader = common.RecordReader()

    def read(record):
        if record:
            raise failure
        return "kept"

    assert list(reader.each([True, False], read)) == ["kept"]
    assert reader.invalid == 1


@pytest.mark.parametrize(
    "failure",
    [
        SoftTimeLimitExceeded(),
        httpx.ConnectError("down"),
        OSError("storage"),
        RuntimeError("upstream"),
    ],
)
def test_reader_never_contains_a_run_failure(failure):
    reader = common.RecordReader()

    def read(record):
        raise failure

    with pytest.raises(type(failure)):
        list(reader.each([1, 2], read))
    assert reader.invalid == 0


def test_a_storage_failure_after_reading_still_fails_the_input():
    reader = common.RecordReader()
    with pytest.raises(OSError):
        for value in reader.each([1, 2], lambda record: record):
            raise OSError("storage")
    assert reader.invalid == 0


# --- End to end, through the reported collector -------------------------------


def _jenkins_run(builds, cursor):
    stored = []

    def handler(request):
        # Served as Jenkins writes it, NaN included.
        body = json.dumps({"allBuilds": builds}).encode()
        return httpx.Response(
            200, content=body, headers={"content-type": "application/json"}
        )

    def client(**kwargs):
        return httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://jenkins.example",
        )

    with (
        patch.object(jk.settings, "jenkins_base_url", "https://jenkins.example"),
        patch.object(jk, "is_safe_url", return_value=True),
        patch.object(jk, "ingestion_client", client),
        patch.object(jk, "_get_or_create_cursor", return_value=cursor),
        patch.object(
            jk,
            "store_event_with_artifact",
            side_effect=lambda db, **kw: stored.append(kw) or {"deduped": False},
        ),
    ):
        result = jk.ingest_jenkins_job(MagicMock(), "deploy", None, None)
    return result, stored


@pytest.mark.parametrize("poison", ["oops", 1e30, -1e30, float("nan"), [1], {"ms": 1}])
def test_one_unreadable_build_neither_stops_nor_freezes_the_job(poison):
    builds = [  # newest first, as Jenkins lists them
        {"number": 3, "result": "SUCCESS", "timestamp": 1_760_000_003_000},
        {"number": 2, "result": "SUCCESS", "timestamp": poison},
        {"number": 1, "result": "SUCCESS", "timestamp": 1_760_000_001_000},
    ]
    cursor = MagicMock(last_ts=None)
    for run in range(2):
        result, stored = _jenkins_run(builds, cursor)
        # The next run reads past the builds collected, the unreadable one too.
        assert result["invalid_records"] == (1 if run == 0 else 0)
        assert [s["summary"] for s in stored] == (
            ["jenkins deploy build #1 SUCCESS", "jenkins deploy build #3 SUCCESS"]
            if run == 0
            else []
        )
        assert cursor.meta == {"last_number": 3, "pending": []}
        # The cursor moves past the unreadable build, to the newest stored.
        assert cursor.last_ts == datetime.fromtimestamp(
            1_760_000_003, timezone.utc
        ).replace(tzinfo=None)


def test_a_record_nested_past_the_recursion_limit_is_counted():
    deep: list = []
    for _ in range(100_000):
        deep = [deep]
    reader = common.RecordReader()
    reads = reader.each([{"deep": deep}, {"flat": 1}], lambda r: json.dumps(r))
    assert list(reads) == ['{"flat": 1}']
    assert reader.invalid == 1
