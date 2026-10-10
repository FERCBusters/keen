"""HTTP contracts with injected identity but real permission checks and SQL."""

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from app.api.routes import control_links, events
from app.core.config import settings
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    Event,
    Framework,
    FrameworkClause,
    ManagedConfiguration,
    Mapping,
    OsaControlMapping,
    User,
)
from app.db.session import get_db
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def api(contract_db, monkeypatch):
    db = contract_db
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 0)
    monkeypatch.setattr(settings, "enabled_frameworks", "")
    monkeypatch.setattr(control_links, "cache_delete_prefix", lambda: 0)
    admin = User(username="admin", password_hash="unused", role="admin", is_active=True)
    a = ControlItem(framework_slug="A", ref="A1", type="control", title="A control")
    b = ControlItem(framework_slug="B", ref="B1", type="control", title="B control")
    c = ControlItem(framework_slug="A", ref="A2", type="control")
    db.add_all([admin, Framework(slug="A"), Framework(slug="B"), a, b, c])
    db.flush()
    selection = ManagedConfiguration(
        name="organisation-frameworks",
        version=1,
        document={"enabled": ["A", "B"], "default": "A"},
    )
    db.add(selection)
    rows = [
        Event(
            source=src,
            external_id=str(i),
            timestamp=datetime(2025, 1, day, hour),
            summary="Example",
            system=system,
            actor=actor,
        )
        for i, (src, day, hour, system, actor) in enumerate(
            [
                ("keen-agent", 1, 0, "alpha", "alice"),
                ("keen-agent", 1, 12, "beta", "bob"),
                ("rss", 2, 0, "alpha", "alice"),
                ("diary", 3, 0, "private", "alice"),
            ]
        )
    ]
    db.add_all(rows)
    db.flush()
    db.add_all(
        [
            Mapping(event_id=rows[0].id, control_item_id=a.id, method="manual"),
            Mapping(event_id=rows[1].id, control_item_id=b.id, method="manual"),
            OsaControlMapping(control_id=a.id, nist_ref="AC-1"),
            OsaControlMapping(control_id=b.id, nist_ref="AC-1"),
        ]
    )
    clause = FrameworkClause(framework_slug="A", ref="5.1", title="Clause")
    db.add(clause)
    db.flush()
    db.add(
        ControlClauseLink(
            control_item_id=a.id, clause_id=clause.id, applicability="applicable"
        )
    )
    db.commit()
    identity = {"user": admin}
    app = FastAPI()
    app.include_router(events.router, prefix="/api")
    app.include_router(control_links.router, prefix="/api")

    @app.middleware("http")
    async def attach_identity(request, call_next):
        if identity["user"] is not None:
            request.state.user = identity["user"]
        return await call_next(request)

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            db=db,
            identity=identity,
            rows=rows,
            a=a,
            b=b,
            c=c,
            selection=selection,
        )


def get(api, **params):
    return api.client.get("/api/v1/events", params={"framework": "A", **params})


@pytest.mark.parametrize(
    "params,indices",
    [
        ({}, [3, 2, 1, 0]),
        ({"source": "keen-agent"}, [1, 0]),
        ({"source": "missing"}, []),
        ({"system": "ALP"}, [2, 0]),
        ({"actor": "BO"}, [1]),
        ({"start_date": "2025-01-01", "end_date": "2025-01-01"}, [1, 0]),
        (
            {
                "start_ts": "2025-01-01T11:00:00+11:00",
                "end_ts": "2025-01-01T23:00:00+11:00",
            },
            [0],
        ),
        ({"start_date": "2025-01-01", "start_ts": "2025-01-02T00:00:00Z"}, [3, 2]),
        ({"control": "A1"}, [1, 0]),
        ({"control": "missing"}, []),
        ({"unmapped": "true"}, [3, 2]),
        ({"clause": "5.1"}, [1, 0]),
        ({"clause": "missing"}, []),
    ],
)
def test_filters_and_half_open_utc_boundaries(api, params, indices):
    response = get(api, **params)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == len(indices)
    assert [r["id"] for r in data["items"]] == [str(api.rows[i].id) for i in indices]


def test_pagination_count_and_limits(api):
    data = get(api, limit=1, offset=1).json()
    assert (
        data["total"] == 4
        and len(data["items"]) == 1
        and data["items"][0]["id"] == str(api.rows[2].id)
    )
    assert get(api, limit=999, offset=-1).json()["limit"] == 200
    assert get(api, offset=100).json()["items"] == []


def test_foreign_control_id_does_not_leak_matches(api):
    assert get(api, control=str(api.b.id)).json()["total"] == 0
    assert get(api, control=str(api.a.id)).json()["total"] == 2


def test_disabling_source_framework_changes_filters(api):
    api.selection.document = {"enabled": ["A"], "default": "A"}
    api.db.commit()
    assert get(api, control="A1").json()["total"] == 1
    assert get(api, unmapped="true").json()["total"] == 3
    assert get(api, clause="5.1").json()["total"] == 1


@pytest.mark.parametrize(
    "path", ["/api/v1/events", "/api/v1/events/facets", "/api/v1/events/export"]
)
def test_route_permissions(api, path):
    api.identity["user"] = None
    assert api.client.get(path).status_code == 401
    api.identity["user"] = User(
        id=uuid.uuid4(), username="reader", role="normal", is_active=True
    )
    assert api.client.get(path).status_code == 403
    api.identity["user"].effective_permission_codes = {"events.read"}
    assert api.client.get(path).status_code == 200


@pytest.mark.parametrize(
    "event_id,status", [("not-a-uuid", 400), (str(uuid.uuid4()), 404)]
)
def test_event_detail_validation(api, event_id, status):
    assert api.client.get("/api/v1/events/" + event_id).status_code == status


def test_facet_counts_and_unmapped_controls(api):
    data = api.client.get("/api/v1/events/facets", params={"framework": "A"}).json()
    assert {x["source"]: x["count"] for x in data["sources"]} == {
        "keen-agent": 2,
        "rss": 1,
        "diary": 1,
    }
    assert data["controls"][0]["count"] == 2
    data = api.client.get(
        "/api/v1/events/facets", params={"framework": "A", "unmapped": "true"}
    ).json()
    assert data["controls"] == []


def test_crosswalk_crud_scope_conflict_and_admin_permission(api):
    url = f"/api/v1/controls/{api.a.id}/cross-framework"
    response = api.client.post(
        url, json={"source_control_id": str(api.b.id), "rationale": "Reviewed"}
    )
    assert response.status_code == 201, response.text
    link = response.json()
    assert link["rationale"] == "Reviewed"
    assert (
        api.client.post(
            url, json={"source_control_id": str(api.b.id), "rationale": "Duplicate"}
        ).status_code
        == 409
    )
    assert (
        api.client.delete(
            f"/api/v1/controls/{api.c.id}/cross-framework/{link['id']}"
        ).status_code
        == 404
    )
    assert api.client.delete(url + "/" + link["id"]).status_code == 204
    items = api.client.get(url).json()["items"]
    assert len(items) == 1 and items[0]["derived"] is True
    api.identity["user"] = User(
        id=uuid.uuid4(), username="reader", role="normal", is_active=True
    )
    assert (
        api.client.post(
            url, json={"source_control_id": str(api.b.id), "rationale": "No"}
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "kind,status", [("same", 400), ("missing", 404), ("blank", 400), ("invalid", 422)]
)
def test_invalid_crosswalk_creation(api, kind, status):
    source = (
        str(api.c.id)
        if kind == "same"
        else str(uuid.uuid4())
        if kind == "missing"
        else "invalid"
        if kind == "invalid"
        else str(api.b.id)
    )
    response = api.client.post(
        f"/api/v1/controls/{api.a.id}/cross-framework",
        json={
            "source_control_id": source,
            "rationale": "   " if kind == "blank" else "Reason",
        },
    )
    assert response.status_code == status, response.text


@pytest.mark.parametrize("format", ["csv", "json", "ndjson"])
def test_exports_respect_filters_and_row_cap(api, format):
    import csv
    import io
    import json

    response = api.client.get(
        "/api/v1/events/export",
        params={
            "framework": "A",
            "source": "keen-agent",
            "format": format,
            "max_rows": 1,
        },
    )
    assert response.status_code == 200, response.text
    if format == "json":
        data = response.json()
        assert data["total"] == 2 and data["returned"] == 1
        rows = data["items"]
    elif format == "ndjson":
        rows = [json.loads(line) for line in response.text.splitlines() if line]
    else:
        rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1
    assert rows[0]["source"] == "keen-agent"


def test_export_rejects_unknown_format(api):
    assert (
        api.client.get("/api/v1/events/export", params={"format": "html"}).status_code
        == 400
    )


def test_clause_filter_works_in_facets_and_export(api):
    facets = api.client.get(
        "/api/v1/events/facets", params={"framework": "A", "clause": "5.1"}
    )
    assert facets.status_code == 200, facets.text
    assert facets.json()["sources"] == [{"source": "keen-agent", "count": 2}]
    export = api.client.get(
        "/api/v1/events/export",
        params={"framework": "A", "clause": "5.1", "format": "json"},
    )
    assert export.status_code == 200, export.text
    assert export.json()["total"] == 2


@pytest.mark.parametrize(
    "payload", ["=1+1", "+1+1", "-1+1", "@SUM(1)", "\t=1", "\r=1", "\n=1", "  =1"]
)
def test_csv_export_neutralises_source_formulas_without_changing_json(api, payload):
    import csv
    import io

    row = api.rows[0]
    row.summary = payload
    row.actor = payload
    api.db.commit()
    response = api.client.get(
        "/api/v1/events/export", params={"framework": "A", "format": "csv"}
    )
    assert response.status_code == 200
    item = next(
        r for r in csv.DictReader(io.StringIO(response.text)) if r["id"] == str(row.id)
    )
    assert item["summary"] == "'" + payload
    assert item["actor"] == "'" + payload
    response = api.client.get(
        "/api/v1/events/export", params={"framework": "A", "format": "json"}
    )
    item = next(r for r in response.json()["items"] if r["id"] == str(row.id))
    assert item["summary"] == payload
