"""Coroutines hand database, Valkey, storage and broker work to the thread pool.

Each API process runs one event loop, so a coroutine that does synchronous
I/O, or long Python work on a request body, holds up every other request in
that process, the health check included, until it returns. Coroutines in the
application therefore await only asynchronous calls and pass synchronous work
to ``run_in_threadpool`` or ``anyio.to_thread.run_sync``.

This test reads every ``async def`` in the application and fails when one
calls, without such a hand-off:

- anything given the request's database session (``db``), or a method on it;
- a function that opens Valkey, storage or the task broker;
- a function of the same module that does either of these; or
- a function the coroutine was given, which it cannot know to be asynchronous.
"""

import ast
import asyncio
import pathlib

import pytest
from app.core.config import settings
from app.ingest import webhooks

from tests.conftest import contract_db, contract_postgres_engine  # noqa: F401
from tests.test_assembled_authority_regressions import keen  # noqa: F401

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

# Calls that do synchronous network or disk work wherever they are made.
BLOCKING_CALLS = frozenset(
    {
        "get_valkey",
        "put_bytes",
        "get_object_stream",
        "address_window_allow",
        "fixed_window_allow",
        "fixed_window_spend",
        "store_event_with_artifact",
        "delay",
        "apply_async",
        "SessionLocal",
    }
)
# Calls that run their arguments in a worker thread.
HAND_OFFS = frozenset({"run_in_threadpool", "run_sync"})


def _name(func) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


SESSIONS = {"db"}


def _is_db(node) -> bool:
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name) and node.id in SESSIONS


def _session_names(function) -> set[str]:
    """Parameters holding a database session: ``db``, or annotated Session."""
    names = set()
    for a in [
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    ]:
        if a.arg == "db" or (
            a.annotation is not None and "Session" in ast.unparse(a.annotation)
        ):
            names.add(a.arg)
    return names


def _direct_calls(body):
    """Calls a function body makes itself: not in nested functions, not
    awaited, and not inside arguments handed to a worker thread."""
    found = []

    def visit(node, awaited=False):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return
        if isinstance(node, ast.Await):
            value = node.value
            if isinstance(value, ast.Call):
                if _name(value.func) in HAND_OFFS:
                    _arguments(value)
                    return
                # The awaited call is a coroutine; its own body is checked
                # where it is defined. Its arguments are evaluated here.
                for child in [*value.args, *(k.value for k in value.keywords)]:
                    visit(child)
                visit(value.func)
                return
        if isinstance(node, ast.Call):
            if _name(node.func) in HAND_OFFS:
                _arguments(node)
                return
            found.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    def _arguments(call):
        # A hand-off runs its callable in a worker thread, but its arguments
        # are evaluated here, on the loop. functools.partial only binds them.
        for argument in [*call.args, *(k.value for k in call.keywords)]:
            if isinstance(argument, ast.Call) and _name(argument.func) == "partial":
                _arguments(argument)
            else:
                visit(argument)

    for statement in body:
        visit(statement)
    return found


def _blocks(call, local_blocking) -> bool:
    if _name(call.func) in BLOCKING_CALLS:
        return True
    if isinstance(call.func, ast.Attribute) and _is_db(call.func.value):
        return True
    if any(_is_db(arg) for arg in call.args) or any(
        _is_db(k.value) for k in call.keywords
    ):
        return True
    return isinstance(call.func, ast.Name) and call.func.id in local_blocking


def blocking_calls(source: str, filename: str):
    """(file, coroutine, line, call) for each blocking call a coroutine makes."""
    tree = ast.parse(source)
    functions = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.setdefault(node.name, node)
    sync = {n: f for n, f in functions.items() if isinstance(f, ast.FunctionDef)}
    # Module functions that block, directly or through each other.
    local_blocking: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, function in sync.items():
            if name in local_blocking:
                continue
            if any(_blocks(c, local_blocking) for c in _direct_calls(function.body)):
                local_blocking.add(name)
                changed = True
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            SESSIONS.clear()
            SESSIONS.update({"db"} | _session_names(node))
            given = {
                a.arg
                for a in [
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                ]
            }
            for call in _direct_calls(node.body):
                passed_in = isinstance(call.func, ast.Name) and call.func.id in given
                if passed_in or _blocks(call, local_blocking):
                    found.append(
                        (filename, node.name, call.lineno, ast.unparse(call.func))
                    )
            SESSIONS.clear()
            SESSIONS.add("db")
    return found


def test_no_coroutine_blocks_the_event_loop():
    found = []
    for path in sorted(APP.rglob("*.py")):
        found += blocking_calls(path.read_text(), path.relative_to(APP).as_posix())
    assert not found, (
        "These coroutines run synchronous database, Valkey, storage or broker "
        "work on the event loop; pass it to run_in_threadpool: "
        + "; ".join(f"{f}:{line} {fn} calls {call}" for f, fn, line, call in found)
    )


def test_the_check_detects_the_webhook_defect():
    # The shape found in the webhook route: synchronous work on the loop,
    # through a session argument and through a module helper using Valkey.
    source = (
        "def _limit(request):\n"
        "    address_window_allow(get_valkey(), 'x', None, 1, 1)\n"
        "async def webhook(request, db):\n"
        "    _limit(request)\n"
        "    rows = connections(db, 'webhooks')\n"
        "    body = await request.body()\n"
        "    return ingest_webhook(db, body=body)\n"
    )
    calls = {call for _, _, _, call in blocking_calls(source, "routes/x.py")}
    assert calls == {"_limit", "connections", "ingest_webhook"}


def test_the_check_detects_a_given_operation_run_on_the_loop():
    source = (
        "async def _in_thread(operation, **kwargs):\n"
        "    result = operation(**kwargs)\n"
        "    await publish()\n"
        "    return result\n"
    )
    assert [c for *_, c in blocking_calls(source, "services/x.py")] == ["operation"]


def test_question_operations_run_off_the_event_loop():
    from app.services import questions

    seen = []

    def operation(*, notices, value):
        seen.append((_off_the_loop(), value))
        notices.admin_open = None
        return value

    assert asyncio.run(questions._in_thread(operation, value=7)) == 7
    assert seen == [(True, 7)]


def test_arguments_given_to_a_hand_off_are_checked():
    source = (
        "async def start(request):\n"
        "    return await run_sync(partial(allow, get_valkey(), 'sso-start'))\n"
    )
    assert [c for *_, c in blocking_calls(source, "security/x.py")] == ["get_valkey"]


def test_a_session_under_another_name_is_recognised():
    source = (
        "async def handler(request, session: Session):\n"
        "    return session.query(Event).all()\n"
    )
    calls = [c for *_, c in blocking_calls(source, "routes/x.py")]
    assert "session.query" in calls


def test_hand_offs_and_awaited_coroutines_pass():
    source = (
        "async def webhook(request, db):\n"
        "    body = await request.body()\n"
        "    await notify(len(body))\n"
        "    return await run_in_threadpool(partial(ingest_webhook, db, body=body))\n"
    )
    assert blocking_calls(source, "routes/x.py") == []


# --- Through the assembled application ---------------------------------------


def _off_the_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return True
    return False


@pytest.fixture
def hook(keen, monkeypatch):  # noqa: F811 - fixture imported above
    monkeypatch.setattr(settings, "webhooks_enabled", True)
    monkeypatch.setattr(
        webhooks,
        "load_webhook_policies",
        lambda path: {
            "providers": {
                "monitor": {
                    "secret_header": "X-Monitor-Secret",
                    "secret_env": "KEEN_TEST_WEBHOOK_SECRET",
                },
                "pager": {
                    "secret_header": "X-Pager-Secret",
                    "secret_env": "KEEN_TEST_PAGER_SECRET",
                },
            }
        },
    )
    monkeypatch.setenv("KEEN_TEST_WEBHOOK_SECRET", "synthetic-secret")
    monkeypatch.setenv("KEEN_TEST_PAGER_SECRET", "synthetic-pager-secret")
    return keen


def _deliver(client, body: bytes):
    return client.post(
        "/v1/webhooks/monitor/alert",
        content=body,
        headers={
            "X-Monitor-Secret": "synthetic-secret",
            "Content-Type": "application/json",
        },
    )


def test_webhook_storage_and_authentication_run_off_the_event_loop(hook, monkeypatch):
    from app import main

    seen = {}
    store = webhooks._store_webhook_event
    authenticate = main._authenticate

    def stored(*args, **kwargs):
        seen["store"] = _off_the_loop()
        return store(*args, **kwargs)

    def authenticated(request):
        seen["authenticate"] = _off_the_loop()
        return authenticate(request)

    monkeypatch.setattr(webhooks, "_store_webhook_event", stored)
    monkeypatch.setattr(main, "_authenticate", authenticated)
    client = hook.client()
    assert _deliver(client, b'{"alert": "synthetic"}').status_code == 200
    assert client.get("/v1/me").status_code == 401
    assert seen == {"store": True, "authenticate": True}


def test_a_connections_byte_allowance_bounds_its_deliveries(hook, monkeypatch):
    from app.api.routes import webhooks as route

    monkeypatch.setattr(route, "CONNECTION_BYTES_PER_MINUTE", 300)
    client = hook.client()
    first = _deliver(client, b'{"n": 1, "pad": "' + b"x" * 180 + b'"}')
    assert first.status_code == 200, first.text
    second = _deliver(client, b'{"n": 2, "pad": "' + b"x" * 180 + b'"}')
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) > 0
    # A wrong secret is refused before anything is spent or stored.
    wrong = client.post(
        "/v1/webhooks/monitor/alert",
        content=b"{}",
        headers={"X-Monitor-Secret": "wrong", "Content-Type": "application/json"},
    )
    assert wrong.status_code == 403
    # Another provider's secret on the same connection has its own allowance.
    pager = client.post(
        "/v1/webhooks/pager/alert",
        content=b'{"n": 3, "pad": "' + b"x" * 180 + b'"}',
        headers={
            "X-Pager-Secret": "synthetic-pager-secret",
            "Content-Type": "application/json",
        },
    )
    assert pager.status_code == 200, pager.text


def test_a_webhook_body_is_read_only_after_its_secret_and_allowances(hook, monkeypatch):
    from starlette.requests import Request

    reads = []
    original = Request.body

    async def body(self):
        reads.append(self.url.path)
        return await original(self)

    monkeypatch.setattr(Request, "body", body)
    client = hook.client()
    wrong = client.post(
        "/v1/webhooks/monitor/alert",
        content=b'{"x": 1}',
        headers={"X-Monitor-Secret": "wrong", "Content-Type": "application/json"},
    )
    assert wrong.status_code == 403 and reads == []
    monkeypatch.setattr(settings, "webhooks_enabled", False)
    for path in (
        "/v1/webhooks/monitor/alert",
        "/v1/webhooks/isms/effectiveness-metrics/m",
    ):
        assert client.post(path, content=b"{}").status_code == 503
    assert reads == []
    monkeypatch.setattr(settings, "webhooks_enabled", True)
    assert _deliver(client, b'{"x": 2}').status_code == 200
    assert reads == ["/v1/webhooks/monitor/alert"]


def _form_parse_threads(monkeypatch):
    """A Router app that records where forms are parsed and handlers run."""
    import threading

    from app.api.routing import Router
    from fastapi import FastAPI, File, Request, UploadFile
    from starlette import formparsers

    threads = {"parse": [], "handler": []}
    parse = formparsers.MultiPartParser.parse

    async def recording_parse(self):
        threads["parse"].append(threading.get_ident())
        return await parse(self)

    monkeypatch.setattr(formparsers.MultiPartParser, "parse", recording_parse)
    router = Router()

    @router.post("/manual")
    async def manual(request: Request):
        threads["handler"].append(threading.get_ident())
        form = await request.form()
        upload = form["attachment"]
        return {"body": form["body"], "size": len(await upload.read())}

    @router.post("/encoded")
    async def encoded(request: Request):
        return dict((await request.form()).multi_items())

    @router.post("/declared")
    async def declared(attachment: UploadFile = File(...)):
        threads["handler"].append(threading.get_ident())
        return {"size": len(await attachment.read())}

    app = FastAPI()
    app.include_router(router)
    return app, threads


def test_forms_are_parsed_off_the_event_loop(monkeypatch):
    """Multipart parsing holds no event loop, whoever reads the form."""
    from fastapi.testclient import TestClient

    app, threads = _form_parse_threads(monkeypatch)
    client = TestClient(app)
    payload = b"x" * (3 * 1024 * 1024)
    manual = client.post(
        "/manual",
        data={"body": "question"},
        files={"attachment": ("a.bin", payload, "application/octet-stream")},
    )
    assert manual.status_code == 200, manual.text
    assert manual.json() == {"body": "question", "size": len(payload)}
    declared = client.post(
        "/declared",
        files={"attachment": ("a.bin", payload, "application/octet-stream")},
    )
    assert declared.status_code == 200, declared.text
    assert declared.json() == {"size": len(payload)}
    assert len(threads["parse"]) == 2 and len(threads["handler"]) == 2
    assert not set(threads["parse"]) & set(threads["handler"])
    # URL-encoded forms are read the same way.
    encoded = client.post("/encoded", data={"body": "question", "n": "1"})
    assert encoded.status_code == 200, encoded.text
    assert encoded.json() == {"body": "question", "n": "1"}
    # Malformed multipart bodies are refused as Starlette refuses them.
    broken = client.post(
        "/manual",
        content=b"--x\r\nContent-Disposition: form-data\r\n\r\nno name\r\n--x--\r\n",
        headers={"Content-Type": "multipart/form-data; boundary=x"},
    )
    assert broken.status_code == 400


def test_every_body_route_uses_the_shared_route_class():
    """Routes outside DomainRoute would parse JSON and forms on the event loop."""
    from app import main
    from app.api.errors import DomainRoute
    from fastapi.routing import APIRoute

    def routes(items):
        for route in items:
            if isinstance(route, APIRoute):
                yield route
            elif hasattr(route, "routes"):
                yield from routes(route.routes)

    plain = sorted(
        f"{sorted(route.methods)} {route.path}"
        for route in routes(main.app.routes)
        if not isinstance(route, DomainRoute) and route.methods - {"GET", "HEAD"}
    )
    assert plain == []
