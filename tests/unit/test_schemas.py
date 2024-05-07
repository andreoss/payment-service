import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models import Currency, PaymentStatus
from app.schemas import PaymentCreateRequest, PaymentDetailResponse


def _valid_payload(**overrides):
    payload = {
        "amount": "100.00",
        "currency": "RUB",
        "description": "test",
        "metadata": {"a": 1},
        "webhook_url": "https://example.com/hook",
    }
    payload.update(overrides)
    return payload


def test_payment_create_request_accepts_valid_payload():
    request = PaymentCreateRequest(**_valid_payload())
    assert request.amount == Decimal("100.00")
    assert request.currency == Currency.RUB
    assert str(request.webhook_url) == "https://example.com/hook"


def test_payment_create_request_rejects_non_positive_amount():
    for bad_amount in ("0", "-5.00"):
        with pytest.raises(ValidationError):
            PaymentCreateRequest(**_valid_payload(amount=bad_amount))


def test_payment_create_request_rejects_unknown_currency():
    with pytest.raises(ValidationError):
        PaymentCreateRequest(**_valid_payload(currency="GBP"))


def test_payment_create_request_rejects_invalid_webhook_url():
    with pytest.raises(ValidationError):
        PaymentCreateRequest(**_valid_payload(webhook_url="not-a-url"))


def test_payment_create_request_metadata_defaults_to_empty_dict():
    payload = _valid_payload()
    del payload["metadata"]
    request = PaymentCreateRequest(**payload)
    assert request.metadata == {}


def test_payment_detail_response_maps_payment_metadata_and_id_aliases():
    fake_payment = SimpleNamespace(
        id=uuid.uuid4(),
        amount=Decimal("42.50"),
        currency=Currency.EUR,
        description=None,
        payment_metadata={"order": "1"},
        status=PaymentStatus.SUCCEEDED,
        webhook_url="https://example.com/hook",
        created_at=datetime.now(UTC),
        processed_at=datetime.now(UTC),
    )

    response = PaymentDetailResponse.model_validate(fake_payment)
    dumped = response.model_dump(by_alias=True)

    assert dumped["payment_id"] == fake_payment.id
    assert dumped["metadata"] == {"order": "1"}
    assert dumped["status"] == PaymentStatus.SUCCEEDED
