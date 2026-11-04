"""Request forms, parsed in a worker thread.

Starlette's form parser runs its multipart state machine, part headers and
field handling on the coroutine that reads the body, so a large upload would
hold the event loop for as long as parsing takes. Every route reads its form
through parse_form instead (DomainRoute installs it as ``request.form``, which
FastAPI's File and Form parameters also call): the body, already bounded by
RequestBodyLimitMiddleware, is spooled to a temporary file and parsed there.
"""

import tempfile

import anyio
from fastapi import HTTPException
from python_multipart.multipart import parse_options_header
from starlette.datastructures import FormData
from starlette.formparsers import FormParser, MultiPartException, MultiPartParser
from starlette.requests import Request

_SPOOL_IN_MEMORY = 1024 * 1024
_READ_CHUNK = 64 * 1024


def _parse(headers, spool, content_type, limits) -> FormData:
    """Parse a spooled body with Starlette's own parser, on this thread's loop."""

    async def stream():
        while chunk := spool.read(_READ_CHUNK):
            yield chunk
        # Request.stream() ends with an empty chunk, which the URL-encoded
        # parser reads as the end of the form.
        yield b""

    async def parse():
        if content_type == b"multipart/form-data":
            return await MultiPartParser(headers, stream(), **limits).parse()
        return await FormParser(
            headers,
            stream(),
            max_fields=limits["max_fields"],
            max_part_size=limits["max_part_size"],
        ).parse()

    return anyio.run(parse)


async def parse_form(
    request: Request,
    *,
    max_files: int | float = 1000,
    max_fields: int | float = 1000,
    max_part_size: int = 1024 * 1024,
) -> FormData:
    """The request's form, parsed once and off the event loop."""
    if request._form is not None:
        return request._form
    content_type, _ = parse_options_header(request.headers.get("Content-Type"))
    if content_type not in (
        b"multipart/form-data",
        b"application/x-www-form-urlencoded",
    ):
        request._form = FormData()
        return request._form
    limits = {
        "max_files": max_files,
        "max_fields": max_fields,
        "max_part_size": max_part_size,
    }
    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_IN_MEMORY) as spool:
        async for chunk in request.stream():
            if chunk:
                await anyio.to_thread.run_sync(spool.write, chunk)
        await anyio.to_thread.run_sync(spool.seek, 0)
        try:
            request._form = await anyio.to_thread.run_sync(
                _parse, request.headers, spool, content_type, limits
            )
        except MultiPartException as exc:
            raise HTTPException(status_code=400, detail=exc.message) from None
    return request._form
