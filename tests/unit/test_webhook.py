import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from app import webhook as webhook_module
from app.models import Currency, PaymentStatus
from app.url_safety import UnsafeWebhookURLError
from app.webhook import send_webhook_notification

WEBHOOK_URL = "https://merchant.example.com/hook"


@pytest.fixture(autouse=True)
def _bypass_ssrf_check():
    with patch.object(webhook_module, "ensure_webhook_url_is_safe", AsyncMock()):
        yield


def _fake_payment(**overrides) -> SimpleNamespace:
    payment = SimpleNamespace(
        id=uuid.uuid4(),
        status=PaymentStatus.SUCCEEDED,
        amount=Decimal("10.00"),
        currency=Currency.USD,
        processed_at=datetime.now(UTC),
        webhook_url=WEBHOOK_URL,
    )
    for key, value in overrides.items():
        setattr(payment, key, value)
    return payment


@respx.mock
async def test_delivers_successfully_on_first_try():
    route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))

    await send_webhook_notification(_fake_payment())

    assert route.call_count == 1


@respx.mock
async def test_retries_on_server_error_then_succeeds():
    route = respx.post(WEBHOOK_URL).mock(
        side_effect=[httpx.Response(500), httpx.Response(502), httpx.Response(200)]
    )

    await send_webhook_notification(_fake_payment())

    assert route.call_count == 3


@respx.mock
async def test_gives_up_after_max_attempts_without_raising():
    route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(503))

    await send_webhook_notification(_fake_payment())

    assert route.call_count == 3


@respx.mock
async def test_does_not_retry_on_client_error():
    route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(400))

    await send_webhook_notification(_fake_payment())

    assert route.call_count == 1


@respx.mock
async def test_retries_on_network_error():
    route = respx.post(WEBHOOK_URL).mock(side_effect=httpx.ConnectError("boom"))

    await send_webhook_notification(_fake_payment())

    assert route.call_count == 3


@respx.mock
async def test_skips_delivery_when_url_is_unsafe():
    route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))

    with patch.object(
        webhook_module,
        "ensure_webhook_url_is_safe",
        AsyncMock(side_effect=UnsafeWebhookURLError("x")),
    ):
        await send_webhook_notification(_fake_payment())

    assert route.call_count == 0
