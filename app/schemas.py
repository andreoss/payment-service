import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.models import Currency, PaymentStatus


class PaymentCreateRequest(BaseModel):
    # max_digits/decimal_places mirror the `Numeric(18, 2)` payments.amount
    # column: without them, e.g. "10.005" would validate fine and then be
    # silently rounded by Postgres on insert instead of being rejected.
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: Currency
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # max_length mirrors payments.webhook_url's `String(2048)` column, so an
    # oversized URL is a 422 instead of a DB error at insert time.
    webhook_url: HttpUrl = Field(max_length=2048)


class PaymentCreateResponse(BaseModel):
    payment_id: uuid.UUID
    status: PaymentStatus
    created_at: datetime


class PaymentDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(serialization_alias="payment_id")
    amount: Decimal
    currency: Currency
    description: str | None
    metadata: dict[str, Any] = Field(validation_alias="payment_metadata")
    status: PaymentStatus
    webhook_url: str
    created_at: datetime
    processed_at: datetime | None
