from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RetryDecision:
    action: Literal["retry", "dlq"]
    next_attempt: int
    delay_ms: int | None = None


def decide(attempt: int, max_attempts: int, base_delay_ms: int) -> RetryDecision:
    """Decides retry vs DLQ for a failed message; delay grows exponentially (base, base*2, base*4, ...)."""
    if attempt >= max_attempts:
        return RetryDecision(action="dlq", next_attempt=attempt)

    delay_ms = base_delay_ms * (2 ** (attempt - 1))
    return RetryDecision(action="retry", next_attempt=attempt + 1, delay_ms=delay_ms)
