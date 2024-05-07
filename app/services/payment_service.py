import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxEvent, Payment
from app.schemas import PaymentCreateRequest


class PaymentService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_payment(self, idempotency_key: str, data: PaymentCreateRequest) -> Payment:
        existing = await self._get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing

        payment = Payment(
            idempotency_key=idempotency_key,
            amount=data.amount,
            currency=data.currency,
            description=data.description,
            payment_metadata=data.metadata,
            webhook_url=str(data.webhook_url),
        )
        self.session.add(payment)

        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            existing = await self._get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing
            raise

        outbox_event = OutboxEvent(
            aggregate_id=payment.id,
            event_type="payment.created",
            payload={
                "payment_id": str(payment.id),
                "idempotency_key": payment.idempotency_key,
                "amount": str(payment.amount),
                "currency": payment.currency.value,
            },
        )
        self.session.add(outbox_event)

        await self.session.commit()
        await self.session.refresh(payment)
        return payment

    async def get_payment(self, payment_id: uuid.UUID) -> Payment | None:
        return await self.session.get(Payment, payment_id)

    async def _get_by_idempotency_key(self, idempotency_key: str) -> Payment | None:
        result = await self.session.execute(
            select(Payment).where(Payment.idempotency_key == idempotency_key)
        )
        return result.scalar_one_or_none()
