FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 --home-dir /srv appuser \
    && chown -R appuser:appuser /srv

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .

USER appuser

FROM base AS api
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS consumer
CMD ["faststream", "run", "app.consumer:app"]

FROM base AS tests
USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY pyproject.toml .
COPY tests ./tests
RUN chown -R appuser:appuser /srv
USER appuser
CMD ["pytest", "-v"]
