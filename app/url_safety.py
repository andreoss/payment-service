"""SSRF protection for client-supplied webhook URLs.

Resolution-based rather than string-based: the hostname is resolved via DNS
and every returned address is checked against non-routable ranges (loopback,
RFC1918, link-local including cloud metadata endpoints, multicast, reserved).
To mitigate DNS rebinding, the first allowed IP is returned and used for the
connection while preserving the original hostname for SNI/Host header.
"""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.config import settings


class UnsafeWebhookURLError(ValueError):
    pass


@dataclass(frozen=True)
class VettedWebhookURL:
    original_url: str
    vetted_ip: str
    port: int
    scheme: str
    hostname: str


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


async def ensure_webhook_url_is_safe(url: str) -> VettedWebhookURL:
    """Rejects webhook URLs that would make this service issue a
    server-side request to internal/private infrastructure (SSRF).

    Returns a VettedWebhookURL containing the first allowed IP address
    to connect to, preserving the original hostname for TLS SNI and Host header.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeWebhookURLError(
            f"webhook_url scheme must be http or https, got {parts.scheme!r}"
        )
    if not parts.hostname:
        raise UnsafeWebhookURLError("webhook_url is missing a hostname")

    if settings.webhook_allow_private_hosts:
        return VettedWebhookURL(
            original_url=url,
            vetted_ip="",
            port=parts.port or (443 if parts.scheme == "https" else 80),
            scheme=parts.scheme,
            hostname=parts.hostname,
        )

    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            parts.hostname, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise UnsafeWebhookURLError(
            f"webhook_url host could not be resolved: {parts.hostname}"
        ) from exc

    vetted_ip = ""
    for *_rest, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if _is_disallowed(ip):
            raise UnsafeWebhookURLError(f"webhook_url resolves to a non-public address: {ip}")
        if not vetted_ip:
            vetted_ip = str(ip)

    if not vetted_ip:
        raise UnsafeWebhookURLError(f"webhook_url resolved to no addresses: {parts.hostname}")

    return VettedWebhookURL(
        original_url=url,
        vetted_ip=vetted_ip,
        port=port,
        scheme=parts.scheme,
        hostname=parts.hostname,
    )
