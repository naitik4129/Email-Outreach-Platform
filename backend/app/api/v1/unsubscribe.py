from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.modules.events.unsubscribe_service import UnsubscribeService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/unsubscribe", tags=["unsubscribe"])


@router.get("/{token}", response_class=Response)
def get_unsubscribe_page(
    token: str,
    db: Session = Depends(get_db),
    accept: str | None = Header(default=None),
) -> Response:
    """Public confirmation view.

    GET requests never automatically execute unsubscribe, preventing automated
    anti-spam URL scanners from accidentally unsubscribing recipients.
    """
    service = UnsubscribeService(db)
    token_info = service.resolve_token(token)

    if token_info is None or token_info.get("revoked_at") is not None:
        if accept and "application/json" in accept:
            return Response(
                content='{"valid": false, "message": "Link expired or invalid"}',
                media_type="application/json",
                status_code=404,
            )
        return HTMLResponse(
            "<html><body><h2>Invalid or Expired Link</h2>"
            "<p>This unsubscribe link is invalid or has already been used.</p></body></html>",
            status_code=404,
        )

    if accept and "application/json" in accept:
        return Response(
            content='{"valid": true, "message": "Confirmation required"}',
            media_type="application/json",
            status_code=200,
        )

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Confirm Unsubscribe</title></head>
    <body style="font-family: sans-serif; text-align: center; padding: 50px;">
        <h2>Confirm Unsubscribe</h2>
        <p>Are you sure you want to stop receiving emails from this sender?</p>
        <form method="POST" action="/api/v1/unsubscribe/{token}">
            <button type="submit" style="background: #e53e3e; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; font-size: 16px;">
                Unsubscribe
            </button>
        </form>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)


@router.post("/{token}")
def post_unsubscribe(
    token: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Execute immediate durable unsubscribe.

    Supports RFC 8058 One-Click unsubscribe and form submissions.
    """
    service = UnsubscribeService(db)
    result = service.execute_unsubscribe(token)

    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail=result.get("message", "Invalid or expired token"),
        )

    return result
