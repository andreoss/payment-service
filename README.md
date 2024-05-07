# Payment Processing Service

Async payment processing microservice: FastAPI + PostgreSQL + RabbitMQ
(outbox pattern, retry/DLQ, webhook notifications).

## Run

```bash
docker compose up --build
```

Starts `postgres`, `rabbitmq` (UI on :15672, guest/guest), `migrate`,
`api` (:8000), `consumer`.

## API

Endpoints `/api/v1/payments/*` require header `X-API-Key: changeme`.

### Create payment

```bash
curl -X POST http://localhost:8000/api/v1/payments \
  -H "X-API-Key: changeme" -H "Idempotency-Key: order-42" \
  -H "Content-Type: application/json" \
  -d '{"amount": "150.00", "currency": "RUB", "webhook_url": "http://api:8000/api/v1/_debug/webhook-echo"}'
```

→ `202 Accepted`, `{"payment_id": "...", "status": "pending", ...}`

### Get payment

```bash
curl http://localhost:8000/api/v1/payments/<payment_id> -H "X-API-Key: changeme"
```

## Tests

```bash
docker compose up --build -d postgres rabbitmq migrate api consumer
docker compose run --rm tests pytest --cov=app --cov-report=term-missing
```

## Environment variables

See `.env.example`.

## Migrations

```bash
alembic revision --autogenerate -m "message"
alembic upgrade head
```
