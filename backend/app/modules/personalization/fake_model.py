"""Deterministic model used by tests and local development (ADR-0012).

It never touches the network. Responses can be scripted (including raising a
`ModelError`) so failure paths -- timeout, malformed output, hallucinated facts,
prompt injection echoes -- are exercised without a provider. It implements both
the per-lead `PersonalizationModel` port and the `DraftingModel` port (ADR-0016).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.modules.personalization.ports import (
    CompanyAnalysisOutput,
    CompanyAnalysisRequest,
    CompanyAnalysisResult,
    DraftedStep,
    GenerationOutput,
    GenerationRequest,
    GenerationResult,
    ModelError,
    SequenceDraftOutput,
    SequenceDraftRequest,
    SequenceDraftResult,
)

Scripted = (
    GenerationOutput | ModelError | Callable[[GenerationRequest], GenerationOutput]
)
ScriptedAnalysis = (
    CompanyAnalysisOutput
    | ModelError
    | Callable[[CompanyAnalysisRequest], CompanyAnalysisOutput]
)
ScriptedDraft = (
    SequenceDraftOutput
    | ModelError
    | Callable[[SequenceDraftRequest], SequenceDraftOutput]
)

# Distinct vocabulary per email so consecutive default drafts do not look like
# repeats to the validator. No digits, no links.
_ANGLES: tuple[tuple[str, str], ...] = (
    (
        "Quick idea for {{company|your team}}",
        "Teams like yours often spend their best hours on repetitive groundwork "
        "instead of conversations that move deals forward, and a lighter process "
        "gives people room to focus on customers.",
    ),
    (
        "A different angle",
        "Another way to look at it is the cost of waiting: every quiet week means "
        "prospects settle on somebody else, so a small change now compounds later "
        "into noticeably steadier pipeline for your account executives.",
    ),
    (
        "One more thought",
        "Companies that adopted this approach describe calmer planning, clearer "
        "handoffs between marketing and sales, and fewer surprises when quarterly "
        "targets come around, which is the outcome we aim to support.",
    ),
    (
        "Closing the loop",
        "Perhaps the timing is simply off, and that is completely fine, so I will "
        "keep this brief and leave the door open for whenever priorities shift "
        "toward growth again.",
    ),
    (
        "A last idea",
        "Before I step back, here is a practical starting point that asks very "
        "little of your schedule and still shows quickly whether this belongs on "
        "your roadmap this season.",
    ),
)


class FakeModel:
    provider = "fake"
    model = "fake-model"

    def __init__(
        self,
        script: Sequence[Scripted] | None = None,
        *,
        analysis_script: Sequence[ScriptedAnalysis] | None = None,
        draft_script: Sequence[ScriptedDraft] | None = None,
    ) -> None:
        self._script = list(script or [])
        self._analysis_script = list(analysis_script or [])
        self._draft_script = list(draft_script or [])
        self.requests: list[GenerationRequest] = []
        self.analysis_requests: list[CompanyAnalysisRequest] = []
        self.draft_requests: list[SequenceDraftRequest] = []

    @property
    def calls(self) -> int:
        return len(self.requests)

    def default_output(self, request: GenerationRequest) -> GenerationOutput:
        """A valid, grounded email: greeting, one fact-based opening, then the
        reference paragraphs verbatim (so offer/CTA/must-mention are preserved)."""
        first_name = request.recipient.get("first_name", "there")
        reference = list(request.reference_paragraphs)
        body: list[str] = []
        used: list[str] = []
        if request.facts:
            fact = request.facts[0]
            used = [fact.id]
            body.append(f"Hi {first_name},")
            body.append(f"I noticed this about your work: {fact.text}.")
            reference = [
                p for p in reference if not p.lower().startswith(("hi ", "hello "))
            ]
        body.extend(reference)
        subject = request.reference_subject
        if request.previous is not None:
            subject = f"Another idea: {request.reference_subject}"
        return GenerationOutput(
            subject=subject,
            paragraphs=tuple(body),
            facts_used=tuple(used),
            angle="fake personalization",
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        step = self._script.pop(0) if self._script else None
        if isinstance(step, ModelError):
            raise step
        if callable(step):
            output = step(request)
        elif step is not None:
            output = step
        else:
            output = self.default_output(request)
        return GenerationResult(
            output=output,
            model=self.model,
            input_tokens=100,
            output_tokens=80,
            latency_ms=5,
        )

    # -- DraftingModel -------------------------------------------------------

    def default_analysis(
        self, request: CompanyAnalysisRequest
    ) -> CompanyAnalysisOutput:
        if request.source == "MANUAL":
            name = request.company_name
            description = request.description.strip()
        else:
            first = request.pages[0] if request.pages else None
            name = (
                request.company_name
                or (first.title if first else "")
                or ("Your company")
            )
            description = (first.meta_description if first else "") or ""
        first_sentence = description.split(".")[0].strip()
        return CompanyAnalysisOutput(
            company_name=name,
            description=description[:400],
            services=(first_sentence[:60],) if first_sentence else (),
            industries=(),
            target_audience="",
            tone_of_voice="clear and friendly",
            key_messages=(),
            suggested_objective=f"Start conversations about {name}",
            suggested_offer=first_sentence[:200] or f"What {name} offers",
            suggested_cta="Would you be open to a short conversation?",
        )

    def analyze_company(self, request: CompanyAnalysisRequest) -> CompanyAnalysisResult:
        self.analysis_requests.append(request)
        step = self._analysis_script.pop(0) if self._analysis_script else None
        if isinstance(step, ModelError):
            raise step
        if callable(step):
            output = step(request)
        elif step is not None:
            output = step
        else:
            output = self.default_analysis(request)
        return CompanyAnalysisResult(
            output=output,
            model=self.model,
            input_tokens=200,
            output_tokens=150,
            latency_ms=5,
        )

    def default_draft(self, request: SequenceDraftRequest) -> SequenceDraftOutput:
        """A valid sequence: every email keeps the offer, CTA and must-mention
        phrases, uses only fallback-carrying variables, and has its own subject
        and wording."""
        objective = request.objective
        offer = str(objective.get("offer", "")).strip()
        cta = str(objective.get("cta", "")).strip()
        must = [str(p) for p in objective.get("must_mention", []) or []]  # type: ignore[union-attr]
        steps: list[DraftedStep] = []
        last = len(request.steps) - 1
        for index, blueprint in enumerate(request.steps):
            subject, angle = _ANGLES[min(blueprint.position - 1, len(_ANGLES) - 1)]
            paragraphs = [
                "Hi {{first_name|there}},",
                f"{angle}",
                f"What we offer: {offer}." if offer else "",
                f"{'; '.join(must)}." if must else "",
                cta,
            ]
            steps.append(
                DraftedStep(
                    position=blueprint.position,
                    role=blueprint.role,
                    subject=subject,
                    paragraphs=tuple(p for p in paragraphs if p),
                    preheader="",
                    wait_days_after=0 if index == last else 3,
                )
            )
        return SequenceDraftOutput(theme="fake theme", steps=tuple(steps))

    def draft_sequence(self, request: SequenceDraftRequest) -> SequenceDraftResult:
        self.draft_requests.append(request)
        step = self._draft_script.pop(0) if self._draft_script else None
        if isinstance(step, ModelError):
            raise step
        if callable(step):
            output = step(request)
        elif step is not None:
            output = step
        else:
            output = self.default_draft(request)
        return SequenceDraftResult(
            output=output,
            model=self.model,
            input_tokens=300,
            output_tokens=500,
            latency_ms=5,
        )
