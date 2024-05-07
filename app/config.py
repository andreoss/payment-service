from pydantic_settings import BaseSettings, SettingsConfigDict


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

    consumer_max_attempts: int = 3
    consumer_retry_base_delay_ms: int = 2000


settings = Settings()
