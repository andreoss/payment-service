import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Currency, OutboxEvent, Payment
from app.schemas import PaymentCreateRequest
from app.services import payment_service as payment_service_module
from app.services.payment_service import PaymentService
from app.url_safety import UnsafeWebhookURLError


def _create_request() -> PaymentCreateRequest:
    return PaymentCreateRequest(
        amount="99.99",
        currency="USD",
        description="desc",
        metadata={"k": "v"},
        webhook_url="https://example.com/hook",
    )


def _existing_payment(idempotency_key: str) -> Payment:
    return Payment(
        id=uuid.uuid4(),
        idempotency_key=idempotency_key,
        amount="1.00",
        currency=Currency.USD,
        payment_metadata={},
        webhook_url="https://example.com/hook",
    )


def _mock_session(lookup_result) -> MagicMock:
    session = MagicMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = lookup_result
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


async def test_create_payment_returns_existing_payment_for_known_idempotency_key():
    existing = _existing_payment("key-1")
    session = _mock_session(existing)

    result = await PaymentService(session).create_payment("key-1", _create_request())

    assert result is existing
    session.add.assert_not_called()
    session.commit.assert_not_called()


async def test_create_payment_persists_payment_and_outbox_event_for_new_key():
    session = _mock_session(None)

    with patch.object(payment_service_module, "ensure_webhook_url_is_safe", AsyncMock()):
        payment = await PaymentService(session).create_payment("key-2", _create_request())

    assert session.add.call_count == 2
    added = [call.args[0] for call in session.add.call_args_list]

    assert any(obj is payment for obj in added)

    outbox_events = [obj for obj in added if isinstance(obj, OutboxEvent)]
    assert len(outbox_events) == 1
    assert outbox_events[0].event_type == "payment.created"
    assert outbox_events[0].payload["idempotency_key"] == "key-2"
    assert outbox_events[0].payload["amount"] == str(payment.amount)

    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(payment)


async def test_create_payment_recovers_from_concurrent_insert_race():
    existing = _existing_payment("key-3")

    session = MagicMock(spec=AsyncSession)
    miss = MagicMock()
    miss.scalar_one_or_none.return_value = None
    hit = MagicMock()
    hit.scalar_one_or_none.return_value = existing
    session.execute = AsyncMock(side_effect=[miss, hit])
    session.flush = AsyncMock(side_effect=IntegrityError("insert", {}, Exception("duplicate key")))
    session.rollback = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with patch.object(payment_service_module, "ensure_webhook_url_is_safe", AsyncMock()):
        result = await PaymentService(session).create_payment("key-3", _create_request())

    assert result is existing
    session.rollback.assert_awaited_once()
    session.commit.assert_not_called()


async def test_get_payment_delegates_to_session_get():
    session = MagicMock(spec=AsyncSession)
    payment = _existing_payment("key-4")
    session.get = AsyncMock(return_value=payment)

    result = await PaymentService(session).get_payment(payment.id)

    assert result is payment
    session.get.assert_awaited_once_with(Payment, payment.id)


async def test_create_payment_rejects_unsafe_webhook_url_and_does_not_persist():
    session = _mock_session(None)
    request = PaymentCreateRequest(
        amount="10.00", currency="USD", webhook_url="http://127.0.0.1/hook"
    )

    try:
        await PaymentService(session).create_payment("key-5", request)
        raise AssertionError("expected UnsafeWebhookURLError")
    except UnsafeWebhookURLError:
        pass

    session.add.assert_not_called()
    session.commit.assert_not_called()
