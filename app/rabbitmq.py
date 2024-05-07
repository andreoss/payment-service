import json
import logging
from datetime import timedelta
from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractRobustConnection
from aio_pika.exceptions import ChannelClosed, ConnectionClosed

logger = logging.getLogger(__name__)


class RabbitPublisher:
    """Thin aio-pika wrapper used for every outbound publish.

    Runs on a channel with publisher confirms enabled: a resolved publish()
    means the broker accepted the message — the guarantee the outbox relay
    relies on before it marks an event as published. Exchanges are declared
    once per connection and reused, rather than re-declared on every publish.

    On connection/channel closure (e.g. during automatic reconnect), the
    exchange cache is cleared and a new channel is established transparently.
    """

    def __init__(self, url: str):
        self._url = url
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None
        self._exchanges: dict[str, AbstractExchange] = {}

    async def connect(self) -> None:
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel(publisher_confirms=True)
        self._exchanges = {}

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
        self._channel = None
        self._exchanges.clear()

    async def _ensure_connected(self) -> AbstractChannel:
        if self._channel is None or self._channel.is_closed:
            logger.info("RabbitMQ channel closed, reconnecting...")
            if self._connection is not None and not self._connection.is_closed:
                await self._connection.close()
            await self.connect()
        return self._channel

    async def _get_exchange(self, name: str) -> AbstractExchange:
        channel = await self._ensure_connected()

        exchange = self._exchanges.get(name)
        if exchange is None:
            exchange = await channel.declare_exchange(
                name, aio_pika.ExchangeType.DIRECT, durable=True
            )
            self._exchanges[name] = exchange
        return exchange

    def _clear_exchanges(self) -> None:
        self._exchanges.clear()

    async def publish(
        self,
        exchange_name: str,
        routing_key: str,
        payload: dict[str, Any],
        headers: dict[str, Any] | None = None,
        expiration_ms: int | None = None,
    ) -> None:
        exchange = await self._get_exchange(exchange_name)
        message = aio_pika.Message(
            body=json.dumps(payload).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=headers or {},
            expiration=timedelta(milliseconds=expiration_ms) if expiration_ms else None,
        )
        try:
            await exchange.publish(message, routing_key=routing_key)
        except (ChannelClosed, ConnectionClosed):
            self._clear_exchanges()
            raise
