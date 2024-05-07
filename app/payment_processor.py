import asyncio
import logging
import random
import uuid
from datetime import UTC, datetime

from sqlalchemy import update

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
    new_status = PaymentStatus.SUCCEEDED if succeeded else PaymentStatus.FAILED
    processed_at = datetime.now(UTC)

    async with async_session_factory() as session:
        result = await session.execute(
            update(Payment)
            .where(Payment.id == payment_id, Payment.status == PaymentStatus.PENDING)
            .values(status=new_status, processed_at=processed_at)
        )
        if result.rowcount == 0:
            logger.info("Payment %s state changed during processing, skipping update", payment_id)
            return
        await session.commit()

        payment = await session.get(Payment, payment_id)

    await send_webhook_notification(payment)
