"""Shared HTTP boundary for built-in collectors.

Resolve and validate immediately before connecting, pin the selected address,
and retain the original hostname for TLS verification and the Host header.

Each request has one wall-clock budget covering the connection, TLS handshake,
request, response headers and body. A socket timeout restarts with every byte,
so an upstream that drips its status line or headers would otherwise hold a
request open indefinitely; every TLS socket operation therefore waits at most
for what remains of the budget. The custom API transport shares this bound.
"""

import ipaddress
import socket
import ssl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.security.provider_logging import suppress_provider_logs

suppress_provider_logs("httpx")


MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_SECONDS = 120
# The upstream bytes a listing may keep in memory across all of its pages.
MAX_RETAINED_BYTES = MAX_RESPONSE_BYTES

_deadline: ContextVar[float | None] = ContextVar("keen_request_deadline", default=None)
# Open listing allowances, outermost first: [bytes counted, limit] each.
_listings: ContextVar[tuple[list[int], ...]] = ContextVar(
    "keen_retained_listings", default=()
)


class ListingTooLarge(ValueError):
    """A listing would keep more upstream data than a collection holds at once."""


@contextmanager
def retained_listing(limit: int | None = None):
    """Count every response read in the block against one allowance.

    A listing that keeps its pages until it has them all holds all of them
    parsed at once, and a parsed document costs many times its size. The
    allowance is one response's limit, so a listing holds no more than the
    costliest single response, which the parse memory budget measures against
    the worker limit, however many pages it reads.

    An allowance opened inside another also counts against the outer one
    while it is open; when it closes, normally or by an error, its bytes are
    released from the outer one.
    """
    outer = _listings.get()
    frame = [0, MAX_RETAINED_BYTES if limit is None else limit]
    token = _listings.set((*outer, frame))
    try:
        yield
    finally:
        _listings.reset(token)
        for enclosing in outer:
            enclosing[0] -= frame[0]


def _retain(size: int) -> None:
    frames = _listings.get()
    # Count the bytes in every open allowance before refusing any, so each
    # allowance releases exactly what it counted.
    for frame in frames:
        frame[0] += size
    for used, limit in frames:
        if used > limit:
            raise ListingTooLarge(
                "Listing exceeds the upstream data one collection holds "
                f"({limit // (1024 * 1024)} MiB); narrow the input"
            )


@contextmanager
def budget(deadline: float) -> Iterator[None]:
    """End this thread's socket operations at ``deadline`` (``time.monotonic``)."""
    token = _deadline.set(deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining(timeout: float | None = None) -> float | None:
    """``timeout`` limited to what is left of the current request's budget."""
    deadline = _deadline.get()
    if deadline is None:
        return timeout
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("Request exceeded its time budget")
    return left if timeout is None else min(timeout, left)


class BudgetedSocket(ssl.SSLSocket):
    """A TLS socket whose handshake, reads and writes end with the budget.

    Each operation's timeout is clamped just before it starts; CPython bounds a
    whole TLS read, write or handshake by the socket timeout, so a peer sending
    one byte at a time cannot extend a request past its deadline.
    """

    def _bound(self) -> None:
        timeout = self.gettimeout()
        if timeout != 0.0:  # leave non-blocking sockets as they are
            self.settimeout(remaining(timeout))

    def do_handshake(self, block=False):
        self._bound()
        return super().do_handshake(block)

    def read(self, len=1024, buffer=None):
        self._bound()
        return super().read(len, buffer)

    def write(self, data):
        self._bound()
        return super().write(data)

    def send(self, data, flags=0):
        self._bound()
        return super().send(data, flags)


def budgeted(context: ssl.SSLContext) -> ssl.SSLContext:
    """Make ``context`` create sockets that observe the current request budget.

    Certificate and hostname verification are those of ``context``.
    """
    context.sslsocket_class = BudgetedSocket
    return context


def destination(url):
    parsed = urlsplit(str(url))
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or "\\" in str(url)
        or any(ord(c) < 32 for c in str(url))
    ):
        raise ValueError(
            "Use an HTTPS destination without embedded credentials or fragment"
        )
    port = parsed.port or 443
    # A malformed restriction must never silently turn into unrestricted access.
    networks = [
        ipaddress.ip_network(v, strict=False)
        for v in settings.ingestion_allowed_cidrs.replace(",", " ").split()
    ]
    addresses = list(
        dict.fromkeys(
            row[4][0]
            for row in socket.getaddrinfo(
                parsed.hostname, port, type=socket.SOCK_STREAM
            )
        )
    )
    if not addresses:
        raise ValueError("Ingestion destination has no addresses")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        ip = getattr(ip, "ipv4_mapped", None) or ip
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
            or ip.is_reserved
        ):
            raise ValueError("Ingestion destination address is prohibited")
        if networks and not ip.is_global and not any(ip in n for n in networks):
            raise ValueError("Ingestion destination is outside the approved networks")
    return addresses


def require_origin(url, endpoint):
    def origin(value):
        u = urlsplit(value)
        if (
            u.scheme != "https"
            or not u.hostname
            or u.username
            or u.password
            or u.fragment
        ):
            raise ValueError("Use an HTTPS feed and connection endpoint")
        return u.scheme, u.hostname.lower(), u.port or 443

    if origin(url) != origin(endpoint):
        raise ValueError(
            "Feed credentials may only be sent to the connection endpoint origin"
        )


class LimitedStream(httpx.SyncByteStream):
    def __init__(self, stream, deadline=None):
        self.stream = stream
        self.deadline = (
            deadline
            if deadline is not None
            else time.monotonic() + MAX_RESPONSE_SECONDS
        )

    def __iter__(self):
        count = 0
        chunks = iter(self.stream)
        try:
            while True:
                # The budget applies only while this stream reads, never while
                # the caller holds a chunk.
                with budget(self.deadline):
                    chunk = next(chunks, None)
                if chunk is None:
                    break
                if time.monotonic() > self.deadline:
                    raise TimeoutError("Ingestion response exceeded its time budget")
                count += len(chunk)
                if count > MAX_RESPONSE_BYTES:
                    raise ValueError(
                        "Ingestion response exceeds 16 MiB; narrow the input"
                    )
                _retain(len(chunk))
                yield chunk
        finally:
            self.close()

    def close(self):
        self.stream.close()


class IngestionTransport(httpx.BaseTransport):
    def __init__(self):
        self.transports = {}
        self.lock = Lock()

    def handle_request(self, request):
        deadline = time.monotonic() + MAX_RESPONSE_SECONDS
        addresses = destination(str(request.url))
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode("ascii")
        headers["Accept-Encoding"] = "identity"
        configured = request.extensions.get("timeout", {})
        with budget(deadline):
            # The TCP connection is made before its TLS socket exists.
            timeouts = {
                name: remaining(configured.get(name))
                for name in ("connect", "read", "write", "pool")
            }
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=addresses[0]),
            headers=headers,
            stream=request.stream,
            extensions={
                **request.extensions,
                "sni_hostname": request.url.host,
                "timeout": timeouts,
            },
        )
        # Pools are keyed by the original origin as well as the pinned IP;
        # two HTTPS hosts on one IP must each perform hostname verification.
        origin = (request.url.host, request.url.port)
        with self.lock:
            if origin not in self.transports:
                self.transports[origin] = httpx.HTTPTransport(
                    verify=budgeted(httpx.create_ssl_context(trust_env=False)),
                    trust_env=False,
                )
            transport = self.transports[origin]
        with budget(deadline):
            response = transport.handle_request(pinned)
        # Do not allow an upstream to bypass the byte cap with a compression bomb.
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            response.close()
            raise ValueError("Ingestion server must honour Accept-Encoding: identity")
        response.stream = LimitedStream(response.stream, deadline)
        return response

    def close(self):
        for transport in self.transports.values():
            transport.close()


def client(**kwargs):
    kwargs.update(
        transport=IngestionTransport(),
        trust_env=False,
        follow_redirects=False,
        verify=True,
    )
    return httpx.Client(**kwargs)
