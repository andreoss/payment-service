import random
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RetryDecision:
    action: Literal["retry", "dlq"]
    next_attempt: int
    delay_ms: int | None = None


def decide(attempt: int, max_attempts: int, base_delay_ms: int) -> RetryDecision:
    """Decides retry vs DLQ for a failed message; delay grows exponentially with jitter."""
    if attempt >= max_attempts:
        return RetryDecision(action="dlq", next_attempt=attempt)

    # Exponential backoff with full jitter: random(0, base * 2^(attempt-1))
    max_delay_ms = base_delay_ms * (2 ** (attempt - 1))
    delay_ms = random.randint(0, max_delay_ms)
    return RetryDecision(action="retry", next_attempt=attempt + 1, delay_ms=delay_ms)
