import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db_session
from app.main import app
from app.models import Currency, Payment, PaymentStatus
from app.services import payment_service as payment_service_module

API_KEY_HEADERS = {"X-API-Key": "changeme"}


def _fake_payment(**overrides) -> Payment:
    payment = Payment(
        id=uuid.uuid4(),
        idempotency_key="key-1",
        amount="12.34",
        currency=Currency.USD,
        description="desc",
        payment_metadata={"a": 1},
        status=PaymentStatus.PENDING,
        webhook_url="https://example.com/hook",
        created_at=datetime.now(UTC),
        processed_at=None,
    )
    for key, value in overrides.items():
        setattr(payment, key, value)
    return payment


def _simulate_flush_assigned_defaults(obj) -> None:
    """`mapped_column(default=...)` only fires on a real flush; since the
    fake session never flushes for real, mimic what Postgres/SQLAlchemy
    would have assigned so response models see fully-populated rows."""
    if isinstance(obj, Payment):
        obj.id = obj.id or uuid.uuid4()
        obj.status = obj.status or PaymentStatus.PENDING
        obj.created_at = obj.created_at or datetime.now(UTC)


@pytest.fixture
def client_with_fake_session():
    session = MagicMock(spec=AsyncSession)
    session.add = MagicMock(side_effect=_simulate_flush_assigned_defaults)

    async def override_get_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    transport = httpx.ASGITransport(app=app)
    try:
        yield httpx.AsyncClient(transport=transport, base_url="http://test"), session
    finally:
        app.dependency_overrides.pop(get_db_session, None)


async def test_health_endpoint():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_create_payment_returns_202_with_pending_status(client_with_fake_session):
    client, session = client_with_fake_session
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with patch.object(payment_service_module, "ensure_webhook_url_is_safe", AsyncMock()):
        async with client:
            resp = await client.post(
                "/api/v1/payments",
                headers={**API_KEY_HEADERS, "Idempotency-Key": "test-key"},
                json={
                    "amount": "10.00",
                    "currency": "USD",
                    "webhook_url": "https://example.com/hook",
                },
            )

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert "payment_id" in body


async def test_create_payment_rejects_webhook_url_targeting_internal_host(
    client_with_fake_session,
):
    client, session = client_with_fake_session
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)

    async with client:
        resp = await client.post(
            "/api/v1/payments",
            headers={**API_KEY_HEADERS, "Idempotency-Key": "test-key-ssrf"},
            json={
                "amount": "10.00",
                "currency": "USD",
                "webhook_url": "http://127.0.0.1/hook",
            },
        )

    assert resp.status_code == 422
    session.add.assert_not_called()


async def test_get_payment_returns_404_when_missing(client_with_fake_session):
    client, session = client_with_fake_session
    session.get = AsyncMock(return_value=None)

    async with client:
        resp = await client.get(
            f"/api/v1/payments/{uuid.uuid4()}",
            headers=API_KEY_HEADERS,
        )

    assert resp.status_code == 404


async def test_get_payment_returns_detail_when_found(client_with_fake_session):
    client, session = client_with_fake_session
    payment = _fake_payment()
    session.get = AsyncMock(return_value=payment)

    async with client:
        resp = await client.get(
            f"/api/v1/payments/{payment.id}",
            headers=API_KEY_HEADERS,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["payment_id"] == str(payment.id)
    assert body["metadata"] == {"a": 1}


async def test_payments_endpoints_reject_missing_api_key(client_with_fake_session):
    client, _session = client_with_fake_session

    async with client:
        resp = await client.get(f"/api/v1/payments/{uuid.uuid4()}")

    assert resp.status_code == 401


async def test_webhook_echo_and_events_debug_endpoints():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {"event": "payment.succeeded", "payment_id": "pid-123", "status": "succeeded"}
        echo_resp = await client.post("/api/v1/_debug/webhook-echo", json=payload)
        assert echo_resp.status_code == 200

        events_resp = await client.get(
            "/api/v1/_debug/webhook-events", params={"payment_id": "pid-123"}
        )
        assert payload in events_resp.json()
