from __future__ import annotations

from app.modules.events.adapters.base import ProviderEventAdapter, VerifiedEventEvidence
from app.modules.events.adapters.generic import GenericWebhookAdapter
from app.modules.events.adapters.gmail import GmailEventAdapter
from app.modules.events.adapters.microsoft import MicrosoftGraphEventAdapter

__all__ = [
    "ProviderEventAdapter",
    "VerifiedEventEvidence",
    "GmailEventAdapter",
    "MicrosoftGraphEventAdapter",
    "GenericWebhookAdapter",
]
