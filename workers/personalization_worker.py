"""Entrypoint of the personalization worker group (docs/adr/0011-0013).

The only process that holds PERSONALIZATION_OPENAI_API_KEY. It refuses to start
when personalization is enabled without the key, rather than failing on the
first generation. When the feature is disabled it still starts (and idles) so a
Compose stack never crash-loops over an optional feature.
"""

from __future__ import annotations

from app.core.config import Settings
from workers.celery_app import celery_app


def main() -> None:
    Settings.current().require_personalization_worker_ready()
    celery_app.worker_main(
        [
            "worker",
            "--loglevel=INFO",
            "--queues=personalization",
            "--concurrency=2",
        ]
    )


if __name__ == "__main__":
    main()
