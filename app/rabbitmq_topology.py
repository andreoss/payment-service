import aio_pika

EXCHANGE_MAIN = "payments.direct"
EXCHANGE_RETRY = "payments.retry"
EXCHANGE_DLQ = "payments.dlq"

QUEUE_NEW = "payments.new"
QUEUE_RETRY = "payments.new.retry"
QUEUE_DLQ = "payments.new.dlq"

ROUTING_NEW = "payment.new"
ROUTING_RETRY = "payment.new.retry"
ROUTING_DLQ = "payment.new.dlq"


async def declare_topology(url: str) -> None:
    """Idempotently declares the routing topology.

    Layout: payments.new consumes fresh messages; a failed message is
    republished to payments.new.retry — a per-message-TTL queue whose
    dead-letter exchange routes expired messages back to payments.new,
    giving delayed retries without blocking the main queue. After the last
    attempt the message is published to the DLQ exchange for inspection.
    """
    connection = await aio_pika.connect_robust(url)
    try:
        channel = await connection.channel()

        main_exchange = await channel.declare_exchange(
            EXCHANGE_MAIN, aio_pika.ExchangeType.DIRECT, durable=True
        )
        retry_exchange = await channel.declare_exchange(
            EXCHANGE_RETRY, aio_pika.ExchangeType.DIRECT, durable=True
        )
        dlq_exchange = await channel.declare_exchange(
            EXCHANGE_DLQ, aio_pika.ExchangeType.DIRECT, durable=True
        )

        new_queue = await channel.declare_queue(
            QUEUE_NEW,
            durable=True,
            arguments={
                "x-dead-letter-exchange": EXCHANGE_DLQ,
                "x-dead-letter-routing-key": ROUTING_DLQ,
            },
        )
        await new_queue.bind(main_exchange, routing_key=ROUTING_NEW)

        retry_queue = await channel.declare_queue(
            QUEUE_RETRY,
            durable=True,
            arguments={
                "x-dead-letter-exchange": EXCHANGE_MAIN,
                "x-dead-letter-routing-key": ROUTING_NEW,
            },
        )
        await retry_queue.bind(retry_exchange, routing_key=ROUTING_RETRY)

        dlq_queue = await channel.declare_queue(QUEUE_DLQ, durable=True)
        await dlq_queue.bind(dlq_exchange, routing_key=ROUTING_DLQ)
    finally:
        await connection.close()
