import logging

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.models import Payment

logger = logging.getLogger(__name__)


class WebhookDeliveryError(Exception):
    pass


@retry(
    reraise=True,
    stop=stop_after_attempt(settings.webhook_max_attempts),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(WebhookDeliveryError),
    # tenacity's LoggerProtocol is stricter than stdlib logging.Logger's
    # actual signature; a plain Logger works fine here at runtime.
    before_sleep=before_sleep_log(logger, logging.WARNING),  # type: ignore[arg-type]
)
async def _post_webhook(url: str, payload: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
        try:
            response = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise WebhookDeliveryError(str(exc)) from exc

    if response.status_code == 429 or response.status_code >= 500:
        raise WebhookDeliveryError(f"webhook endpoint returned status={response.status_code}")
    return response


async def send_webhook_notification(payment: Payment) -> None:
    """Delivers the payment result to the client's webhook URL.

    Retries transient failures (network errors, 5xx, 429) up to
    settings.webhook_max_attempts times with exponential backoff. A
    permanent failure (whether exhausted retries or a non-retryable 4xx) is
    logged but never raised: the payment record is already the durable
    source of truth, so a webhook outage must not trigger reprocessing of
    the payment itself.
    """
    payload = {
        "event": f"payment.{payment.status.value}",
        "payment_id": str(payment.id),
        "status": payment.status.value,
        "amount": str(payment.amount),
        "currency": payment.currency.value,
        "processed_at": payment.processed_at.isoformat() if payment.processed_at else None,
    }
    try:
        response = await _post_webhook(payment.webhook_url, payload)
    except WebhookDeliveryError:
        logger.error(
            "Webhook delivery permanently failed for payment %s after %s attempts",
            payment.id,
            settings.webhook_max_attempts,
        )
        return

    if response.status_code >= 400:
        logger.error(
            "Webhook rejected with non-retryable client error %s for payment %s; not retrying",
            response.status_code,
            payment.id,
        )
    else:
        logger.info("Webhook delivered for payment %s", payment.id)
