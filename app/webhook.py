import hashlib
import hmac
import json
import logging
from urllib.parse import urlsplit

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
from app.url_safety import UnsafeWebhookURLError, ensure_webhook_url_is_safe, VettedWebhookURL

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Webhook-Signature"


class WebhookDeliveryError(Exception):
    """Transient delivery failure worth retrying."""


def sign_payload(body: bytes, key: str) -> str:
    """HMAC-SHA256 hex digest over the exact request body.

    Receivers verify authenticity by recomputing the digest over the raw body
    and comparing via hmac.compare_digest — no shared secret ever travels on
    the wire.
    """
    return hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _build_url_for_ip(vetted: VettedWebhookURL) -> str:
    parts = urlsplit(vetted.original_url)
    if vetted.vetted_ip:
        host = vetted.vetted_ip
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
    else:
        host = parts.hostname or ""
    if vetted.port not in (80, 443) or (vetted.scheme == "http" and vetted.port != 80) or (vetted.scheme == "https" and vetted.port != 443):
        host = f"{host}:{vetted.port}"
    return f"{vetted.scheme}://{host}{parts.path or '/'}{('?' + parts.query) if parts.query else ''}"


class _HostHeaderTransport(httpx.AsyncHTTPTransport):
    def __init__(self, hostname: str, *args, **kwargs):
        self._hostname = hostname
        super().__init__(*args, **kwargs)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.headers.setdefault("Host", self._hostname)
        return await super().handle_async_request(request)


def _webhook_retry():
    return retry(
        reraise=True,
        stop=stop_after_attempt(settings.webhook_max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(WebhookDeliveryError),
        before_sleep=before_sleep_log(logger, logging.WARNING),  # type: ignore[arg-type]
    )


@_webhook_retry()
async def _post_webhook(vetted: VettedWebhookURL, body: bytes, headers: dict[str, str]) -> httpx.Response:
    target_url = _build_url_for_ip(vetted)
    transport = _HostHeaderTransport(vetted.hostname)
    async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds, transport=transport) as client:
        try:
            response = await client.post(target_url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise WebhookDeliveryError(str(exc)) from exc

    if response.status_code == 429 or response.status_code >= 500:
        raise WebhookDeliveryError(f"webhook endpoint returned status={response.status_code}")
    return response


async def send_webhook_notification(payment: Payment) -> None:
    """Delivers the payment result to the client's webhook URL.

    Transient failures (network errors, 429, 5xx) are retried with exponential
    backoff; other 4xx responses are permanent and logged once. Failures are
    never raised to the caller: the payments table is the durable source of
    truth and the webhook is best-effort notification on top of it.
    """
    try:
        vetted = await ensure_webhook_url_is_safe(payment.webhook_url)
    except UnsafeWebhookURLError:
        logger.error(
            "Refusing to deliver webhook for payment %s: URL is not a permitted destination",
            payment.id,
        )
        return

    payload = {
        "event": f"payment.{payment.status.value}",
        "payment_id": str(payment.id),
        "status": payment.status.value,
        "amount": str(payment.amount),
        "currency": payment.currency.value,
        "processed_at": payment.processed_at.isoformat() if payment.processed_at else None,
    }
    # Sign over the exact bytes that go on the wire; re-serializing would
    # invalidate the signature.
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if settings.webhook_signing_key:
        headers[SIGNATURE_HEADER] = sign_payload(body, settings.webhook_signing_key)

    try:
        response = await _post_webhook(vetted, body, headers)
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
