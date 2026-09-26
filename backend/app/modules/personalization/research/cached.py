"""Per-workspace cached website research (ADR-0013).

Cache reads/writes and the budget reservation each use their own short
transaction; the network fetch runs with no database transaction open. The
cache is keyed by workspace, so one tenant's fetched text is never served to
another.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from app.modules.personalization.ports import ResearchOutcome, ResearchSource
from app.modules.personalization.research.website import (
    normalize_company_url,
    snippets_from_text,
)

logger = logging.getLogger(__name__)

POSITIVE_TTL_SECONDS = 7 * 24 * 3600
NEGATIVE_TTL_SECONDS = 3600
SOURCE = "WEBSITE"


def url_hash(normalized_url: str) -> str:
    return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()


class CachedWebsiteResearch:
    def __init__(
        self,
        *,
        db: Any,  # GenerationDb-like: .transaction() -> repo context manager
        source: ResearchSource,
        fetch_cap: int,
        enabled: bool = True,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        self._db = db
        self._source = source
        self._fetch_cap = fetch_cap
        self._enabled = enabled
        self._today = today

    def research(
        self, *, workspace_id: UUID, company_website: str | None
    ) -> ResearchOutcome:
        if not self._enabled:
            return ResearchOutcome(status="SKIPPED")
        url = normalize_company_url(company_website or "")
        if url is None:
            return ResearchOutcome(status="NONE")
        key = url_hash(url)

        with self._db.transaction() as repo:
            cached = repo.get_cache(
                workspace_id=workspace_id, source=SOURCE, url_hash=key
            )
        if cached is not None:
            return self._outcome(cached["status"], cached["extracted_text"], url)

        with self._db.transaction() as repo:
            reserved = repo.reserve_usage(
                workspace_id=workspace_id,
                day=self._today(),
                kind="FETCH",
                cap=self._fetch_cap,
            )
        if not reserved:
            return ResearchOutcome(status="SKIPPED", source_url=url)

        try:
            document = self._source.fetch(url)
            status = document.status
            text_value = document.text if status == "OK" else None
        except Exception:  # best effort: research must never fail a message
            logger.warning(
                "Website research failed unexpectedly",
                extra={"workspace_id": str(workspace_id)},
            )
            status, text_value = "ERROR", None

        ttl = POSITIVE_TTL_SECONDS if status == "OK" else NEGATIVE_TTL_SECONDS
        try:
            with self._db.transaction() as repo:
                repo.upsert_cache(
                    workspace_id=workspace_id,
                    source=SOURCE,
                    url_hash=key,
                    normalized_url=url,
                    status=status,
                    extracted_text=text_value,
                    content_sha256=(
                        hashlib.sha256(text_value.encode("utf-8")).hexdigest()
                        if text_value
                        else None
                    ),
                    http_status=None,
                    ttl_seconds=ttl,
                )
        except Exception:
            logger.warning(
                "Could not cache research", extra={"workspace_id": str(workspace_id)}
            )
        return self._outcome(status, text_value, url)

    @staticmethod
    def _outcome(status: str, text_value: str | None, url: str) -> ResearchOutcome:
        if status == "OK" and text_value:
            return ResearchOutcome(
                status="OK", snippets=snippets_from_text(text_value), source_url=url
            )
        return ResearchOutcome(status=status, source_url=url)
