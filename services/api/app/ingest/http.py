"""Shared HTTP boundary for built-in collectors.

Resolve and validate immediately before connecting, pin the selected address,
and retain the original hostname for TLS verification and the Host header.
"""
import ipaddress
import socket
from threading import Lock
from urllib.parse import urlsplit

import httpx

from app.core.config import settings

MAX_RESPONSE_BYTES = 16 * 1024 * 1024


def destination(url):
    parsed = urlsplit(str(url))
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or '\\' in str(url)
            or any(ord(c) < 32 for c in str(url))):
        raise ValueError('Use an HTTPS destination without embedded credentials or fragment')
    port = parsed.port or 443
    # A malformed restriction must never silently turn into unrestricted access.
    networks = [ipaddress.ip_network(v, strict=False) for v in
                settings.ingestion_allowed_cidrs.replace(',', ' ').split()]
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(
        parsed.hostname, port, type=socket.SOCK_STREAM)))
    if not addresses:
        raise ValueError('Ingestion destination has no addresses')
    for address in addresses:
        ip = ipaddress.ip_address(address)
        ip = getattr(ip, 'ipv4_mapped', None) or ip
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise ValueError('Ingestion destination address is prohibited')
        if networks and not ip.is_global and not any(ip in n for n in networks):
            raise ValueError('Ingestion destination is outside the approved networks')
    return addresses


def require_origin(url, endpoint):
    def origin(value):
        u = urlsplit(value)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.fragment:
            raise ValueError('Use an HTTPS feed and connection endpoint')
        return u.scheme, u.hostname.lower(), u.port or 443
    if origin(url) != origin(endpoint):
        raise ValueError('Feed credentials may only be sent to the connection endpoint origin')


class LimitedStream(httpx.SyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    def __iter__(self):
        count = 0
        try:
            for chunk in self.stream:
                count += len(chunk)
                if count > MAX_RESPONSE_BYTES:
                    raise ValueError('Ingestion response exceeds 16 MiB; narrow the input')
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
        addresses = destination(str(request.url))
        headers = request.headers.copy()
        headers['Host'] = request.url.netloc.decode('ascii')
        headers['Accept-Encoding'] = 'identity'
        pinned = httpx.Request(request.method, request.url.copy_with(host=addresses[0]),
            headers=headers, stream=request.stream,
            extensions={**request.extensions, 'sni_hostname': request.url.host})
        # Pools are keyed by the original origin as well as the pinned IP;
        # two HTTPS hosts on one IP must each perform hostname verification.
        origin = (request.url.host, request.url.port)
        with self.lock:
            if origin not in self.transports:
                self.transports[origin] = httpx.HTTPTransport(verify=True, trust_env=False)
            transport = self.transports[origin]
        response = transport.handle_request(pinned)
        # Do not allow an upstream to bypass the byte cap with a compression bomb.
        if response.headers.get('content-encoding', 'identity').lower() != 'identity':
            response.close()
            raise ValueError('Ingestion server must honour Accept-Encoding: identity')
        response.stream = LimitedStream(response.stream)
        return response

    def close(self):
        for transport in self.transports.values():
            transport.close()


def client(**kwargs):
    kwargs.update(transport=IngestionTransport(), trust_env=False, follow_redirects=False, verify=True)
    return httpx.Client(**kwargs)
