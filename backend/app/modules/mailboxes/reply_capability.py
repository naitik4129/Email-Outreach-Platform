from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from app.modules.mailboxes.providers.imap_sync import imap_configured


class ReplySyncStatus(StrEnum):
    ENABLED = "ENABLED"
    # OAuth mailbox connected before the read scope was requested: it must be
    # reconnected and re-consented; syncing would only produce 403s.
    RECONNECT_REQUIRED = "RECONNECT_REQUIRED"
    # SMTP mailbox without IMAP settings: SMTP alone cannot receive mail.
    IMAP_NOT_CONFIGURED = "IMAP_NOT_CONFIGURED"
    UNSUPPORTED = "UNSUPPORTED"


_GMAIL_READ_SCOPES = ("/gmail.readonly", "/gmail.modify")
_GMAIL_FULL_ACCESS = "https://mail.google.com/"
_GRAPH_READ_SCOPES = ("mail.read", "mail.readwrite")


def _scope_list(granted_scopes: Any) -> list[str]:
    if not granted_scopes:
        return []
    if isinstance(granted_scopes, str):
        try:
            parsed = json.loads(granted_scopes)
        except ValueError:
            return granted_scopes.split()
        return _scope_list(parsed)
    return [str(scope) for scope in granted_scopes]


def reply_sync_status(
    provider: str,
    granted_scopes: Any,
    protected_config: Mapping[str, Any] | None,
) -> ReplySyncStatus:
    """Whether a mailbox's stored connection can actually read inbound mail.

    The single place that decides reply-sync eligibility, so the scheduler,
    the sync service and the mailbox UI cannot disagree.
    """
    name = (provider or "").upper()
    scopes = [s.strip() for s in _scope_list(granted_scopes)]
    if name == "GMAIL":
        ok = any(
            s == _GMAIL_FULL_ACCESS or s.endswith(_GMAIL_READ_SCOPES) for s in scopes
        )
        return ReplySyncStatus.ENABLED if ok else ReplySyncStatus.RECONNECT_REQUIRED
    if name == "MICROSOFT":
        ok = any(s.lower().rsplit("/", 1)[-1] in _GRAPH_READ_SCOPES for s in scopes)
        return ReplySyncStatus.ENABLED if ok else ReplySyncStatus.RECONNECT_REQUIRED
    if name == "SMTP":
        if protected_config and imap_configured(protected_config):
            return ReplySyncStatus.ENABLED
        return ReplySyncStatus.IMAP_NOT_CONFIGURED
    return ReplySyncStatus.UNSUPPORTED
