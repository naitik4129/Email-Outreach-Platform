"""Deterministic model used by tests and local development (ADR-0012).

It never touches the network. Responses can be scripted (including raising a
`ModelError`) so failure paths -- timeout, malformed output, hallucinated facts,
prompt injection echoes -- are exercised without a provider.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.modules.personalization.ports import (
    GenerationOutput,
    GenerationRequest,
    GenerationResult,
    ModelError,
)

Scripted = (
    GenerationOutput | ModelError | Callable[[GenerationRequest], GenerationOutput]
)


class FakeModel:
    provider = "fake"
    model = "fake-model"

    def __init__(self, script: Sequence[Scripted] | None = None) -> None:
        self._script = list(script or [])
        self.requests: list[GenerationRequest] = []

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
