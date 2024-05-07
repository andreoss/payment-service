from app.retry_policy import decide


def test_first_failure_schedules_retry_with_base_delay():
    decision = decide(attempt=1, max_attempts=3, base_delay_ms=2000)
    assert decision.action == "retry"
    assert decision.next_attempt == 2
    assert decision.delay_ms == 2000


def test_delay_doubles_each_retry():
    decision = decide(attempt=2, max_attempts=3, base_delay_ms=2000)
    assert decision.action == "retry"
    assert decision.next_attempt == 3
    assert decision.delay_ms == 4000


def test_final_attempt_routes_to_dlq():
    decision = decide(attempt=3, max_attempts=3, base_delay_ms=2000)
    assert decision.action == "dlq"
    assert decision.next_attempt == 3
    assert decision.delay_ms is None


def test_attempt_beyond_max_also_routes_to_dlq():
    decision = decide(attempt=5, max_attempts=3, base_delay_ms=2000)
    assert decision.action == "dlq"
