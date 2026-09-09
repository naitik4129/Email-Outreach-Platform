from __future__ import annotations

import pytest

from workers.celery_app import create_celery_app
from workers.tasks import infrastructure_smoke


def test_celery_routes_smoke_task_to_maintenance() -> None:
    celery = create_celery_app()

    route = celery.conf.task_routes["infrastructure.smoke"]

    assert route == {"queue": "maintenance"}


def test_smoke_task_is_infrastructure_only() -> None:
    result = infrastructure_smoke.run({"correlation_id": "rid"})

    assert result == {
        "status": "ok",
        "task": "infrastructure.smoke",
        "correlation_id": "rid",
    }


def test_smoke_task_rejects_product_like_payload() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        infrastructure_smoke.run({"campaign_id": "not-phase-0"})

