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
    """Idempotently declares the full payments exchange/queue topology.

    payments.new -> (on failure) payments.new.retry (per-message TTL) -> dead-lettered
    back into payments.new for redelivery -> after max attempts, routed to payments.new.dlq.
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

        new_queue = await channel.declare_queue(QUEUE_NEW, durable=True)
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
