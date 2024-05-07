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
    """Polls the outbox table and publishes pending events to RabbitMQ.

    Uses SELECT ... FOR UPDATE SKIP LOCKED so multiple replicas can run
    concurrently without double-publishing. A batch commits as one
    transaction: committing per-row instead would release the lock mid-batch
    and let another replica double-publish, so a partial failure republishes
    the whole (small, `outbox_batch_size`) batch instead.
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
