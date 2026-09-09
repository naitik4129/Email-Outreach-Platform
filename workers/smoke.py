from __future__ import annotations

import argparse
from uuid import uuid4

from workers.celery_app import celery_app


def publish_smoke(timeout: float) -> dict[str, object]:
    correlation_id = str(uuid4())
    result = celery_app.send_task(
        "infrastructure.smoke",
        kwargs={"payload": {"correlation_id": correlation_id}},
        queue="maintenance",
    )
    return result.get(timeout=timeout)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish and await a Phase 0 smoke task.")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    print(publish_smoke(args.timeout))


if __name__ == "__main__":
    main()

