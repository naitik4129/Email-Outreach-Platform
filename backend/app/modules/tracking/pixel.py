from __future__ import annotations

import re
from html import escape
from urllib.parse import urlparse
from uuid import UUID

from app.core.config import Settings
from app.modules.tracking.tokens import make_open_token

# 1x1 transparent GIF (43 bytes). The same bytes are served for every request,
# valid or not, so the response reveals nothing about the token.
TRANSPARENT_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01"
    b"\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)

_BODY_CLOSE_RE = re.compile(r"</body\s*>", re.IGNORECASE)
OPEN_PATH_PREFIX = "/api/v1/t/o/"


def open_tracking_ready(settings: Settings) -> bool:
    """Tracking is injected only when fully configured, so an unconfigured
    environment can never emit an unsigned or unreachable tracking URL."""
    if not (
        settings.open_tracking_enabled
        and settings.tracking_signing_key
        and settings.tracking_base_url
    ):
        return False
    parsed = urlparse(settings.tracking_base_url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def open_pixel_url(settings: Settings, workspace_id: UUID, message_id: UUID) -> str:
    token = make_open_token(workspace_id, message_id, settings.tracking_signing_key)
    return f"{settings.tracking_base_url.rstrip('/')}{OPEN_PATH_PREFIX}{token}.gif"


def inject_open_pixel(html: str, pixel_url: str) -> str:
    """Add the tracking pixel to an HTML body (before </body> when present)."""
    tag = (
        f'<img src="{escape(pixel_url, quote=True)}" width="1" height="1" alt="" '
        'style="border:0;width:1px;height:1px;opacity:0" />'
    )
    matches = list(_BODY_CLOSE_RE.finditer(html))
    if matches:
        last = matches[-1]
        return html[: last.start()] + tag + html[last.start() :]
    return html + tag
