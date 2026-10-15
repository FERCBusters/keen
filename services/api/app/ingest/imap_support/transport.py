"""IMAPS only: pinned destination, verified TLS, pre-allocation response limits."""

import imaplib
import ipaddress
import socket
import ssl
import time

MAX_SESSION_BYTES = 4 * 1024 * 1024
MAX_LITERAL = 1024 * 1024


def addresses(host, port, allowed_cidrs):
    networks = [
        ipaddress.ip_network(n) for n in allowed_cidrs.replace(",", " ").split()
    ]
    values = list(
        dict.fromkeys(
            row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        )
    )
    if not values or len(values) > 16:
        raise ValueError("Invalid IMAP address set")
    for value in values:
        ip = ipaddress.ip_address(value)
        ip = getattr(ip, "ipv4_mapped", None) or ip
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
            or ip.is_reserved
        ):
            raise ValueError("Prohibited IMAP destination")
        if not ip.is_global and not any(ip in net for net in networks):
            raise ValueError(
                "Private IMAP destination requires an explicit allowed CIDR"
            )
    return values


class Client(imaplib.IMAP4_SSL):
    def __init__(self, host, port, allowed_cidrs, deadline):
        self.deadline = deadline
        self.received = 0
        self.lines = 0
        self.literal_limit = 0  # Only an explicit BODY.PEEK request permits a literal.
        self.pinned = addresses(host, port, allowed_cidrs)[0]
        super().__init__(
            host, port, ssl_context=ssl.create_default_context(), timeout=10
        )

    def _create_socket(self, timeout):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("IMAP deadline")
        sock = socket.create_connection(
            (self.pinned, self.port), timeout=min(10, remaining)
        )
        try:
            return self.ssl_context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise

    def budget(self, size):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("IMAP deadline")
        if size < 0 or self.received + size > MAX_SESSION_BYTES:
            raise ValueError("IMAP response budget exceeded")
        self.sock.settimeout(min(15, remaining))

    def read(self, size):
        # imaplib calls this BEFORE allocating a server-declared literal.
        if self.literal_limit <= 0 or size > min(self.literal_limit, MAX_LITERAL):
            raise ValueError("IMAP literal exceeds requested limit")
        self.budget(size)
        # Consume the allowance even for an empty literal; extra literals in
        # this command must be rejected before their payload is read.
        self.literal_limit = 0
        result = self.file.read(size)
        self.received += len(result)
        return result

    def readline(self):
        self.budget(32769)
        self.lines += 1
        if self.lines > 2048:
            raise ValueError("Too many IMAP response lines")
        result = self.file.readline(32769)
        if len(result) > 32768:
            raise ValueError("IMAP response line exceeds limit")
        self.received += len(result)
        return result
