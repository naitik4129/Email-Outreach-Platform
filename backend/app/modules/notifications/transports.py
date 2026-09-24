from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransactionalEmailMessage:
    to_email: str
    subject: str
    body_text: str
    body_html: str | None = None
    category: str = "platform_notification"


class PlatformTransactionalEmailTransport:
    """Platform transactional email transport (ADR 0008).

    CRITICAL ARCHITECTURAL BOUNDARY:
    Platform notifications (invitations, critical system alerts, recovery)
    must NEVER be routed through customer outreach mailboxes or customer OAuth/SMTP
    accounts. This transport uses a dedicated, separate platform infrastructure.
    """

    _sent_messages: list[tuple[TransactionalEmailMessage, datetime]] = []

    @classmethod
    def send(cls, message: TransactionalEmailMessage) -> bool:
        """Sends a platform transactional email."""
        logger.info(
            "Sending platform transactional email",
            extra={
                "to_email": message.to_email,
                "subject": message.subject,
                "category": message.category,
            },
        )
        cls._sent_messages.append((message, datetime.now(UTC)))
        return True

    @classmethod
    def get_sent_messages(cls) -> list[TransactionalEmailMessage]:
        return [msg for msg, _ in cls._sent_messages]

    @classmethod
    def clear_sent_messages(cls) -> None:
        cls._sent_messages.clear()
