"""Canonical client address, accepting forwarding only through trusted proxies."""
import ipaddress
from app.core.config import settings

def normalise_ip(value):
    try:
        ip = ipaddress.ip_address(value)
        return str(ip.ipv4_mapped or ip) if isinstance(ip, ipaddress.IPv6Address) else str(ip)
    except (ValueError, TypeError):
        return None


def request_ip(request):
    if request is None:
        return None
    peer = normalise_ip(getattr(request.client, 'host', '') or '')
    if not peer:
        return None
    networks = [ipaddress.ip_network(x.strip()) for x in settings.security_trusted_proxy_cidrs.split(',') if x.strip()]
    def trusted(value):
        return any(ipaddress.ip_address(value) in network for network in networks)
    if not trusted(peer):
        return peer
    chain = request.headers.get('x-forwarded-for', '')
    if not chain or len(chain) > 2048:
        return peer
    addresses = [normalise_ip(x.strip()) for x in chain.split(',')]
    if not all(addresses):
        return peer
    current = peer
    for address in reversed(addresses):
        if not trusted(current):
            break
        current = address
    return current

