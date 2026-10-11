"""Translate expected service failures at the HTTP boundary."""

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.services.errors import DomainError


async def domain_error_response(request: Request, error: DomainError):
    return JSONResponse(
        status_code=error.status_code,
        content={"detail": error.detail},
        headers=error.headers,
    )


class DomainRoute(APIRoute):
    """Also support routers mounted independently in adapter tests."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except DomainError as error:
                raise HTTPException(
                    error.status_code, error.detail, headers=error.headers
                ) from None

        return handle
