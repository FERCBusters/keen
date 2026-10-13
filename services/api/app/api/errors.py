"""Translate expected service failures at the HTTP boundary."""

import json
import logging
import math
from functools import partial

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import DataError

from app.security.redaction import storable
from app.services.errors import DomainError

MAX_JSON_DEPTH = 64
logger = logging.getLogger(__name__)


async def domain_error_response(request: Request, error: DomainError):
    return JSONResponse(
        status_code=error.status_code,
        content={"detail": error.detail},
        headers=error.headers,
    )


async def validation_error_response(request: Request, error: RequestValidationError):
    """Name the invalid fields without echoing submitted values.

    Submitted values can contain credentials, and some (non-finite numbers or
    deeply nested documents) cannot be encoded in a JSON response at all.
    """
    detail = []
    for item in error.errors()[:50]:
        location = [
            part if isinstance(part, int) else str(part)[:128]
            for part in tuple(item.get("loc") or ())[:16]
        ]
        detail.append(
            {
                "type": str(item.get("type") or "")[:64],
                "loc": location,
                "msg": str(item.get("msg") or "")[:500],
            }
        )
    return JSONResponse(status_code=422, content={"detail": detail})


def _non_finite(_constant):
    raise ValueError("non-finite number")


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def _within_depth(value) -> bool:
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            return False
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
    return True


def decode_json_body(body: bytes):
    """Decode a request body as standard JSON that the application can store.

    Non-finite numbers and excessive nesting are rejected. NUL characters, which
    PostgreSQL text and JSON values cannot hold, become replacement characters.
    """
    try:
        value = json.loads(body, parse_constant=_non_finite, parse_float=_finite_float)
    except (ValueError, RecursionError):
        raise HTTPException(400, "Request body must be valid JSON") from None
    if not _within_depth(value):
        raise HTTPException(400, "Request body is nested too deeply")
    return storable(value) if b"\\u0000" in body else value


async def json_object(request: Request) -> dict:
    """The request body as a JSON object, for handlers that parse it themselves."""
    value = await request.json()
    if not isinstance(value, dict):
        raise HTTPException(400, "Request body must be a JSON object")
    return value


async def _strict_json(request: Request):
    if not hasattr(request, "_json"):
        request._json = decode_json_body(await request.body())
    return request._json


class DomainRoute(APIRoute):
    """Also support routers mounted independently in adapter tests."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            # Parsed only when a handler reads the body, after earlier checks.
            request.json = partial(_strict_json, request)
            try:
                return await handler(request)
            except DomainError as error:
                raise HTTPException(
                    error.status_code, error.detail, headers=error.headers
                ) from None
            except DataError as error:
                # PostgreSQL refused a value for its column type, such as an
                # out-of-range number. The transaction is rolled back; the log
                # names the database error class without the statement values.
                logger.warning(
                    "Database rejected a request value: %s",
                    type(getattr(error, "orig", error)).__name__,
                )
                raise HTTPException(400, "A request value cannot be stored") from None
            except OverflowError:
                # Date or number arithmetic on a request value left its range.
                logger.warning("Request value out of range in %s", self.path)
                raise HTTPException(400, "A request value is out of range") from None

        return handle
