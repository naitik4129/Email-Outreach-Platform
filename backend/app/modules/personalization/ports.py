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
class PageText:
    """Bounded, already-sanitized text of one company web page (untrusted)."""

    label: str  # "home" or the path without its query string
    title: str = ""
    meta_description: str = ""
    headings: tuple[str, ...] = ()
    nav_labels: tuple[str, ...] = ()
    blocks: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompanyAnalysisRequest:
    """Input for understanding a company (ADR-0016). Carries public website text
    or text the user typed, never lead data."""

    workspace_ref: str
    source: Literal["WEBSITE", "MANUAL"]
    company_name: str = ""
    description: str = ""  # MANUAL only
    pages: tuple[PageText, ...] = ()  # WEBSITE only


@dataclass(frozen=True)
class CompanyAnalysisOutput:
    company_name: str
    description: str
    services: tuple[str, ...]
    industries: tuple[str, ...]
    target_audience: str
    tone_of_voice: str
    key_messages: tuple[str, ...]
    suggested_objective: str
    suggested_offer: str
    suggested_cta: str


@dataclass(frozen=True)
class CompanyAnalysisResult:
    output: CompanyAnalysisOutput
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


@dataclass(frozen=True)
class StepBlueprint:
    position: int  # 1-based position among the EMAIL steps
    role: str  # e.g. "initial", "follow_up_1"


@dataclass(frozen=True)
class SequenceDraftRequest:
    """Input for drafting reference emails. Objective and company text only."""

    workspace_ref: str
    objective: Mapping[str, object]  # the original objective fields only
    company: Mapping[str, object] | None
    steps: tuple[StepBlueprint, ...]  # the emails to write
    allowed_variables: tuple[str, ...]
    # STEP mode: the neighbouring emails, so a regenerated step fits the sequence.
    context_emails: tuple[Mapping[str, object], ...] = ()
    # STEP mode, only when the user asked for specific changes: what they wrote and
    # the email as it stands now, so the change is applied to it.
    user_instructions: str | None = None
    current_email: Mapping[str, object] | None = None
    retry_codes: tuple[str, ...] = ()
    # Emails in the whole sequence (0 = steps + context_emails); the length
    # decides which email is the closing one.
    email_count: int = 0


@dataclass(frozen=True)
class DraftedStep:
    position: int
    role: str
    subject: str
    paragraphs: tuple[str, ...]
    preheader: str
    wait_days_after: int


@dataclass(frozen=True)
class SequenceDraftOutput:
    theme: str
    steps: tuple[DraftedStep, ...]


@dataclass(frozen=True)
class SequenceDraftResult:
    output: SequenceDraftOutput
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


class DraftingModel(Protocol):
    """Interactive authoring calls made from the API process (ADR-0016)."""

    provider: str
    model: str

    def analyze_company(
        self, request: CompanyAnalysisRequest
    ) -> CompanyAnalysisResult: ...

    def draft_sequence(self, request: SequenceDraftRequest) -> SequenceDraftResult: ...


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
