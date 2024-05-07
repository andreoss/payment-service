import httpx

from .helpers import API_BASE_URL, sample_payload


async def test_missing_api_key_returns_401():
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=10.0) as client:
        resp = await client.post(
            "/api/v1/payments",
            headers={"Idempotency-Key": "auth-test-missing"},
            json=sample_payload(),
        )
    assert resp.status_code == 401


async def test_wrong_api_key_returns_401():
    async with httpx.AsyncClient(
        base_url=API_BASE_URL, headers={"X-API-Key": "wrong-key"}, timeout=10.0
    ) as client:
        resp = await client.post(
            "/api/v1/payments",
            headers={"Idempotency-Key": "auth-test-wrong"},
            json=sample_payload(),
        )
    assert resp.status_code == 401


async def test_get_payment_without_api_key_returns_401():
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=10.0) as client:
        resp = await client.get("/api/v1/payments/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 401
