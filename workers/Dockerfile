FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/backend

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY backend ./backend
COPY workers ./workers
RUN pip install --no-cache-dir ./backend

USER app

CMD ["celery", "-A", "workers.celery_app:celery_app", "worker", "--loglevel=INFO", "--queues=maintenance"]
