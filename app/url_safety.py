import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from app.config import settings


class UnsafeWebhookURLError(ValueError):
    pass


def _is_disallowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def ensure_webhook_url_is_safe(url: str) -> None:
    """Rejects webhook URLs that would make this service issue a
    server-side request to internal/private infrastructure (SSRF)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeWebhookURLError(f"webhook_url scheme must be http or https, got {parts.scheme!r}")
    if not parts.hostname:
        raise UnsafeWebhookURLError("webhook_url is missing a hostname")

    if settings.webhook_allow_private_hosts:
        return

    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            parts.hostname, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise UnsafeWebhookURLError(f"webhook_url host could not be resolved: {parts.hostname}") from exc

    for *_rest, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if _is_disallowed(ip):
            raise UnsafeWebhookURLError(f"webhook_url resolves to a non-public address: {ip}")
