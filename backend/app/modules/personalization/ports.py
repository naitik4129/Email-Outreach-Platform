"""Ports and value types of the personalization pipeline (ADR-0012/0013).

The pipeline talks to a language model and to research sources only through
these interfaces, so tests use deterministic fakes and no test can reach the
network. Nothing here carries secrets.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

FactSource = Literal["LEAD", "WEBSITE"]


@dataclass(frozen=True)
class Fact:
    """One piece of information the model may build the email on."""

    id: str
    source: FactSource
    text: str


@dataclass(frozen=True)
class PreviousEmail:
    """What the recipient was already sent, for a follow-up."""

    subject: str
    body_text: str
    angle: str | None
    fact_ids_used: tuple[str, ...]
    facts_used: tuple[str, ...]
    days_since_sent: int


@dataclass(frozen=True)
class GenerationRequest:
    """Everything the model is given. Contains lead data, so it must never be
    logged or placed in a task payload."""

    workspace_ref: str  # opaque hash, never an address
    objective: Mapping[str, object]
    reference_subject: str
    reference_paragraphs: tuple[str, ...]
    recipient: Mapping[str, str]
    facts: tuple[Fact, ...]
    step_position: int
    previous: PreviousEmail | None = None
    # Validator failure codes from the previous attempt (codes only, never the
    # rejected output).
    retry_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class GenerationOutput:
    subject: str
    paragraphs: tuple[str, ...]
    facts_used: tuple[str, ...]
    angle: str


@dataclass(frozen=True)
class GenerationResult:
    output: GenerationOutput
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


class ModelError(Exception):
    """Base class. `code` is a short stable token that is safe to store/log;
    the message never contains prompts, outputs or credentials."""

    code = "model_error"
    # Retry classification used by the generation runner.
    transient = False
    permanent = False

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message or self.code)
        if code:
            self.code = code


class ModelTimeout(ModelError):
    code = "model_timeout"
    transient = True


class ModelRateLimited(ModelError):
    code = "model_rate_limited"
    transient = True

    def __init__(
        self, message: str = "", *, retry_after_seconds: float | None = None
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ModelTransientError(ModelError):
    code = "model_unavailable"
    transient = True


class ModelPermanentError(ModelError):
    """Authentication, billing, unknown model or malformed request: retrying the
    same call cannot help and an operator must act."""

    code = "model_configuration_error"
    permanent = True


class ModelRefusal(ModelError):
    """The model declined to answer. Counts as a rejected attempt."""

    code = "model_refusal"


class MalformedOutput(ModelError):
    """Not valid JSON / not the requested schema / truncated. Counts as a
    rejected attempt."""

    code = "malformed_output"


class PersonalizationModel(Protocol):
    provider: str
    model: str

    def generate(self, request: GenerationRequest) -> GenerationResult: ...


@dataclass(frozen=True)
class ResearchDocument:
    """Bounded, already-sanitized text from a research source."""

    source: str
    url: str
    text: str
    status: Literal["OK", "EMPTY", "BLOCKED", "ERROR"] = "OK"
    snippets: tuple[str, ...] = field(default_factory=tuple)


class ResearchSource(Protocol):
    def fetch(self, url: str) -> ResearchDocument: ...


@dataclass(frozen=True)
class ResearchOutcome:
    """What the generation pipeline learns from research. `status` is one of
    NONE (no URL), OK, EMPTY, BLOCKED, ERROR or SKIPPED (budget/disabled).
    Research is best effort: anything but OK just means fewer facts."""

    status: str
    snippets: tuple[str, ...] = field(default_factory=tuple)
    source_url: str | None = None
