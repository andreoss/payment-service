import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import settings
from app.db import async_session_factory
from app.models import OutboxEvent
from app.rabbitmq import RabbitPublisher
from app.rabbitmq_topology import EXCHANGE_MAIN, ROUTING_NEW

logger = logging.getLogger(__name__)


class OutboxRelay:
    """Polls the outbox table and reliably publishes pending events to RabbitMQ.

    Runs as a background asyncio task inside the API process. Uses
    SELECT ... FOR UPDATE SKIP LOCKED so multiple replicas could run this
    concurrently without double-publishing. A row is only marked published
    after the broker publish call returns (publisher confirms are enabled),
    so a crash between publish and commit can cause an at-most-once extra
    redelivery, which the consumer handles idempotently.

    A whole batch is committed as one transaction. If publishing fails partway
    through a batch, every row in it - including ones already published to
    RabbitMQ earlier in the same loop iteration - stays unmarked and gets
    republished on the next tick. This is deliberate: committing per-row would
    release the FOR UPDATE lock on the rest of the batch after the first row,
    letting a second relay replica pick up rows this instance already has in
    memory, reintroducing the double-publish race SKIP LOCKED exists to
    prevent. The batch is kept small (`outbox_batch_size`) to bound how much
    redundant (but harmless, since processing is idempotent) republishing a
    partial failure can cause.
    """

    def __init__(self, publisher: RabbitPublisher):
        self._publisher = publisher
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="outbox-relay")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            await self._task

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                published = await self._relay_batch()
            except Exception:
                logger.exception("Outbox relay iteration failed")
                published = 0

            if published == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=settings.outbox_poll_interval
                    )

    async def _relay_batch(self) -> int:
        async with async_session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.created_at)
                    .limit(settings.outbox_batch_size)
                    .with_for_update(skip_locked=True)
                )
                events = list(result.scalars())
                if not events:
                    return 0

                for event in events:
                    await self._publisher.publish(
                        exchange_name=EXCHANGE_MAIN,
                        routing_key=ROUTING_NEW,
                        payload=event.payload,
                        headers={"x-attempt": 1, "event-type": event.event_type},
                    )
                    event.published_at = datetime.now(UTC)
                    logger.info("Published outbox event %s (%s)", event.id, event.event_type)

            return len(events)
