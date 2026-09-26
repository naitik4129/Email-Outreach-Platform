"""Wires the personalization runtime from Settings (ADR-0012).

Only the personalization worker builds a real model (it alone holds the API
key). Tests never reach the network: constructing the OpenAI adapter with
`app_env == "test"` requires an explicitly injected transport.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from app.core.config import Settings
from app.modules.personalization.budget import RedisRpmLimiter, RpmLimiter
from app.modules.personalization.jit import GenerationRunner, RunnerConfig
from app.modules.personalization.openai_model import OpenAIModel
from app.modules.personalization.ports import PersonalizationModel, ResearchSource
from app.modules.personalization.previews import PreviewRunner
from app.modules.personalization.repository import GenerationDb
from app.modules.personalization.research.cached import CachedWebsiteResearch
from app.modules.personalization.research.egress import SafeFetcher
from app.modules.personalization.research.website import WebsiteResearchSource
from app.modules.personalization.service import PersonalizationService


def build_model(
    settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> PersonalizationModel:
    if settings.app_env == "test" and transport is None:
        raise RuntimeError(
            "The OpenAI adapter must not be constructed in tests without an "
            "injected transport"
        )
    settings.require_personalization_worker_ready()
    return OpenAIModel(
        api_key=settings.personalization_openai_api_key.get_secret_value(),
        model=settings.personalization_model,
        base_url=settings.personalization_openai_base_url,
        timeout_seconds=settings.personalization_request_timeout_seconds,
        max_output_tokens=settings.personalization_max_output_tokens,
        transport=transport,
    )


def build_source(fetcher: SafeFetcher | None = None) -> ResearchSource:
    return WebsiteResearchSource(fetcher or SafeFetcher())


def build_service(
    settings: Settings,
    *,
    db: GenerationDb,
    model: PersonalizationModel | None = None,
    source: ResearchSource | None = None,
) -> PersonalizationService:
    research = CachedWebsiteResearch(
        db=db,
        source=source or build_source(),
        fetch_cap=settings.personalization_daily_fetch_cap,
        enabled=settings.personalization_website_research_enabled,
    )
    return PersonalizationService(
        model=model or build_model(settings),
        research=research,
        min_facts=settings.personalization_min_facts,
    )


def _rpm(settings: Settings, rpm: RpmLimiter | None) -> RpmLimiter:
    return rpm or RedisRpmLimiter(
        redis_url=settings.redis_url, limit_per_minute=settings.personalization_rpm
    )


def build_generation_runner(
    settings: Settings,
    workspace_id: UUID,
    *,
    session_factory: Any,
    model: PersonalizationModel | None = None,
    source: ResearchSource | None = None,
    rpm: RpmLimiter | None = None,
) -> GenerationRunner:
    db = GenerationDb(session_factory, workspace_id)
    return GenerationRunner(
        db=db,
        service=build_service(settings, db=db, model=model, source=source),
        rpm=_rpm(settings, rpm),
        config=RunnerConfig.from_settings(settings),
    )


def build_preview_runner(
    settings: Settings,
    workspace_id: UUID,
    *,
    session_factory: Any,
    model: PersonalizationModel | None = None,
    source: ResearchSource | None = None,
    rpm: RpmLimiter | None = None,
) -> PreviewRunner:
    db = GenerationDb(session_factory, workspace_id)
    return PreviewRunner(
        db=db,
        service=build_service(settings, db=db, model=model, source=source),
        rpm=_rpm(settings, rpm),
        max_attempts=settings.personalization_max_attempts,
        daily_preview_cap=settings.personalization_daily_preview_cap,
    )
