"""Each value in an Events list filter is bounded before any query runs.

Requests come from a user who can read events, through the assembled
application; the bound applies to every comma-separated filter list.
"""

import pytest
from app.services import events as event_operations
from app.services.event_filters import MAX_VALUE_CHARS

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen  # noqa: F401


@pytest.mark.parametrize("name", ["source", "control", "clause", "rule"])
def test_an_overlong_value_in_a_filter_list_is_refused(keen, monkeypatch, name):  # noqa: F811
    reader = keen.client(keen.user("events.read"))
    longest = "x" * MAX_VALUE_CHARS

    queried = []
    original = event_operations.list_events

    def listing(**kwargs):
        queried.append(kwargs["filters"])
        return original(**kwargs)

    monkeypatch.setattr(event_operations, "list_events", listing)
    refused = reader.get("/v1/events", params={name: f"ci,{longest}y"})

    assert (refused.status_code, refused.json()["detail"]) == (
        400,
        f"A {name} value must be at most {MAX_VALUE_CHARS} characters",
    )
    assert queried == []
    # A value at the bound is accepted.
    accepted = reader.get("/v1/events", params={name: f"ci,{longest}"})
    assert (accepted.status_code, accepted.json()) == (
        200,
        {"total": 0, "limit": 50, "offset": 0, "items": []},
    )
    (filters,) = queried
    assert longest in getattr(filters, name + "s")
