FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
RUN chmod -R a+rX /app

# Test image: adds dev tools and the test suite (used by `docker compose --profile test`).
FROM base AS dev
COPY requirements-dev.txt pyproject.toml pytest.ini ./
RUN pip install -r requirements-dev.txt
COPY tests ./tests

# Runtime image (default target): application code only, non-root user.
FROM base AS runtime
RUN useradd --create-home --uid 10001 payflow
USER payflow
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
