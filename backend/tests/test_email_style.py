"""Craft rules shared by reference drafting and per-lead generation (ADR-0018).

Pure unit tests: no database, no network, no model."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.modules.personalization import draft_prompt
from app.modules.personalization.config_schema import PersonalizationConfig
from app.modules.personalization.email_style import (
    MAX_PARAGRAPH_WORDS,
    max_words_for_reference,
    shape_codes,
    step_brief,
)
from app.modules.personalization.fake_model import FakeModel
from app.modules.personalization.ports import (
    DraftedStep,
    Fact,
    GenerationOutput,
    GenerationRequest,
    SequenceDraftOutput,
    SequenceDraftRequest,
    StepBlueprint,
)
from app.modules.personalization.prompt_builder import build_chat_messages, build_data
from app.modules.personalization.reference_validator import (
    DraftContext,
    validate_draft,
)
from app.modules.personalization.validator import (
    CODE_GUIDANCE,
    ValidationContext,
    validate_generation,
)

CONFIG = {
    "objective": "Book a demo with SaaS founders",
    "offer": "We help SaaS teams automate manual outbound prospecting",
    "cta": "Would you be open to a quick conversation?",
    "target": "SaaS founders",
    "problem_solved": "Manual outbound work",
    "tone": "friendly",
    "must_mention": [],
    "never_say": [],
}
GOOD_PARAGRAPHS = (
    "Hi Sarah,",
    "Most sales leads at growing SaaS teams lose hours to manual prospecting.",
    "We help SaaS teams automate manual outbound prospecting, so reps spend "
    "that time talking to buyers.",
    "Would you be open to a quick conversation?",
)


class TestStepBrief:
    def test_every_email_gets_a_different_job(self) -> None:
        jobs = [step_brief(p, 5).job for p in range(1, 6)]
        assert jobs == ["intro", "new_angle", "proof", "insight", "close"]

    def test_a_two_email_sequence_has_no_closing_note(self) -> None:
        assert [step_brief(p, 2).job for p in (1, 2)] == ["intro", "new_angle"]

    def test_the_first_email_is_under_about_a_hundred_words(self) -> None:
        assert step_brief(1, 4).max_words <= 100
        assert step_brief(2, 4).max_words < step_brief(1, 4).max_words

    def test_a_per_lead_email_may_not_grow_far_beyond_its_reference(self) -> None:
        assert max_words_for_reference(80) == 108
        assert max_words_for_reference(10) == 60  # a floor for very short references


class TestShapeCodes:
    def test_a_well_built_email_has_no_problems(self) -> None:
        assert shape_codes(GOOD_PARAGRAPHS) == []

    def test_one_long_paragraph_is_a_wall_of_text(self) -> None:
        wall = ("Hi Sarah,", " ".join(["word"] * 70) + "?")
        codes = shape_codes(wall)
        assert "paragraph_too_dense" in codes and "too_few_paragraphs" in codes

    def test_the_ask_must_be_a_question(self) -> None:
        statement = (*GOOD_PARAGRAPHS[:-1], "Let's book a call this week.")
        assert shape_codes(statement) == ["ask_not_question"]

    def test_a_sign_off_or_ps_after_the_ask_is_fine(self) -> None:
        assert shape_codes((*GOOD_PARAGRAPHS, "Best,\nJohn")) == []
        assert shape_codes((*GOOD_PARAGRAPHS, "Best,\nJohn", "P.S. No rush.")) == []

    @pytest.mark.parametrize(
        "opener",
        [
            "I hope this email finds you well.",
            "I'm reaching out because we help teams.",
            "My name is John and I run sales.",
            "We are a leading provider of tools.",
        ],
    )
    def test_pleasantry_and_self_introduction_openers_are_refused(
        self, opener: str
    ) -> None:
        paragraphs = (GOOD_PARAGRAPHS[0], opener, *GOOD_PARAGRAPHS[2:])
        assert "generic_opener" in shape_codes(paragraphs)

    def test_a_very_short_note_may_be_two_paragraphs(self) -> None:
        assert shape_codes(("Hi Sarah,", "Should I close this out?")) == []

    def test_every_code_has_guidance_for_the_model(self) -> None:
        for code in ("paragraph_too_dense", "too_few_paragraphs"):
            assert CODE_GUIDANCE[code]
            assert draft_prompt.CODE_GUIDANCE[code]


class TestPerLeadValidator:
    def _ctx(self) -> ValidationContext:
        text = "\n\n".join(GOOD_PARAGRAPHS)
        return ValidationContext(
            objective=PersonalizationConfig.model_validate(CONFIG),
            reference_subject="Quick idea",
            reference_text=text,
            reference_urls=frozenset(),
            facts={"F1": Fact("F1", "LEAD", "Company: Acme")},
        )

    def _output(self, paragraphs) -> GenerationOutput:
        return GenerationOutput(
            subject="Quick idea",
            paragraphs=tuple(paragraphs),
            facts_used=("F1",),
            angle="Acme",
        )

    def test_the_reference_shape_passes(self) -> None:
        result = validate_generation(self._output(GOOD_PARAGRAPHS), self._ctx())
        assert result.codes == ()

    def test_a_dense_unquestioning_email_is_rejected(self) -> None:
        wall = " ".join(["Acme runs outbound manually."] * 12)
        result = validate_generation(
            self._output(("Hi Sarah,", wall, "Book a call.")), self._ctx()
        )
        assert {"paragraph_too_dense", "ask_not_question"} <= set(result.codes)

    def test_the_email_may_not_balloon_past_its_reference(self) -> None:
        # Many short paragraphs: dense and shape rules pass, only the length fails.
        extra = tuple(
            f"Acme point number {i} matters to sales teams." for i in range(14)
        )
        result = validate_generation(
            self._output((*GOOD_PARAGRAPHS[:-1], *extra, GOOD_PARAGRAPHS[-1])),
            self._ctx(),
        )
        assert "body_too_long" in result.codes


class TestPerLeadPrompt:
    def _request(self) -> GenerationRequest:
        return GenerationRequest(
            workspace_ref="ws",
            objective=PersonalizationConfig.model_validate(CONFIG).objective_core(),
            reference_subject="Quick idea",
            reference_paragraphs=GOOD_PARAGRAPHS,
            recipient={"first_name": "Sarah"},
            facts=(Fact("F1", "LEAD", "Company: Acme"),),
            step_position=1,
        )

    def test_the_prompt_teaches_the_structure_and_the_limits_are_data(self) -> None:
        system = build_chat_messages(self._request())[0]["content"]
        for part in ("hook", "value", "ask", "I hope this finds you well"):
            assert part in system
        data = build_data(self._request())
        words = sum(len(p.split()) for p in GOOD_PARAGRAPHS)
        assert data["limits"] == {
            "max_words": max_words_for_reference(words),
            "max_words_per_paragraph": MAX_PARAGRAPH_WORDS,
        }
        json.dumps(data)  # still plain JSON


class TestDraftingPlaybook:
    def _request(self, **kw) -> SequenceDraftRequest:
        base = {
            "workspace_ref": "ws",
            "objective": PersonalizationConfig.model_validate(CONFIG).objective_core(),
            "company": None,
            "steps": tuple(StepBlueprint(i, f"r{i}") for i in range(1, 5)),
            "allowed_variables": ("first_name", "company"),
        }
        base.update(kw)
        return SequenceDraftRequest(**base)

    def test_each_email_is_briefed_with_a_distinct_job_and_length(self) -> None:
        emails = draft_prompt.build_data(self._request())["emails_to_write"]
        assert [e["job"] for e in emails] == [
            "intro",
            "new_angle",
            "proof",
            "close",
        ]
        assert all(e["instruction"] and len(e["word_range"]) == 2 for e in emails)

    def test_regenerating_one_step_keeps_its_place_in_the_sequence(self) -> None:
        request = self._request(
            steps=(StepBlueprint(4, "follow_up_3"),),
            context_emails=({"position": 1}, {"position": 2}, {"position": 3}),
            email_count=4,
        )
        (email,) = draft_prompt.build_data(request)["emails_to_write"]
        assert email["job"] == "close"

    def test_the_fake_draft_is_a_valid_playbook_sequence(self) -> None:
        request = self._request(email_count=4)
        output = FakeModel().default_draft(request)
        verdict = validate_draft(
            output,
            DraftContext(
                objective=PersonalizationConfig.model_validate(CONFIG),
                company=None,
                expected_positions=(1, 2, 3, 4),
                allowed_variables=frozenset({"first_name", "company"}),
            ),
        )
        assert verdict.codes == ()


class TestReferenceValidator:
    def _validate(self, paragraphs, *, position=1, total=3):
        cfg = PersonalizationConfig.model_validate(CONFIG)
        step = DraftedStep(
            position, "r", f"Subject {position}", tuple(paragraphs), "", 3
        )
        positions = tuple(range(1, total + 1))
        steps = []
        for p in positions:
            if p == position:
                steps.append(step)
            else:
                steps.append(
                    replace(
                        step,
                        position=p,
                        subject=f"Other subject {p}",
                        paragraphs=(
                            "Hi {{first_name|there}},",
                            f"Angle number {p} talks about a quite different "
                            f"problem number {p} for revenue teams today.",
                            "Would you be open to a quick conversation?",
                        ),
                    )
                )
        return validate_draft(
            SequenceDraftOutput("t", tuple(steps)),
            DraftContext(
                objective=cfg,
                company=None,
                expected_positions=positions,
                allowed_variables=frozenset({"first_name", "company"}),
            ),
        )

    def test_a_single_wall_of_text_is_rejected(self) -> None:
        wall = " ".join(["We automate outbound prospecting for SaaS teams."] * 9)
        verdict = self._validate(
            (
                "Hi {{first_name|there}},",
                wall,
                "Would you be open to a quick conversation?",
            )
        )
        assert "paragraph_too_dense" in verdict.codes

    def test_the_closing_note_has_a_tighter_length_than_the_intro(self) -> None:
        long_close = (
            "Hi {{first_name|there}},",
            " ".join(["Timing matters for outbound teams."] * 4),
            " ".join(["Plenty of SaaS teams automate prospecting."] * 4),
            "Would you be open to a quick conversation?",
        )
        assert "body_too_long" in self._validate(long_close, position=3, total=3).codes
        assert (
            "body_too_long" not in self._validate(long_close, position=1, total=3).codes
        )
