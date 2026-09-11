from __future__ import annotations

from app.core.config import Settings
from app.core.logging import configure_logging
from celery import Celery


def create_celery_app(settings: Settings | None = None) -> Celery:
    settings = settings or Settings.current()
    configure_logging(settings, service_name="worker")
    celery = Celery("email_outreach", broker=settings.redis_url, backend=settings.redis_url)
    celery.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_default_queue="maintenance",
        task_routes={"infrastructure.smoke": {"queue": "maintenance"}},
        worker_prefetch_multiplier=1,
        task_acks_late=False,
        broker_connection_retry_on_startup=True,
    )
    return celery


celery_app = create_celery_app()

import workers.tasks  # noqa: F401
import workers.imports  # noqa: F401
