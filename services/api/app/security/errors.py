"""Public diagnostics must not include provider bodies, URLs or database values."""

import socket
import ssl

import httpx
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded


def collection_error(exc):
    """A bounded, generic description of one input's failure.

    A collector reports each input's failure and carries on with the next.
    The task's time limit is not one input's failure: it ends the whole run,
    so it is raised again here, from every handler that describes a failure,
    and the run stops with the pages and inputs it has finished.
    """
    if isinstance(exc, (SoftTimeLimitExceeded, TimeLimitExceeded)):
        raise exc
    if isinstance(exc, httpx.HTTPStatusError):
        return (
            f"Collection failed: upstream returned HTTP {int(exc.response.status_code)}"
        )
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "Collection timed out; narrow the input or check upstream availability"
    if isinstance(exc, ssl.SSLError):
        return "TLS verification failed; check the upstream certificate and trusted CA"
    if isinstance(exc, (socket.gaierror, httpx.ConnectError)):
        return "Connection failed; check DNS, connectivity and the upstream TLS certificate"
    return "Collection failed; check the input, credentials, upstream service and storage configuration"
