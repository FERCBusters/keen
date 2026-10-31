"""Shared route guards through the assembled application.

Evidence routes use one events.read dependency and question routes use the
common authentication guard; each route still refuses anonymous callers and,
for evidence, signed-in users without events.read.
"""

import uuid

import pytest

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (  # noqa: F401
    keen,
    unsafe,
)

ID = str(uuid.uuid4())

EVIDENCE_ROUTES = [
    ("GET", f"/v1/artifacts/{ID}/download"),
    ("GET", f"/v1/artifacts/{ID}/preview"),
    ("GET", "/v1/sources"),
    ("GET", "/v1/sources/meta"),
    ("GET", "/v1/sources/github/insights"),
    ("GET", f"/v1/controls/{ID}/evidence"),
    ("GET", f"/v1/controls/{ID}/evidence/summary"),
    ("GET", "/v1/events"),
    ("GET", "/v1/events/facets"),
    ("GET", "/v1/events/export"),
    ("GET", "/v1/evidence/latest"),
    ("GET", f"/v1/events/{ID}/control-suggestions"),
    ("GET", f"/v1/events/{ID}"),
    ("POST", f"/v1/events/{ID}/incident"),
    ("DELETE", f"/v1/events/{ID}/incidents/{ID}"),
]

QUESTION_ROUTES = [
    ("GET", f"/v1/events/{ID}/questions"),
    ("POST", f"/v1/events/{ID}/questions"),
    ("POST", f"/v1/questions/{ID}/posts"),
    ("DELETE", f"/v1/questions/{ID}"),
    ("GET", "/v1/me/questions/summary"),
    ("GET", "/v1/me/questions"),
    ("GET", f"/v1/questions/entities/risk/{ID}"),
    ("POST", f"/v1/questions/entities/risk/{ID}"),
    ("POST", f"/v1/questions/entities/risk/{ID}/mark-seen"),
    ("POST", f"/v1/events/{ID}/questions/mark-seen"),
]


def _call(client, method, path):
    headers = {}
    if method != "GET":
        # Anonymous callers have no CSRF cookie, so only the Origin header is sent.
        headers = {k: v for k, v in unsafe(client).items() if v is not None}
    body = {} if method == "POST" else None
    return client.request(
        method, path, headers=headers, **({"json": body} if body is not None else {})
    )


@pytest.mark.parametrize("method,path", EVIDENCE_ROUTES)
def test_evidence_routes_require_events_read(keen, method, path):  # noqa: F811
    assert _call(keen.client(), method, path).status_code == 401
    plain = keen.client(keen.user())
    response = _call(plain, method, path)
    assert response.status_code == 403, response.text
    reader = keen.client(keen.user("events.read"))
    if method == "GET":
        assert _call(reader, method, path).status_code not in (401, 403), path
    admin = keen.client(keen.user(role="admin"))
    assert _call(admin, method, path).status_code not in (401, 403), path


@pytest.mark.parametrize("method,path", QUESTION_ROUTES)
def test_question_routes_require_a_signed_in_user(keen, method, path):  # noqa: F811
    assert _call(keen.client(), method, path).status_code == 401
    response = _call(keen.client(keen.user()), method, path)
    assert response.status_code != 401, response.text
