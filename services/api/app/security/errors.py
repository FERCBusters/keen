"""Public diagnostics must not include provider bodies, URLs or database values."""
import ssl
import socket
import httpx


def collection_error(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Collection failed: upstream returned HTTP {int(exc.response.status_code)}"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "Collection timed out; narrow the input or check upstream availability"
    if isinstance(exc, ssl.SSLError):
        return "TLS verification failed; check the upstream certificate and trusted CA"
    if isinstance(exc, (socket.gaierror, httpx.ConnectError)):
        return "Connection failed; check DNS, connectivity and the upstream TLS certificate"
    return "Collection failed; check the input, credentials, upstream service and storage configuration"
