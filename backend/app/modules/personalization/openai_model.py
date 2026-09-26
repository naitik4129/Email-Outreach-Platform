"""OpenAI adapter for the PersonalizationModel port (ADR-0012).

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
from typing import Any

import httpx

from app.modules.personalization.ports import (
    GenerationOutput,
    GenerationRequest,
    GenerationResult,
    MalformedOutput,
    ModelPermanentError,
    ModelRateLimited,
    ModelRefusal,
    ModelTimeout,
    ModelTransientError,
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

    def generate(self, request: GenerationRequest) -> GenerationResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": build_chat_messages(request),
            "response_format": {"type": "json_schema", "json_schema": OUTPUT_SCHEMA},
            "max_completion_tokens": self._max_output_tokens,
            "store": False,
            # Opaque per-workspace hash for provider abuse monitoring; never an address.
            "user": hashlib.sha256(request.workspace_ref.encode("utf-8")).hexdigest()[
                :32
            ],
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

        output = self._parse_output(message.get("content"))
        usage = body.get("usage") or {}
        return GenerationResult(
            output=output,
            model=str(body.get("model") or self.model)[:128],
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=latency_ms,
        )

    @staticmethod
    def _parse_output(content: object) -> GenerationOutput:
        if not isinstance(content, str) or not content.strip():
            raise MalformedOutput("empty model output")
        try:
            data = json.loads(content)
        except ValueError as exc:
            raise MalformedOutput("model output was not JSON") from exc
        if not isinstance(data, dict):
            raise MalformedOutput("model output was not an object")
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
