import contextvars
import logging
import uuid
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.payments import router as payments_router
from app.config import settings
from app.db import async_session_factory, engine
from app.outbox_relay import OutboxRelay
from app.rabbitmq import RabbitPublisher
from app.rabbitmq_topology import declare_topology


correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record):
        record.correlation_id = correlation_id_var.get()
        return True


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] [corr_id=%(correlation_id)s] %(message)s",
)
logging.getLogger().addFilter(CorrelationIdFilter())
logger = logging.getLogger(__name__)

MAX_BODY_SIZE = 1 * 1024 * 1024  # 1 MB


class CorrelationIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))

        async def send_with_correlation(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-correlation-id", correlation_id.encode()))
                message["headers"] = headers
            await send(message)

        token = correlation_id_var.set(correlation_id)
        try:
            await self.app(scope, receive, send_with_correlation)
        finally:
            correlation_id_var.reset(token)


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
app.add_middleware(CorrelationIdMiddleware)
app.include_router(payments_router)

@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > MAX_BODY_SIZE:
        return PlainTextResponse("Request body too large", status_code=413)
    return await call_next(request)


async def _check_database() -> bool:
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Database health check failed")
        return False


async def _check_rabbitmq(publisher: RabbitPublisher) -> bool:
    try:
        if publisher._channel is None or publisher._channel.is_closed:
            return False
        return True
    except Exception:
        logger.exception("RabbitMQ health check failed")
        return False


@app.get("/health", tags=["health"])
async def health() -> dict:
    publisher: RabbitPublisher | None = getattr(app.state, "publisher", None)
    db_ok = await _check_database()
    rabbit_ok = await _check_rabbitmq(publisher) if publisher else False
    return {
        "status": "ok" if db_ok and rabbit_ok else "degraded",
        "database": "ok" if db_ok else "unavailable",
        "rabbitmq": "ok" if rabbit_ok else "unavailable",
    }


debug_router = APIRouter(prefix="/api/v1/_debug", tags=["debug"])

# Local unauthenticated webhook receiver for testing. Never enable in production.
_received_webhooks: deque[dict] = deque(maxlen=1000)


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
        return list(_received_webhooks)
    return [event for event in _received_webhooks if event.get("payment_id") == payment_id]


if settings.debug_endpoints_enabled:
    app.include_router(debug_router)
