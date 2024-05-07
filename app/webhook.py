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
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def _post_webhook(url: str, payload: dict) -> None:
    async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
        try:
            response = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise WebhookDeliveryError(str(exc)) from exc

    if response.status_code == 429 or response.status_code >= 500:
        raise WebhookDeliveryError(f"webhook endpoint returned status={response.status_code}")
    if response.status_code >= 400:
        logger.error("Webhook rejected with client error %s for %s", response.status_code, url)


async def send_webhook_notification(payment: Payment) -> None:
    """Delivers the payment result to the client's webhook URL.

    Retries transient failures (network errors, 5xx, 429) up to
    settings.webhook_max_attempts times with exponential backoff. A
    permanent failure is logged but never raised: the payment record is
    already the durable source of truth, so a webhook outage must not
    trigger reprocessing of the payment itself.
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
        await _post_webhook(payment.webhook_url, payload)
        logger.info("Webhook delivered for payment %s", payment.id)
    except WebhookDeliveryError:
        logger.error(
            "Webhook delivery permanently failed for payment %s after %s attempts",
            payment.id,
            settings.webhook_max_attempts,
        )
