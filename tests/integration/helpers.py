import json
import os

import aio_pika
import httpx

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("API_KEY", "changeme")

RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
RABBITMQ_MGMT_URL = os.environ.get("RABBITMQ_MGMT_URL", "http://localhost:15672")
RABBITMQ_MGMT_USER = os.environ.get("RABBITMQ_MGMT_USER", "guest")
RABBITMQ_MGMT_PASSWORD = os.environ.get("RABBITMQ_MGMT_PASSWORD", "guest")


def webhook_echo_url() -> str:
    return f"{API_BASE_URL}/api/v1/_debug/webhook-echo"


def sample_payload(**overrides) -> dict:
    payload = {
        "amount": "150.00",
        "currency": "RUB",
        "description": "integration test",
        "metadata": {"source": "pytest"},
        "webhook_url": webhook_echo_url(),
    }
    payload.update(overrides)
    return payload


async def wait_for_terminal_status(api_client: httpx.AsyncClient, payment_id: str, timeout: float = 15.0) -> dict:
    import asyncio

    interval = 0.5
    elapsed = 0.0
    while elapsed < timeout:
        resp = await api_client.get(f"/api/v1/payments/{payment_id}")
        resp.raise_for_status()
        data = resp.json()
        if data["status"] != "pending":
            return data
        await asyncio.sleep(interval)
        elapsed += interval
    raise AssertionError(f"payment {payment_id} did not leave pending state within {timeout}s")


async def get_queue_message_count(queue_name: str) -> int:
    async with httpx.AsyncClient(auth=(RABBITMQ_MGMT_USER, RABBITMQ_MGMT_PASSWORD), timeout=10.0) as client:
        resp = await client.get(f"{RABBITMQ_MGMT_URL}/api/queues/%2F/{queue_name}")
        resp.raise_for_status()
        return resp.json()["messages"]


async def publish_raw_message(
    exchange_name: str, routing_key: str, payload: dict, headers: dict | None = None
) -> None:
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    try:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(exchange_name, aio_pika.ExchangeType.DIRECT, durable=True)
        message = aio_pika.Message(
            body=json.dumps(payload).encode("utf-8"),
            headers=headers or {},
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await exchange.publish(message, routing_key=routing_key)
    finally:
        await connection.close()
