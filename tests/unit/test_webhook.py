import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import respx

from app.models import Currency, PaymentStatus
from app.webhook import send_webhook_notification

WEBHOOK_URL = "https://merchant.example.com/hook"


def _fake_payment(**overrides) -> SimpleNamespace:
    payment = SimpleNamespace(
        id=uuid.uuid4(),
        status=PaymentStatus.SUCCEEDED,
        amount=Decimal("10.00"),
        currency=Currency.USD,
        processed_at=datetime.now(timezone.utc),
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

    # Must not raise: a dead webhook must never affect the payment record.
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
