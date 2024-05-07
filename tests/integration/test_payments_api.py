import pytest

from .helpers import sample_payload, wait_for_terminal_status


async def test_health(api_client):
    resp = await api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_create_and_get_payment_full_flow(api_client, idempotency_key):
    create_resp = await api_client.post(
        "/api/v1/payments",
        headers={"Idempotency-Key": idempotency_key},
        json=sample_payload(),
    )
    assert create_resp.status_code == 202
    body = create_resp.json()
    assert body["status"] == "pending"
    assert "payment_id" in body and "created_at" in body
    payment_id = body["payment_id"]

    data = await wait_for_terminal_status(api_client, payment_id)
    assert data["status"] in ("succeeded", "failed")
    assert data["processed_at"] is not None
    assert data["amount"] == "150.00"
    assert data["currency"] == "RUB"
    assert data["metadata"] == {"source": "pytest"}

    events_resp = await api_client.get(
        "/api/v1/_debug/webhook-events", params={"payment_id": payment_id}
    )
    events = events_resp.json()
    assert len(events) == 1
    assert events[0]["status"] == data["status"]
    assert events[0]["event"] == f"payment.{data['status']}"


async def test_idempotent_replay_returns_same_payment_and_ignores_new_body(api_client, idempotency_key):
    first = await api_client.post(
        "/api/v1/payments",
        headers={"Idempotency-Key": idempotency_key},
        json=sample_payload(amount="10.00"),
    )
    second = await api_client.post(
        "/api/v1/payments",
        headers={"Idempotency-Key": idempotency_key},
        json=sample_payload(amount="9999.00", currency="USD"),
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["payment_id"] == second.json()["payment_id"]

    detail = await api_client.get(f"/api/v1/payments/{first.json()['payment_id']}")
    assert detail.json()["amount"] == "10.00"
    assert detail.json()["currency"] == "RUB"


async def test_get_unknown_payment_returns_404(api_client):
    resp = await api_client.get("/api/v1/payments/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


async def test_create_payment_requires_idempotency_key(api_client):
    resp = await api_client.post("/api/v1/payments", json=sample_payload())
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": "-1.00"},
        {"amount": "0"},
        {"currency": "GBP"},
        {"webhook_url": "not-a-url"},
    ],
)
async def test_create_payment_validation_errors(api_client, idempotency_key, overrides):
    resp = await api_client.post(
        "/api/v1/payments",
        headers={"Idempotency-Key": idempotency_key},
        json=sample_payload(**overrides),
    )
    assert resp.status_code == 422
