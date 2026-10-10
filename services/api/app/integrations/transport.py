from .errors import IntegrationError

"""HTTPS with validated/pinned DNS, no redirects/proxies, bounded responses."""
import base64
import http.client
import ipaddress
import json
import os
import socket
import ssl
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

from cryptography.fernet import Fernet

MAX_BYTES = 4 * 1024 * 1024


def cipher():
    key = os.environ.get("KEEN_INTEGRATION_SECRET_KEY", "")
    if not key:
        raise IntegrationError(
            "Set KEEN_INTEGRATION_SECRET_KEY on API and worker before storing credentials"
        )
    return Fernet(key.encode())


def encrypt(value):
    return cipher().encrypt(json.dumps(value).encode()).decode()


def decrypt(value):
    return json.loads(cipher().decrypt(value.encode())) if value else {}


def destination(url):
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.fragment:
        raise IntegrationError(
            "Use an HTTPS URL without embedded credentials or fragment"
        )
    allowed = os.environ.get("KEEN_INTEGRATION_ALLOWED_HOSTS", "").split(",")
    if u.hostname.lower() not in {v.strip().lower() for v in allowed if v.strip()}:
        raise IntegrationError("Destination is not in KEEN_INTEGRATION_ALLOWED_HOSTS")
    networks = [
        ipaddress.ip_network(v.strip())
        for v in os.environ.get("KEEN_INTEGRATION_PRIVATE_CIDRS", "").split(",")
        if v.strip()
    ]
    addresses = list(
        dict.fromkeys(
            x[4][0]
            for x in socket.getaddrinfo(
                u.hostname, u.port or 443, type=socket.SOCK_STREAM
            )
        )
    )
    if not addresses:
        raise IntegrationError("Destination has no addresses")
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
            raise IntegrationError("Destination address is prohibited")
        if not ip.is_global and not any(ip in n for n in networks):
            raise IntegrationError("Private destination requires an approved CIDR")
    return u, addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname, port, address, timeout):
        super().__init__(
            hostname, port, timeout=timeout, context=ssl.create_default_context()
        )
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def request(
    url,
    *,
    method="GET",
    query=None,
    body=None,
    headers=None,
    auth=None,
    timeout=20,
    form=None,
):
    u, addresses = destination(url)
    pairs = parse_qsl(u.query, keep_blank_values=True) + list((query or {}).items())
    hdr = {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        **(headers or {}),
    }
    auth = auth or {}
    if auth.get("kind") == "bearer":
        hdr["Authorization"] = "Bearer " + auth["secret"]
    elif auth.get("kind") == "basic":
        hdr["Authorization"] = (
            "Basic "
            + base64.b64encode(
                (auth.get("username", "") + ":" + auth["secret"]).encode()
            ).decode()
        )
    elif auth.get("kind") == "header":
        hdr[auth.get("name", "X-API-Key")] = auth["secret"]
    elif auth.get("kind") == "query":
        pairs.append((auth.get("name", "api_key"), auth["secret"]))
    payload = json.dumps(body).encode() if body is not None else None
    if form is not None:
        payload = urlencode(form).encode()
    if payload is not None:
        hdr["Content-Type"] = (
            "application/x-www-form-urlencoded"
            if form is not None
            else "application/json"
        )
    deadline = time.monotonic() + timeout
    conn = PinnedHTTPS(u.hostname, u.port or 443, addresses[0], timeout)
    try:
        conn.request(
            method,
            (u.path or "/") + ("?" + urlencode(pairs) if pairs else ""),
            payload,
            hdr,
        )
        response = conn.getresponse()
        if response.status != 200:
            # Never include remote bodies/URLs or credentials in exceptions.
            raise IntegrationError(
                f"HTTP {response.status}: check credentials, endpoint and service availability"
            )
        if response.getheader("Content-Encoding", "identity") != "identity":
            raise IntegrationError("Compressed responses are not supported")
        data = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("API response exceeded its time budget")
            if conn.sock is not None:
                conn.sock.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise IntegrationError("Response exceeds 4 MiB; reduce page size")
        return json.loads(data), dict(response.getheaders())
    finally:
        conn.close()


def connection_auth(connection):
    saved = decrypt(connection.encrypted_secret)
    auth = {
        "kind": connection.auth_kind,
        "name": connection.auth_name,
        "username": connection.username,
        **saved,
    }
    if connection.auth_kind == "oauth_client_credentials":
        options = connection.auth_options or {}
        form = {"grant_type": "client_credentials"}
        if options.get("scope"):
            form["scope"] = options["scope"]
        response, _ = request(
            connection.base_url.rstrip("/") + options.get("token_path", "/oauth/token"),
            method="POST",
            form=form,
            auth={"kind": "basic", "username": connection.username, **saved},
        )
        if (
            not isinstance(response, dict)
            or not response.get("access_token")
            or str(response.get("token_type", "Bearer")).lower() != "bearer"
        ):
            raise IntegrationError(
                "OAuth endpoint did not return a bearer access token"
            )
        auth = {"kind": "bearer", "secret": response["access_token"]}
    return auth, saved.get("secret")
