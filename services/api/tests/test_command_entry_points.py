"""The hosted commands' script entry points, and the API module with SQL tracing.

`python -m app.bootstrap_hosted` and `python -m app.hosted_health` run their
module as a script, and the API module adds request labelling when SQL tracing
is switched on. Each module is executed from its source, as the interpreter
runs it, so the code under its entry point is the code tested.
"""

import runpy

import pytest
from app import bootstrap_hosted, hosted_health, main
from app.api.sql_trace import RequestLabelMiddleware
from app.core.config import settings


@pytest.mark.parametrize(
    "module,message",
    [
        (bootstrap_hosted, "Hosted mode is required"),
        (hosted_health, "Not a hosted installation"),
    ],
    ids=["bootstrap", "health"],
)
def test_a_hosted_command_run_outside_hosted_mode_stops_with_an_error(
    monkeypatch, module, message
):
    monkeypatch.setattr(settings, "hosted_mode", False)

    with pytest.raises(RuntimeError, match=f"^{message}$"):
        runpy.run_path(module.__file__, run_name="__main__")


def test_sql_tracing_labels_requests_from_the_outermost_middleware(monkeypatch):
    monkeypatch.setattr(settings, "sql_trace", True)

    traced = runpy.run_path(main.__file__, run_name="keen_traced_api")["app"]

    layers = [m.cls for m in traced.user_middleware]
    assert layers[0] is RequestLabelMiddleware
    # Every other layer is the untraced application's, in the same order.
    assert layers[1:] == [m.cls for m in main.app.user_middleware]
    assert RequestLabelMiddleware not in layers[1:]
