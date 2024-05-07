import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import update

from app import payment_processor
from app.models import Payment, PaymentStatus


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info):
        return False


def _make_session(get_return: Payment | None, execute_return=None) -> MagicMock:
    session = MagicMock()
    session.get = AsyncMock(return_value=get_return)
    session.execute = AsyncMock(return_value=execute_return)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


def _pending_payment() -> Payment:
    return Payment(
        id=uuid.uuid4(),
        idempotency_key="key",
        amount="10.00",
        currency="USD",
        payment_metadata={},
        status=PaymentStatus.PENDING,
        webhook_url="https://example.com/hook",
    )


def _result_mock(rowcount: int) -> MagicMock:
    result = MagicMock()
    result.rowcount = rowcount
    return result


async def test_skips_when_payment_not_found():
    session = _make_session(get_return=None)
    factory = MagicMock(return_value=_FakeSessionContext(session))

    with (
        patch.object(payment_processor, "async_session_factory", factory),
        patch.object(payment_processor, "send_webhook_notification", AsyncMock()) as webhook,
    ):
        await payment_processor.process_payment(uuid.uuid4())

    webhook.assert_not_called()
    factory.assert_called_once()


async def test_skips_when_not_pending():
    payment = _pending_payment()
    payment.status = PaymentStatus.SUCCEEDED
    session = _make_session(get_return=payment)
    factory = MagicMock(return_value=_FakeSessionContext(session))

    with (
        patch.object(payment_processor, "async_session_factory", factory),
        patch.object(payment_processor, "send_webhook_notification", AsyncMock()) as webhook,
    ):
        await payment_processor.process_payment(payment.id)

    webhook.assert_not_called()
    factory.assert_called_once()


async def test_processes_pending_payment_and_sends_webhook_on_success():
    payment = _pending_payment()
    first_session = _make_session(get_return=payment)
    second_session = _make_session(get_return=payment, execute_return=_result_mock(rowcount=1))
    factory = MagicMock(side_effect=[
        _FakeSessionContext(first_session),
        _FakeSessionContext(second_session),
    ])

    with (
        patch.object(payment_processor, "async_session_factory", factory),
        patch.object(payment_processor, "send_webhook_notification", AsyncMock()) as webhook,
        patch.object(payment_processor.asyncio, "sleep", AsyncMock()),
        patch.object(payment_processor.random, "uniform", return_value=0.0),
        patch.object(payment_processor.random, "random", return_value=0.99),
    ):
        await payment_processor.process_payment(payment.id)

    assert payment.status == PaymentStatus.SUCCEEDED
    assert payment.processed_at is not None
    second_session.commit.assert_awaited_once()
    second_session.execute.assert_awaited_once()
    webhook.assert_awaited_once_with(payment)
    assert factory.call_count == 2


async def test_processes_pending_payment_and_marks_failed_on_simulated_gateway_error():
    payment = _pending_payment()
    first_session = _make_session(get_return=payment)
    second_session = _make_session(get_return=payment, execute_return=_result_mock(rowcount=1))
    factory = MagicMock(side_effect=[
        _FakeSessionContext(first_session),
        _FakeSessionContext(second_session),
    ])

    with (
        patch.object(payment_processor, "async_session_factory", factory),
        patch.object(payment_processor, "send_webhook_notification", AsyncMock()) as webhook,
        patch.object(payment_processor.asyncio, "sleep", AsyncMock()),
        patch.object(payment_processor.random, "uniform", return_value=0.0),
        patch.object(payment_processor.random, "random", return_value=0.0),
    ):
        await payment_processor.process_payment(payment.id)

    assert payment.status == PaymentStatus.FAILED
    second_session.execute.assert_awaited_once()
    webhook.assert_awaited_once_with(payment)


async def test_skips_update_when_state_changed_during_simulated_processing():
    payment = _pending_payment()
    first_session = _make_session(get_return=payment)
    already_processed = _pending_payment()
    already_processed.status = PaymentStatus.SUCCEEDED
    second_session = _make_session(get_return=already_processed, execute_return=_result_mock(rowcount=0))
    factory = MagicMock(side_effect=[
        _FakeSessionContext(first_session),
        _FakeSessionContext(second_session),
    ])

    with (
        patch.object(payment_processor, "async_session_factory", factory),
        patch.object(payment_processor, "send_webhook_notification", AsyncMock()) as webhook,
        patch.object(payment_processor.asyncio, "sleep", AsyncMock()),
        patch.object(payment_processor.random, "uniform", return_value=0.0),
        patch.object(payment_processor.random, "random", return_value=0.99),
    ):
        await payment_processor.process_payment(payment.id)

    second_session.execute.assert_awaited_once()
    second_session.commit.assert_not_called()
    webhook.assert_not_called()
