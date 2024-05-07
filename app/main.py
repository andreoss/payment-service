import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request

from app.api.payments import router as payments_router
from app.config import settings
from app.outbox_relay import OutboxRelay
from app.rabbitmq import RabbitPublisher
from app.rabbitmq_topology import declare_topology

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await declare_topology(settings.rabbitmq_url)

    publisher = RabbitPublisher(settings.rabbitmq_url)
    await publisher.connect()

    relay = OutboxRelay(publisher)
    relay.start()

    app.state.publisher = publisher
    app.state.relay = relay

    logger.info("Payment service started")
    try:
        yield
    finally:
        await relay.stop()
        await publisher.close()
        logger.info("Payment service stopped")


app = FastAPI(title="Payment Processing Service", version="1.0.0", lifespan=lifespan)
app.include_router(payments_router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok"}


debug_router = APIRouter(prefix="/api/v1/_debug", tags=["debug"])

_received_webhooks: list[dict] = []


@debug_router.post("/webhook-echo")
async def webhook_echo(request: Request) -> dict:
    """Local stand-in webhook receiver, unauthenticated like a real merchant endpoint."""
    payload = await request.json()
    logger.info("webhook-echo received: %s", payload)
    _received_webhooks.append(payload)
    return {"received": True}


@debug_router.get("/webhook-events")
async def webhook_events(payment_id: str | None = None) -> list[dict]:
    """Returns everything webhook-echo has received, for local/test inspection."""
    if payment_id is None:
        return _received_webhooks
    return [event for event in _received_webhooks if event.get("payment_id") == payment_id]


app.include_router(debug_router)
