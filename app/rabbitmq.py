import json
from datetime import timedelta
from typing import Any

import aio_pika


class RabbitPublisher:
    """Thin wrapper around a robust aio-pika channel with publisher confirms.

    Used for every outbound publish in the system (outbox relay, consumer
    retry/DLQ routing) so delivery can be confirmed before the caller commits
    to having "sent" a message.
    """

    def __init__(self, url: str):
        self._url = url
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractRobustChannel | None = None

    async def connect(self) -> None:
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel(publisher_confirms=True)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()

    async def publish(
        self,
        exchange_name: str,
        routing_key: str,
        payload: dict[str, Any],
        headers: dict[str, Any] | None = None,
        expiration_ms: int | None = None,
    ) -> None:
        assert self._channel is not None, "RabbitPublisher.connect() must be called first"

        exchange = await self._channel.declare_exchange(
            exchange_name, aio_pika.ExchangeType.DIRECT, durable=True
        )
        message = aio_pika.Message(
            body=json.dumps(payload).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=headers or {},
            expiration=timedelta(milliseconds=expiration_ms) if expiration_ms else None,
        )
        await exchange.publish(message, routing_key=routing_key)
