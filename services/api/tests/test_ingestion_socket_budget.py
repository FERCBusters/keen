"""The built-in collectors' request budget at the TLS socket and at resolution.

A blocking TLS operation waits at most for what remains of the request's
budget, whichever TLS call starts it; a non-blocking socket never waits, so its
caller keeps its own timeout. A destination name that resolves to no address is
refused before any connection. The sockets here are local socket pairs with no
peer speaking TLS, so nothing leaves the test process.
"""

import socket
import ssl
import time

import pytest
from app.core.config import settings
from app.ingest import common, http


@pytest.fixture
def pair():
    near, far = socket.socketpair()
    yield near, far
    far.close()


@pytest.fixture
def peer(pair):
    return pair[1]


@pytest.fixture
def tls_socket(pair):
    context = http.budgeted(ssl.create_default_context())
    wrapped = context.wrap_socket(
        pair[0], server_hostname="feed.example", do_handshake_on_connect=False
    )
    yield wrapped
    wrapped.close()


def test_the_budgeted_context_keeps_certificate_and_hostname_checks(tls_socket):
    assert isinstance(tls_socket, http.BudgetedSocket)
    assert tls_socket.context.verify_mode == ssl.CERT_REQUIRED
    assert tls_socket.context.check_hostname is True


def test_a_blocking_tls_write_after_the_budget_ends_is_refused(tls_socket):
    # The socket's own timeout is far longer than what the budget leaves.
    tls_socket.settimeout(1.0)
    with http.budget(time.monotonic() - 1):
        with pytest.raises(TimeoutError, match="Request exceeded its time budget"):
            tls_socket.write(b"GET / HTTP/1.1\r\n")
    assert tls_socket.gettimeout() == 1.0


def test_a_blocking_tls_write_within_the_budget_waits_only_what_remains(
    tls_socket, peer
):
    tls_socket.settimeout(30.0)
    peer.close()  # The peer goes away, so the write fails at once.
    with http.budget(time.monotonic() + 2):
        with pytest.raises(BrokenPipeError):
            tls_socket.write(b"GET / HTTP/1.1\r\n")
    # The write ran with its timeout clamped to the budget's remainder.
    assert 0 < tls_socket.gettimeout() <= 2


def test_a_non_blocking_tls_socket_keeps_its_own_timeout(tls_socket):
    tls_socket.setblocking(False)
    with http.budget(time.monotonic() - 1):
        # The handshake waits for nobody: it reports at once that the peer
        # has yet to answer, and the spent budget leaves it alone.
        with pytest.raises(ssl.SSLWantReadError):
            tls_socket.do_handshake()
    assert tls_socket.gettimeout() == 0.0


def test_a_destination_that_resolves_to_no_address_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "ingestion_allowed_cidrs", "")
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *a, **k: [])

    with pytest.raises(ValueError, match="Ingestion destination has no addresses"):
        http.destination("https://feed.example/rss")
    assert common.is_safe_url("https://feed.example/rss") is False
