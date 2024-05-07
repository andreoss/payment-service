from pydantic_settings import BaseSettings, SettingsConfigDict
import warnings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://payments:payments@localhost:5432/payments"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    api_key: str = "changeme"

    outbox_poll_interval: float = 1.0
    outbox_batch_size: int = 20

    payment_min_processing_seconds: float = 2.0
    payment_max_processing_seconds: float = 5.0
    payment_failure_rate: float = 0.1

    webhook_timeout_seconds: float = 5.0
    webhook_max_attempts: int = 3
    # Empty disables the X-Webhook-Signature HMAC header entirely.
    webhook_signing_key: str = ""
    webhook_allow_private_hosts: bool = False

    debug_endpoints_enabled: bool = False

    consumer_max_attempts: int = 3
    consumer_retry_base_delay_ms: int = 2000

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.api_key == "changeme" and not self.debug_endpoints_enabled:
            warnings.warn(
                "API_KEY is set to default 'changeme' — change it in production!",
                RuntimeWarning,
                stacklevel=2,
            )
        if not self.webhook_signing_key and not self.debug_endpoints_enabled:
            warnings.warn(
                "WEBHOOK_SIGNING_KEY is empty — webhook signatures disabled in production!",
                RuntimeWarning,
                stacklevel=2,
            )


settings = Settings()
