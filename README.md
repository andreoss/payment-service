# Payment Processing Service

Async payment processing microservice: accepts a payment request, emulates a
call to an external payment gateway via a RabbitMQ queue, and notifies the
client of the result via webhook.

## Stack

- FastAPI + Pydantic v2
- SQLAlchemy 2.0 (async, asyncpg)
- PostgreSQL 16
- RabbitMQ (FastStream + aio-pika)
- Alembic
- Docker / docker-compose

## Architecture

```
                 ┌─────────────┐         outbox table          ┌──────────────┐
POST /payments → │   API (FastAPI)  │──── insert payment + ────▶│  PostgreSQL  │
                 │  + outbox relay  │       outbox event         │              │
                 └────────┬────────┘                             └──────────────┘
                          │ background task polls
                          │ outbox (FOR UPDATE SKIP LOCKED)
                          ▼
                 payments.direct (exchange) → payments.new (queue)
                          │
                          ▼
                 ┌─────────────────┐   process (2-5s, 90%/10%)   ┌──────────────┐
                 │  Consumer        │────────────────────────────▶│  PostgreSQL  │
                 │  (FastStream)    │   update payment status     │              │
                 └────────┬────────┘                             └──────────────┘
                          │ webhook POST (tenacity, 3 attempts,
                          │ exponential backoff)
                          ▼
                   client's webhook_url

  on processing exception:
    attempt < 3  → payments.retry (per-message TTL = 2^n sec) → dead-lettered
                    back into payments.direct/payments.new for redelivery
    attempt >= 3 → payments.dlq (exchange) → payments.new.dlq (queue)
```

### Components

- **api** — HTTP API (`app/main.py`, `app/api/`). Its lifespan also starts the
  **outbox relay** — a background asyncio task that once a second reads
  unpublished events from the `outbox_events` table
  (`SELECT ... FOR UPDATE SKIP LOCKED`), publishes them to RabbitMQ with
  publisher confirms, and only after confirmation marks the event as
  published.
- **consumer** — a separate FastStream process subscribed to the
  `payments.new` queue. Emulates payment processing, updates the status in
  the DB, and sends the webhook. On an unhandled exception it performs its
  own retries with exponential delay via a delay queue, and after 3 attempts
  sends the message to the DLQ.
- **migrate** — a one-shot service that applies Alembic migrations before
  api/consumer start.

### Why this design

- **Outbox pattern**: the payment row and the publication event are written
  in a single DB transaction (`PaymentService.create_payment`), so the
  situation "payment created but event lost" (or vice versa) is impossible.
  Publishing to RabbitMQ happens in a separate background process
  (at-least-once delivery) — the consumer therefore must be idempotent.
- **Idempotency**:
  - At the API level: `Idempotency-Key` is unique in the `payments` table; a
    repeated request with the same key returns the already created payment
    without creating a new row or a new outbox event.
  - At the consumer level: before processing it checks that
    `status == pending`. If the payment is already processed (e.g. the
    message was duplicated due to the outbox relay's at-least-once delivery),
    processing is skipped — the gateway is not called again.
- **Retry / DLQ**: retry is implemented via a RabbitMQ delay queue —
  `payments.new.retry` with `x-dead-letter-exchange` pointing back to the
  main exchange. When publishing into it, a per-message TTL (`expiration`) is
  set equal to the exponential delay of the attempt (2s, 4s, 8s...). When the
  TTL expires, RabbitMQ itself moves the message back to `payments.new` — no
  extra consumers/schedulers are needed. After `CONSUMER_MAX_ATTEMPTS`
  (default 3) failed attempts the message is published to `payments.dlq` →
  `payments.new.dlq` for manual investigation.
- **Webhook retry** — a mechanism separate from message retry: `tenacity`
  makes up to `WEBHOOK_MAX_ATTEMPTS` attempts with exponential backoff on
  network errors, 5xx, and 429. A webhook failure after attempts are
  exhausted does **not** cause the payment to be reprocessed — the payment is
  already in a final status (`succeeded`/`failed`), which is the single
  source of truth; an undelivered webhook is only logged.

## Run

```bash
cp .env.example .env   # optional, .env is already included with working defaults
docker compose up --build
```

Starts: `postgres`, `rabbitmq` (with management UI on :15672, guest/guest),
`migrate` (applies migrations and exits), `api` (:8000), `consumer`.

Check that everything is up:

```bash
curl http://localhost:8000/health
```

## Tests

Tests run in a separate Docker service `tests`, so no local Python is
required — only Docker.

```bash
# the main stack must be up first
docker compose up --build -d postgres rabbitmq migrate api consumer

# unit + integration tests (integration ones hit the live api/consumer/rabbitmq)
docker compose run --rm tests pytest -v

# unit tests only (do not require api/consumer to be up)
docker compose run --rm --no-deps tests pytest tests/unit -v

# integration only
docker compose run --rm tests pytest tests/integration -v
```

- **Unit tests** (`tests/unit/`) — no network or DB: Pydantic schema
  validation, webhook retry (httpx mocked via `respx`), the branching of
  `PaymentService.create_payment` (SQLAlchemy session mocked), gateway
  emulation and idempotency in `payment_processor.process_payment` (the
  session and `send_webhook_notification` mocked), the HTTP layer
  (`app/api/*`) via `httpx.ASGITransport` with the `get_db_session`
  dependency overridden — no real FastAPI TestClient/network, but through
  real routing and response serialization — and the retry/DLQ policy
  (`app/retry_policy.py`).
- **Integration tests** (`tests/integration/`) — run against the actually
  running stack (same docker-compose network; the `tests` service reaches
  `api`/`rabbitmq` by container names): the full payment lifecycle from
  creation to `succeeded`/`failed` and webhook delivery, idempotency by
  `Idempotency-Key`, authentication, validation, and the retry → DLQ
  scenario — the test publishes a deliberately "poisoned" message directly
  into the `payments.new` queue and verifies via the RabbitMQ management API
  that after 3 attempts it ends up in `payments.new.dlq`.

For debugging/manually checking webhook delivery without an external service
there is `GET /api/v1/_debug/webhook-events` — it returns all payloads
received by the `/_debug/webhook-echo` stub (used both in the tests and in
the examples below).

### Linters and typing

```bash
docker compose run --rm --no-deps tests ruff check .              # lint
docker compose run --rm --no-deps -v "$(pwd):/srv" tests ruff format .  # autoformat (writes to the host)
docker compose run --rm --no-deps tests mypy app                  # static typing
```

Configuration lives in `pyproject.toml` (`[tool.ruff]`, `[tool.mypy]`).
`ruff format` needs the bind mount (`-v`), otherwise formatting is applied
only inside the one-off container and never reaches the host.

### Test coverage

```bash
docker compose run --rm tests pytest --cov=app --cov-report=term-missing
```

The `fail_under = 70` threshold (in `[tool.coverage.report]`) is the real
result of the whole suite (unit + integration). Business logic (`app/api/*`,
`app/services/`, `app/schemas.py`, `app/webhook.py`,
`app/payment_processor.py`, `app/retry_policy.py`) is covered at 95-100%.
Modules like `app/consumer.py`, `app/main.py` (lifespan),
`app/outbox_relay.py`, `app/rabbitmq*.py`, `app/db.py` show low coverage in
this report not because they are untested, but because they actually run in
other containers (`api`/`consumer`) during the integration tests —
`coverage.py` cannot see execution in another process. This is a limitation
of line-coverage tools in a multi-container system, not a gap in the tests:
the same code is exercised in `tests/integration/` against the live stack.

## API

All `/api/v1/payments/*` endpoints require the `X-API-Key` header
(the value comes from `API_KEY` in `.env`, default `changeme`).

### Create payment

```bash
curl -X POST http://localhost:8000/api/v1/payments \
  -H "X-API-Key: changeme" \
  -H "Idempotency-Key: order-42" \
  -H "Content-Type: application/json" \
  -d '{
    "amount": "150.00",
    "currency": "RUB",
    "description": "Order #42",
    "metadata": {"order_id": "42"},
    "webhook_url": "http://api:8000/api/v1/_debug/webhook-echo"
  }'
```

`webhook_url` must be reachable from the consumer container — for local
verification without an external service the project includes a stub
`POST /api/v1/_debug/webhook-echo` that simply logs the received payload
(see `docker compose logs api`). For a real external URL something like
https://webhook.site works.

Response `202 Accepted`:

```json
{
  "payment_id": "bff3aa8c-bc06-44d2-bda5-2b5e0f6ab46d",
  "status": "pending",
  "created_at": "2026-08-14T14:30:25.560075Z"
}
```

A repeated request with the same `Idempotency-Key` (even with a different
body) returns the same `payment_id` and does not create a duplicate payment.

### Get payment

```bash
curl http://localhost:8000/api/v1/payments/bff3aa8c-bc06-44d2-bda5-2b5e0f6ab46d \
  -H "X-API-Key: changeme"
```

```json
{
  "payment_id": "bff3aa8c-bc06-44d2-bda5-2b5e0f6ab46d",
  "amount": "150.00",
  "currency": "RUB",
  "description": "Order #42",
  "metadata": {"order_id": "42"},
  "status": "succeeded",
  "webhook_url": "http://api:8000/api/v1/_debug/webhook-echo",
  "created_at": "2026-08-14T14:30:25.560075Z",
  "processed_at": "2026-08-14T14:30:28.501748Z"
}
```

Processing is asynchronous and takes 2-5 seconds (gateway emulation), so
right after creation the status will be `pending`.

## Checking the DLQ

To see a message that landed in the DLQ after 3 failed processing attempts,
the easiest way is to look at the `payments.new.dlq` queue in the RabbitMQ
management UI (http://localhost:15672, guest/guest) — the Queues tab.
Messages there contain the original event payload and an `x-attempt` header
with the attempt count.

## Environment variables

See `.env.example`. The main ones:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | async DSN for PostgreSQL |
| `RABBITMQ_URL` | AMQP DSN |
| `API_KEY` | value expected in the `X-API-Key` header |
| `PAYMENT_FAILURE_RATE` | share of payments emulated as `failed` (default 0.1) |
| `WEBHOOK_MAX_ATTEMPTS` / `CONSUMER_MAX_ATTEMPTS` | number of webhook delivery / message processing attempts |
| `CONSUMER_RETRY_BASE_DELAY_MS` | base delay of the retry queue (doubles exponentially) |

## Migrations

```bash
# apply manually (without docker compose)
alembic upgrade head

# create a new migration after changing app/models.py
alembic revision --autogenerate -m "message"
```

## Running locally without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# start postgres and rabbitmq any way you like, put their addresses in .env
alembic upgrade head
uvicorn app.main:app --reload            # terminal 1
faststream run app.consumer:app --reload # terminal 2
```

## Known simplifications

- The outbox relay is a background task inside the `api` process, not a
  separate service; at this scale (a single api replica) this does not affect
  delivery guarantees (SKIP LOCKED still protects against races when
  scaling out).
- A webhook delivery failure after retries are exhausted is only logged;
  there is no separate table/queue for webhook retries — the payment itself
  is already durably stored and always available via
  `GET /api/v1/payments/{id}`.
- Both a missing and an invalid `X-API-Key` return `401` (rather than `422`
  for a missing one) — the header is declared optional at the FastAPI level
  and checked manually in `verify_api_key`, so both cases produce a uniform
  authentication response.
