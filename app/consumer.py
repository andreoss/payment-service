import asyncio
import logging
import signal
import uuid

from faststream import FastStream
from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange, RabbitMessage, RabbitQueue

from app.config import settings
from app.payment_processor import process_payment
from app.rabbitmq import RabbitPublisher
from app.rabbitmq_topology import (
    EXCHANGE_DLQ,
    EXCHANGE_MAIN,
    EXCHANGE_RETRY,
    QUEUE_NEW,
    ROUTING_DLQ,
    ROUTING_NEW,
    ROUTING_RETRY,
    declare_topology,
)
from app.retry_policy import decide

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

broker = RabbitBroker(settings.rabbitmq_url, prefetch_count=10)
app = FastStream(broker, title="payment-consumer")

publisher = RabbitPublisher(settings.rabbitmq_url)
_shutdown_event = asyncio.Event()


def _signal_handler(signame: str) -> None:
    logger.info("Received %s, initiating graceful shutdown...", signame)
    _shutdown_event.set()


main_exchange = RabbitExchange(EXCHANGE_MAIN, type=ExchangeType.DIRECT, durable=True)
new_queue = RabbitQueue(QUEUE_NEW, durable=True, routing_key=ROUTING_NEW)


@app.on_startup
async def on_startup() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: _signal_handler(signal.Signals(s).name))

    await declare_topology(settings.rabbitmq_url)
    await publisher.connect()
    await broker.start()
    logger.info("Consumer connected to RabbitMQ, listening on %s", QUEUE_NEW)


@app.on_shutdown
async def on_shutdown() -> None:
    logger.info("Shutting down consumer...")
    await publisher.close()
    await broker.close()
    logger.info("Consumer shut down complete")


# Handler exceptions are caught here rather than propagated so the broker
# always ACKs the original delivery; retry/DLQ routing below is explicit.
# Letting exceptions escape would make FastStream requeue immediately,
# hot-looping a poison message with no backoff and no attempt counting.
@broker.subscriber(new_queue, main_exchange)
async def handle_new_payment(body: dict, message: RabbitMessage) -> None:
    # x-attempt is stamped by the outbox relay (1) and by each retry publish.
    try:
        attempt = int((message.headers or {}).get("x-attempt", 1))
    except (ValueError, TypeError):
        attempt = 1
        logger.warning("Invalid x-attempt header, defaulting to 1: %s", message.headers)

    try:
        payment_id = uuid.UUID(body["payment_id"])
        logger.info("Received payment.new for %s (attempt %s)", payment_id, attempt)
        await process_payment(payment_id)
    except Exception:
        logger.exception("Processing failed for message %s (attempt %s)", body, attempt)

        decision = decide(
            attempt, settings.consumer_max_attempts, settings.consumer_retry_base_delay_ms
        )

        if decision.action == "dlq":
            logger.error(
                "Message exceeded %s attempts, routing to DLQ: %s",
                settings.consumer_max_attempts,
                body,
            )
            await publisher.publish(
                exchange_name=EXCHANGE_DLQ,
                routing_key=ROUTING_DLQ,
                payload=body,
                headers={"x-attempt": attempt, "x-original-routing-key": ROUTING_NEW},
            )
        else:
            logger.warning(
                "Scheduling retry %s/%s for message %s in %sms",
                decision.next_attempt,
                settings.consumer_max_attempts,
                body,
                decision.delay_ms,
            )
            await publisher.publish(
                exchange_name=EXCHANGE_RETRY,
                routing_key=ROUTING_RETRY,
                payload=body,
                headers={"x-attempt": decision.next_attempt},
                expiration_ms=decision.delay_ms,
            )
