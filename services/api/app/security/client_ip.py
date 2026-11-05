"""Canonical client address, accepting forwarding only through trusted proxies."""

import ipaddress
import logging
import threading

from app.core.config import settings

log = logging.getLogger(__name__)
_untrusted_proxy_reported = threading.Event()
_report_lock = threading.Lock()


def normalise_ip(value):
    try:
        ip = ipaddress.ip_address(value)
        return (
            str(ip.ipv4_mapped or ip)
            if isinstance(ip, ipaddress.IPv6Address)
            else str(ip)
        )
    except (ValueError, TypeError):
        return None


def _report_untrusted_proxy(peer):
    """Tell the operator, once per process, that a proxy is not trusted."""
    if _untrusted_proxy_reported.is_set():
        return
    with _report_lock:
        if _untrusted_proxy_reported.is_set():
            return
        _untrusted_proxy_reported.set()
    # The peer is the socket address; forwarded header values are never logged.
    log.warning(
        "Requests from %s carry X-Forwarded-For, but "
        "KEEN_SECURITY_TRUSTED_PROXY_CIDRS is empty, so every client behind that "
        "proxy shares its address in the audit trail and per-address rate limits. "
        "Set KEEN_SECURITY_TRUSTED_PROXY_CIDRS to the proxy's address or network.",
        peer,
    )


def request_ip(request):
    peer = normalise_ip(getattr(request.client, "host", "") or "")
    if not peer:
        return None
    networks = [
        ipaddress.ip_network(x.strip())
        for x in settings.security_trusted_proxy_cidrs.split(",")
        if x.strip()
    ]

    def trusted(value):
        return any(ipaddress.ip_address(value) in network for network in networks)

    if not trusted(peer):
        if (
            not networks
            and "x-forwarded-for" in request.headers
            and ipaddress.ip_address(peer).is_private
        ):
            _report_untrusted_proxy(peer)
        return peer
    # Repeated field lines form one list in order (RFC 9110 §5.3); a proxy that
    # appends its own line after the client's must still be the nearest hop.
    chain = ",".join(request.headers.getlist("x-forwarded-for"))
    current = peer
    # Walk back from the nearest hop while each hop is a trusted proxy. Entries
    # beyond the first untrusted hop are client-supplied, so their length or
    # format cannot change the result; an unparseable hop ends the walk.
    for raw in reversed(chain.split(",")[-64:] if chain else []):
        if not trusted(current):
            break
        address = normalise_ip(raw.strip()[:64])
        if not address:
            break
        current = address
    return current
