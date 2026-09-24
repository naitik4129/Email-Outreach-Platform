from __future__ import annotations

from app.modules.replies.matcher import ReplyMatcher
from app.modules.replies.normalizer import (
    classify_inbound_message,
    extract_rfc_message_ids,
    extract_text_from_html,
    normalize_inbound_message,
)
from app.modules.replies.repository import ReplyRepository
from app.modules.replies.schemas import (
    InboundClassification,
    MailboxSyncResult,
    NormalizedInboundMessage,
    ReplyConfidence,
    ReplyEvidenceType,
    ReplyMatchCandidate,
    ReplyMatchResult,
    SyncCheckpoint,
)
from app.modules.replies.service import ReplySyncService

__all__ = [
    "InboundClassification",
    "MailboxSyncResult",
    "NormalizedInboundMessage",
    "ReplyConfidence",
    "ReplyEvidenceType",
    "ReplyMatchCandidate",
    "ReplyMatchResult",
    "ReplyMatcher",
    "ReplyRepository",
    "ReplySyncService",
    "SyncCheckpoint",
    "classify_inbound_message",
    "extract_rfc_message_ids",
    "extract_text_from_html",
    "normalize_inbound_message",
]
