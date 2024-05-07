import asyncio
import logging
import random
import uuid
from datetime import UTC, datetime

from app.config import settings
from app.db import async_session_factory
from app.models import Payment, PaymentStatus
from app.webhook import send_webhook_notification

logger = logging.getLogger(__name__)


async def process_payment(payment_id: uuid.UUID) -> None:
    """Emulates a gateway call and notifies via webhook; idempotent, skips
    if the payment already left `pending` (e.g. a redelivered message) so
    the gateway is never charged twice."""
    async with async_session_factory() as session:
        payment = await session.get(Payment, payment_id)
        if payment is None:
            logger.warning("Payment %s not found, skipping", payment_id)
            return
        if payment.status != PaymentStatus.PENDING:
            logger.info(
                "Payment %s already processed (status=%s), skipping", payment_id, payment.status
            )
            return

    delay = random.uniform(
        settings.payment_min_processing_seconds, settings.payment_max_processing_seconds
    )
    await asyncio.sleep(delay)
    succeeded = random.random() >= settings.payment_failure_rate

    async with async_session_factory() as session:
        payment = await session.get(Payment, payment_id)
        if payment is None or payment.status != PaymentStatus.PENDING:
            logger.info("Payment %s state changed during processing, skipping update", payment_id)
            return

        payment.status = PaymentStatus.SUCCEEDED if succeeded else PaymentStatus.FAILED
        payment.processed_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(payment)

    await send_webhook_notification(payment)
