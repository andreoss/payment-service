import asyncio
import uuid

from app.rabbitmq_topology import EXCHANGE_MAIN, QUEUE_DLQ, ROUTING_NEW

from .helpers import get_queue_message_count, publish_raw_message


async def test_poison_message_retries_then_lands_in_dlq():
    before = await get_queue_message_count(QUEUE_DLQ)

    await publish_raw_message(
        EXCHANGE_MAIN,
        ROUTING_NEW,
        payload={"payment_id": f"not-a-valid-uuid-{uuid.uuid4().hex}"},
        headers={"x-attempt": 1},
    )

    # 3 attempts with 2s/4s backoff between them: allow generous headroom.
    deadline = 25.0
    elapsed = 0.0
    after = before
    while elapsed < deadline:
        await asyncio.sleep(1.0)
        elapsed += 1.0
        after = await get_queue_message_count(QUEUE_DLQ)
        if after > before:
            break

    assert after == before + 1, f"expected exactly one new DLQ message, before={before} after={after}"
