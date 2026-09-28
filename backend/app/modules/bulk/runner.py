from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.schemas.bulk import (
    BulkActionIn,
    BulkActionOut,
    BulkItemIn,
    BulkItemResult,
)

logger = logging.getLogger(__name__)


def run_bulk(
    session: Session,
    payload: BulkActionIn,
    action: Callable[[BulkItemIn], object],
) -> BulkActionOut:
    """Apply ``action`` to every item, independently.

    Each item runs in its own savepoint, so one item that the domain rules (or
    a database guard) refuse is rolled back on its own and reported, while the
    others still commit. ``action`` is the ordinary single-item service call:
    authorization, tenant scoping and state rules stay in one place and a bulk
    request can never do something the single endpoint would refuse.
    """
    results: list[BulkItemResult] = []
    for item in payload.items:
        try:
            with session.begin_nested():
                action(item)
            results.append(BulkItemResult(id=item.id, ok=True))
        except AppError as exc:
            results.append(
                BulkItemResult(id=item.id, ok=False, code=exc.code, message=exc.message)
            )
        except DBAPIError:
            # A database guard refused the change. The driver message can name
            # internal objects, so report a fixed one and log the detail.
            logger.warning("bulk item rejected by database", exc_info=True)
            results.append(
                BulkItemResult(
                    id=item.id,
                    ok=False,
                    code="rejected",
                    message="This item cannot be changed in its current state",
                )
            )
    succeeded = sum(1 for result in results if result.ok)
    return BulkActionOut(
        results=results, succeeded=succeeded, failed=len(results) - succeeded
    )
