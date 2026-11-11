"""A request target names one route: encoded separators cannot reach another.

Browser pages build API paths from record identifiers and query values, and
send them with the page's session and CSRF token. A path parameter carrying an
encoded "/" or "../" that some proxy decodes and resolves would turn such a
request into one for a different route. The supplied nginx forwards the API
target as the browser sent it, and the API refuses any target with a "." or
".." segment, or an encoded slash outside a ``:path`` parameter.
"""

import re
from pathlib import Path

import pytest
from app.security.request_limits import ENCODED_SLASH_ROUTES, target_traverses
from starlette.convertors import PathConvertor

from tests.conftest import contract_db, contract_postgres_engine  # noqa: F401
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401

NGINX = Path(__file__).resolve().parents[2] / "ui" / "nginx" / "default.conf"


def _scope(raw: bytes):
    from urllib.parse import unquote

    return {"type": "http", "path": unquote(raw.decode()), "raw_path": raw}


@pytest.mark.parametrize(
    "raw",
    [
        # The reported link: a quiz round id that climbs to another route.
        b"/v1/me/quizzes/rounds/..%2F..%2F..%2Fadmin%2Fusers%2FU1%2Fpassword/attempt",
        b"/v1/pestle/items/..%2Fadmin%2Fgroups%2Fg1",
        b"/v1/sources/%2e%2e%2Fme",
        b"/v1/sources/x%2Fy/insights",
        b"/v1/sources/x%5Cy/insights",
        b"/v1/./me",
        b"/v1/sources/../me",
    ],
)
def test_traversing_targets_are_recognised(raw):
    assert target_traverses(_scope(raw))


@pytest.mark.parametrize(
    "raw",
    [
        b"/v1/me",
        b"/v1/sources/keen-agent/insights",
        b"/v1/sources/a.b..c/insights",
        b"/v1/admin/frameworks/iso/nodes/control/A%2F1",
    ],
)
def test_ordinary_targets_pass(raw):
    assert not target_traverses(_scope(raw))


def _routes(routes, prefix=""):
    """Every served route with its full path, as the access inventory reads them."""
    for route in routes:
        if hasattr(route, "original_router"):
            context = getattr(route, "include_context", None)
            yield from _routes(
                route.original_router.routes,
                prefix + (getattr(context, "prefix", "") or ""),
            )
        elif hasattr(route, "param_convertors"):
            yield prefix + route.path, route
        elif hasattr(route, "routes"):
            yield from _routes(route.routes, prefix + getattr(route, "path", ""))


def test_every_path_parameter_route_is_allowed_its_encoded_slash():
    from app import main

    seen = 0
    for path, route in _routes(main.app.routes):
        seen += 1
        sample = re.sub(r"\{[^}]+\}", "x", path)
        takes_slash = any(
            isinstance(c, PathConvertor) for c in route.param_convertors.values()
        )
        assert bool(ENCODED_SLASH_ROUTES.fullmatch(sample)) is takes_slash, (
            path,
            "routes with a :path parameter, and only those, belong in "
            "ENCODED_SLASH_ROUTES",
        )
    assert seen > 400


def test_the_supplied_proxy_forwards_the_api_target_as_sent():
    policy = NGINX.read_text()
    api = policy[policy.index("location ^~ /api/ {") :]
    api = api[: api.index("\n  }\n")]
    assert "rewrite" not in api
    assert api.count("proxy_pass $keen_api$keen_api_target;") == 2
    assert '"~^/api(/.*)$" $1;' in policy


@pytest.mark.parametrize(
    "path",
    [
        "/v1/me/quizzes/rounds/..%2F..%2F..%2Fme%2Finbox%2Fread-all/attempt",
        "/v1/sources/..%2Fme",
        "/v1/sources/%2e%2e%2Fme",
    ],
)
def test_the_application_refuses_them_before_authentication(
    keen,  # noqa: F811 - fixture imported above
    path,
):
    client = keen.client(keen.user())
    assert client.get("/v1/me").status_code == 200
    response = client.post(path, headers=unsafe(client))
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid request target"}
    refused = client.get(path)
    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "Invalid request target"},
    )
