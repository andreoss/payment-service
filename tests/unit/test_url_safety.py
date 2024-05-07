from unittest.mock import patch

import pytest

from app.config import settings
from app.url_safety import UnsafeWebhookURLError, ensure_webhook_url_is_safe


async def test_rejects_non_http_scheme():
    with pytest.raises(UnsafeWebhookURLError):
        await ensure_webhook_url_is_safe("ftp://example.com/hook")


async def test_rejects_loopback_ip_literal():
    with pytest.raises(UnsafeWebhookURLError):
        await ensure_webhook_url_is_safe("http://127.0.0.1/hook")


async def test_rejects_link_local_metadata_ip_literal():
    with pytest.raises(UnsafeWebhookURLError):
        await ensure_webhook_url_is_safe("http://169.254.169.254/latest/meta-data/")


async def test_rejects_private_ip_literal():
    with pytest.raises(UnsafeWebhookURLError):
        await ensure_webhook_url_is_safe("http://10.0.0.5/hook")


async def test_rejects_unresolvable_host():
    with pytest.raises(UnsafeWebhookURLError):
        await ensure_webhook_url_is_safe("https://this-host-should-not-exist.invalid/hook")


async def test_allows_public_ip_literal():
    await ensure_webhook_url_is_safe("http://8.8.8.8/hook")


async def test_allows_private_host_when_explicitly_enabled():
    with patch.object(settings, "webhook_allow_private_hosts", True):
        await ensure_webhook_url_is_safe("http://127.0.0.1/hook")
