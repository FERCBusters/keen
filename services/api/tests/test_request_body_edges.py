"""Request bodies and domain errors at the HTTP boundary.

Covers the application-wide domain error handler for routes outside
DomainRoute, the single decode of a JSON or form body that both FastAPI and a
handler read, the availability check every DomainRoute carries once, form
parsing of bodies that are not forms, and diary entries whose controls are
neither a list nor text. Assembled-application requests use real sign-in,
CSRF and the production middleware.
"""

import uuid

import pytest
from app.api import errors, forms
from app.api.dependencies.features import enforce_features
from app.api.errors import json_object
from app.api.routing import Router
from app.db.models import Event, Mapping
from app.db.session import get_db
from app.services.errors import DomainError
from fastapi import Depends, FastAPI, Form, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)


def _adapter(router):
    """An application holding only ``router``, as adapter tests mount one."""
    app = FastAPI()
    app.include_router(router)

    def no_database():
        yield None

    app.dependency_overrides[get_db] = no_database
    return TestClient(app)


def test_a_domain_error_outside_domain_route_keeps_its_status_and_headers(
    keen,  # noqa: F811 - fixture imported above
):
    from app import main

    def refused():
        raise DomainError(409, "Synthetic conflict", headers={"Retry-After": "7"})

    # Routes declared on the application itself use FastAPI's own route class.
    main.app.add_api_route("/v1/synthetic-domain-error", refused, methods=["GET"])
    route = main.app.router.routes[-1]
    try:
        assert type(route) is not errors.DomainRoute
        response = keen.client(keen.user()).get("/v1/synthetic-domain-error")
    finally:
        main.app.router.routes.remove(route)

    assert response.status_code == 409
    assert response.json() == {"detail": "Synthetic conflict"}
    assert response.headers["retry-after"] == "7"


class Item(BaseModel):
    name: str


def test_a_json_body_read_by_fastapi_and_the_handler_is_decoded_once(monkeypatch):
    decoded = []
    decode = errors.decode_json_body

    def counted(body, max_values=None):
        decoded.append(body)
        return decode(body, max_values)

    monkeypatch.setattr(errors, "decode_json_body", counted)
    router = Router()

    @router.post("/synthetic/items")
    async def create(item: Item, request: Request):
        again = await json_object(request)
        return {"model": item.name, "handler": again}

    response = _adapter(router).post(
        "/synthetic/items", json={"name": "backup-check", "extra": 1}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "model": "backup-check",
        "handler": {"name": "backup-check", "extra": 1},
    }
    assert decoded == [b'{"name":"backup-check","extra":1}']


def test_a_form_read_by_fastapi_and_the_handler_is_parsed_once(monkeypatch):
    parsed = []
    parse = forms._parse

    def counted(*args):
        parsed.append(args[2])
        return parse(*args)

    monkeypatch.setattr(forms, "_parse", counted)
    router = Router()

    @router.post("/synthetic/forms")
    async def submit(request: Request, name: str = Form(...)):
        first, second = await request.form(), await request.form()
        return {"field": name, "form": first["name"], "same": first is second}

    response = _adapter(router).post("/synthetic/forms", data={"name": "policy"})

    assert response.status_code == 200, response.text
    assert response.json() == {"field": "policy", "form": "policy", "same": True}
    assert parsed == [b"application/x-www-form-urlencoded"]


def test_every_domain_route_carries_one_availability_check():
    declared = Router(dependencies=[Depends(enforce_features)])
    plain = Router()

    @declared.get("/synthetic/declared")
    def with_check():
        return {}

    @plain.get("/synthetic/plain")
    def without_check():
        return {}

    for router in (declared, plain):
        (route,) = router.routes
        checks = [d for d in route.dependencies if d.dependency is enforce_features]
        assert len(checks) == 1, route.path
        assert _adapter(router).get(route.path).status_code == 200


@pytest.fixture
def diary(keen):  # noqa: F811 - fixture imported above
    client = keen.client(keen.user(role="admin"))

    def post(headers=None, **kwargs):
        return client.post(
            "/v1/admin/diary", headers={**unsafe(client), **(headers or {})}, **kwargs
        )

    return keen.db, post


@pytest.mark.parametrize("content_type", ["text/plain", "application/octet-stream"])
def test_a_diary_body_that_is_not_a_form_is_read_as_an_empty_form(diary, content_type):
    db, post = diary
    body = b"summary=Backups+restored"
    before = db.query(Event).filter_by(source="diary").count()

    refused = post(content=body, headers={"Content-Type": content_type})

    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == "Missing summary"
    assert db.query(Event).filter_by(source="diary").count() == before
    # The same body sent as a form is read.
    form = post(
        content=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert form.status_code == 200, form.text


@pytest.mark.parametrize("controls", [{"A.5.1": True}, 5, True])
def test_diary_controls_that_are_neither_text_nor_a_list_map_nothing(diary, controls):
    db, post = diary

    created = post(json={"summary": "Access review signed", "controls": controls})

    assert created.status_code == 200, created.text
    assert created.json()["manual_mappings_created"] == 0
    event_id = uuid.UUID(created.json()["event_id"])
    assert db.query(Mapping).filter_by(event_id=event_id).count() == 0
