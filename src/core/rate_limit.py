"""The single shared rate limiter, keyed on the real client IP.

Forwarding headers are only believed when the request comes from a proxy listed in
TRUSTED_PROXY_IPS. Otherwise anyone could send `X-Forwarded-For: <random>` and get a
fresh rate-limit bucket on every request.
"""

from ipaddress import IPv4Network, IPv6Network, ip_address

from slowapi import Limiter
from starlette.requests import Request

from src.core.config import settings

CONTACT_LIMIT = "5/minute"
LOGIN_LIMIT = "5/minute"
CONTEXT_LIMIT = "30/minute"


def _parse_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        return None


def _is_trusted(ip: str, networks: list[IPv4Network | IPv6Network]) -> bool:
    address = ip_address(ip)
    return any(address in network for network in networks)


def client_ip(request: Request) -> str:
    """The address of the visitor, as best it can be trusted."""
    peer_host = request.client.host if request.client else None
    peer = _parse_ip(peer_host)
    if peer is None:
        return peer_host or "unknown"

    networks = settings.trusted_proxy_ips
    if not networks or not _is_trusted(peer, networks):
        return peer

    if settings.client_ip_header == "cf-connecting-ip":
        return _parse_ip(request.headers.get("cf-connecting-ip")) or peer

    # X-Forwarded-For is "client, proxy1, proxy2"; each proxy appends the address it saw.
    # Walk from the right past our own trusted proxies: the first untrusted hop is the
    # client. Anything further left was written by the client and can be forged.
    forwarded = request.headers.get("x-forwarded-for", "")
    for hop in reversed(forwarded.split(",")):
        hop_ip = _parse_ip(hop)
        if hop_ip is None:
            break
        if not _is_trusted(hop_ip, networks):
            return hop_ip
    return peer


limiter = Limiter(key_func=client_ip, storage_uri=settings.rate_limit_storage_uri)
