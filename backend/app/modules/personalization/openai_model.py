"""OpenAI adapter for the PersonalizationModel and DraftingModel ports
(ADR-0012, ADR-0016).

Raw `httpx` on purpose: one endpoint, exact control over timeouts, no proxy
(`trust_env=False`), no hidden client-side retries, and provider errors mapped
into the port's taxonomy. Prompts, outputs and the API key are never logged or
put into exception messages -- only a status code and the provider's short error
code are kept.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.modules.personalization import analysis_prompt, draft_prompt
from app.modules.personalization.ports import (
    CompanyAnalysisOutput,
    CompanyAnalysisRequest,
    CompanyAnalysisResult,
    DraftedStep,
    GenerationOutput,
    GenerationRequest,
    GenerationResult,
    MalformedOutput,
    ModelPermanentError,
    ModelRateLimited,
    ModelRefusal,
    ModelTimeout,
    ModelTransientError,
    SequenceDraftOutput,
    SequenceDraftRequest,
    SequenceDraftResult,
)
from app.modules.personalization.prompt_builder import (
    OUTPUT_SCHEMA,
    build_chat_messages,
)

_SAFE_CODE_RE = re.compile(r"[^a-z0-9_]")
# Provider error codes that mean "billing/quota", which retrying cannot fix.
_PERMANENT_429_CODES = frozenset({"insufficient_quota", "billing_hard_limit_reached"})


def _safe_code(value: object) -> str:
    return _SAFE_CODE_RE.sub("", str(value or "").lower())[:64]


@dataclass(frozen=True)
class _Completion:
    content: object
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class OpenAIModel:
    provider = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
        max_output_tokens: int = 1200,
        draft_max_output_tokens: int = 4000,
        analysis_max_output_tokens: int = 1500,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ModelPermanentError(
                "OpenAI API key is not configured", code="missing_api_key"
            )
        if not model:
            raise ModelPermanentError("Model is not configured", code="missing_model")
        self.model = model
        self._max_output_tokens = max_output_tokens
        self._draft_max_output_tokens = draft_max_output_tokens
        self._analysis_max_output_tokens = analysis_max_output_tokens
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
            # Never inherit proxy/CA settings from the environment (ADR-0012).
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _complete(
        self,
        *,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        max_tokens: int,
        workspace_ref: str,
    ) -> _Completion:
        """One chat-completion call with a strict JSON schema. Shared by every
        model call so transport settings, error mapping and secret hygiene exist
        once. Raises the port's ModelError taxonomy; never puts prompts, outputs
        or the key into an exception."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_schema", "json_schema": schema},
            "max_completion_tokens": max_tokens,
            "store": False,
            # Opaque per-workspace hash for provider abuse monitoring; never an address.
            "user": hashlib.sha256(workspace_ref.encode("utf-8")).hexdigest()[:32],
        }
        started = time.monotonic()
        try:
            response = self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise ModelTimeout("model request timed out") from exc
        except httpx.HTTPError as exc:
            raise ModelTransientError(
                "model request failed", code="model_network_error"
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        if response.status_code != 200:
            raise self._map_error(response)

        try:
            body = response.json()
            choice = body["choices"][0]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise MalformedOutput("provider response was not understood") from exc

        message = choice.get("message") or {}
        if message.get("refusal"):
            raise ModelRefusal("model declined to answer")
        if choice.get("finish_reason") == "length":
            raise MalformedOutput("model output was truncated", code="output_truncated")

        usage = body.get("usage") or {}
        return _Completion(
            content=message.get("content"),
            model=str(body.get("model") or self.model)[:128],
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=latency_ms,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        completion = self._complete(
            messages=build_chat_messages(request),
            schema=OUTPUT_SCHEMA,
            max_tokens=self._max_output_tokens,
            workspace_ref=request.workspace_ref,
        )
        return GenerationResult(
            output=self._parse_output(completion.content),
            model=completion.model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            latency_ms=completion.latency_ms,
        )

    def analyze_company(self, request: CompanyAnalysisRequest) -> CompanyAnalysisResult:
        completion = self._complete(
            messages=analysis_prompt.build_chat_messages(request),
            schema=analysis_prompt.OUTPUT_SCHEMA,
            max_tokens=self._analysis_max_output_tokens,
            workspace_ref=request.workspace_ref,
        )
        return CompanyAnalysisResult(
            output=self._parse_analysis(completion.content),
            model=completion.model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            latency_ms=completion.latency_ms,
        )

    def draft_sequence(self, request: SequenceDraftRequest) -> SequenceDraftResult:
        completion = self._complete(
            messages=draft_prompt.build_chat_messages(request),
            schema=draft_prompt.OUTPUT_SCHEMA,
            max_tokens=self._draft_max_output_tokens,
            workspace_ref=request.workspace_ref,
        )
        return SequenceDraftResult(
            output=self._parse_draft(completion.content),
            model=completion.model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            latency_ms=completion.latency_ms,
        )

    @staticmethod
    def _load_object(content: object) -> dict[str, Any]:
        if not isinstance(content, str) or not content.strip():
            raise MalformedOutput("empty model output")
        try:
            data = json.loads(content)
        except ValueError as exc:
            raise MalformedOutput("model output was not JSON") from exc
        if not isinstance(data, dict):
            raise MalformedOutput("model output was not an object")
        return data

    @staticmethod
    def _strings(value: object) -> tuple[str, ...]:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise MalformedOutput("model output did not match the schema")
        return tuple(value)

    @classmethod
    def _parse_output(cls, content: object) -> GenerationOutput:
        data = cls._load_object(content)
        subject = data.get("subject")
        paragraphs = data.get("paragraphs")
        facts_used = data.get("facts_used")
        angle = data.get("angle")
        if (
            not isinstance(subject, str)
            or not isinstance(angle, str)
            or not isinstance(paragraphs, list)
            or not isinstance(facts_used, list)
            or not all(isinstance(p, str) for p in paragraphs)
            or not all(isinstance(f, str) for f in facts_used)
        ):
            raise MalformedOutput("model output did not match the schema")
        return GenerationOutput(
            subject=subject,
            paragraphs=tuple(paragraphs),
            facts_used=tuple(facts_used),
            angle=angle,
        )

    @classmethod
    def _parse_analysis(cls, content: object) -> CompanyAnalysisOutput:
        data = cls._load_object(content)
        text_fields = (
            "company_name",
            "description",
            "target_audience",
            "tone_of_voice",
            "suggested_objective",
            "suggested_offer",
            "suggested_cta",
        )
        if not all(isinstance(data.get(name), str) for name in text_fields):
            raise MalformedOutput("model output did not match the schema")
        return CompanyAnalysisOutput(
            company_name=data["company_name"],
            description=data["description"],
            services=cls._strings(data.get("services")),
            industries=cls._strings(data.get("industries")),
            target_audience=data["target_audience"],
            tone_of_voice=data["tone_of_voice"],
            key_messages=cls._strings(data.get("key_messages")),
            suggested_objective=data["suggested_objective"],
            suggested_offer=data["suggested_offer"],
            suggested_cta=data["suggested_cta"],
        )

    @classmethod
    def _parse_draft(cls, content: object) -> SequenceDraftOutput:
        data = cls._load_object(content)
        theme = data.get("theme")
        emails = data.get("emails")
        if not isinstance(theme, str) or not isinstance(emails, list):
            raise MalformedOutput("model output did not match the schema")
        steps: list[DraftedStep] = []
        for item in emails:
            if not isinstance(item, dict):
                raise MalformedOutput("model output did not match the schema")
            position = item.get("position")
            wait = item.get("wait_days_after")
            if (
                not isinstance(position, int)
                or isinstance(position, bool)
                or not isinstance(wait, int)
                or isinstance(wait, bool)
                or not all(
                    isinstance(item.get(name), str)
                    for name in ("role", "subject", "preheader")
                )
            ):
                raise MalformedOutput("model output did not match the schema")
            steps.append(
                DraftedStep(
                    position=position,
                    role=item["role"],
                    subject=item["subject"],
                    paragraphs=cls._strings(item.get("paragraphs")),
                    preheader=item["preheader"],
                    wait_days_after=wait,
                )
            )
        return SequenceDraftOutput(theme=theme, steps=tuple(steps))

    @staticmethod
    def _map_error(response: httpx.Response) -> Exception:
        status = response.status_code
        error_code = ""
        try:
            error = response.json().get("error") or {}
            error_code = _safe_code(error.get("code") or error.get("type"))
        except (ValueError, AttributeError):
            pass
        if status == 429:
            if error_code in _PERMANENT_429_CODES:
                return ModelPermanentError(
                    "provider quota exhausted", code="provider_quota"
                )
            retry_after: float | None = None
            try:
                retry_after = float(response.headers.get("retry-after", ""))
            except ValueError:
                retry_after = None
            return ModelRateLimited(
                "provider rate limited", retry_after_seconds=retry_after
            )
        if status == 408:
            return ModelTimeout("provider timed out")
        if status >= 500:
            return ModelTransientError(
                f"provider error {status}", code="model_unavailable"
            )
        # 400/401/403/404 and friends: bad key, bad model, bad request.
        return ModelPermanentError(
            f"provider rejected the request ({status})",
            code=f"provider_{status}",
        )
