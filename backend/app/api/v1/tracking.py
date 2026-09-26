from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import Settings
from app.core.metrics import record_open_tracking
from app.db.context import set_transaction_context
from app.modules.tracking.pixel import TRANSPARENT_GIF
from app.modules.tracking.service import record_open
from app.modules.tracking.tokens import parse_open_token

logger = logging.getLogger(__name__)

# Public, unauthenticated: the signed token is the only credential. Recipients'
# mail clients call this, so it must never fail visibly.
router = APIRouter(prefix="/t", tags=["tracking"])

_PIXEL_HEADERS = {
    "Cache-Control": "no-store, no-cache, max-age=0, private",
    "X-Content-Type-Options": "nosniff",
}


def _pixel() -> Response:
    return Response(
        content=TRANSPARENT_GIF, media_type="image/gif", headers=_PIXEL_HEADERS
    )


@router.head("/o/{filename}", include_in_schema=False)
def open_pixel_head(filename: str) -> Response:
    # Link scanners and prefetchers probe with HEAD; that is not an open.
    return _pixel()


@router.get("/o/{filename}", include_in_schema=False)
def open_pixel(filename: str, db: Session = Depends(get_db)) -> Response:
    """Record an email open and return a 1x1 transparent GIF.

    The same image is returned for valid, invalid, unknown and failing requests
    so the response leaks nothing about which tokens exist.
    """
    settings = Settings.current()
    parsed = parse_open_token(
        filename.removesuffix(".gif"), settings.tracking_signing_key
    )
    if parsed is None:
        record_open_tracking("rejected")
        logger.info("open_tracking_rejected", extra={"reason": "invalid_token"})
        return _pixel()

    workspace_id, message_id = parsed
    try:
        set_transaction_context(db, workspace_id=workspace_id)
        recorded = record_open(db, workspace_id=workspace_id, message_id=message_id)
    except SQLAlchemyError:
        db.rollback()
        record_open_tracking("error")
        logger.exception(
            "open_tracking_record_failed",
            extra={"workspace_id": str(workspace_id), "message_id": str(message_id)},
        )
        return _pixel()

    if not recorded:
        record_open_tracking("not_found")
        logger.info(
            "open_tracking_rejected",
            extra={"reason": "unknown_message", "workspace_id": str(workspace_id)},
        )
        return _pixel()

    record_open_tracking("recorded")
    return _pixel()
