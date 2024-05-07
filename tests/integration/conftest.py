import uuid

import httpx
import pytest

from .helpers import API_BASE_URL, API_KEY


@pytest.fixture
async def api_client():
    async with httpx.AsyncClient(
        base_url=API_BASE_URL, headers={"X-API-Key": API_KEY}, timeout=10.0
    ) as client:
        yield client


@pytest.fixture
def idempotency_key() -> str:
    return f"test-{uuid.uuid4()}"
