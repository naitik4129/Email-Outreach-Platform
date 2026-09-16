from __future__ import annotations

from functools import lru_cache

from celery import Celery

from app.core.config import Settings


@lru_cache
def _build_task_producer(broker_url: str) -> Celery:
    """A bare Celery client for API code to enqueue worker tasks by name.

    Deliberately does not import `workers.celery_app`: that module also
    imports every task implementation (workers.tasks, workers.imports,
    workers.campaigns) as a side effect, which pulls in dependencies the
    backend does not install and does not need. Publishing a task by name
    only requires a client configured with the same broker and serializer
    settings as the worker -- the task functions themselves only need to be
    importable by the process that actually consumes the queue.
    """
    celery = Celery("email_outreach_api", broker=broker_url, backend=broker_url)
    celery.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
    )
    return celery


def get_task_producer(settings: Settings | None = None) -> Celery:
    settings = settings or Settings.current()
    return _build_task_producer(settings.redis_url)
