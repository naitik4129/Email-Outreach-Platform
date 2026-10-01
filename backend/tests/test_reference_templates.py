"""AI drafting of reference emails: planning, validation/retry, all-or-nothing
writes, concurrency, connection release and error mapping (ADR-0016). The model
is the deterministic fake; sequence writes go to a recording fake; the database
session is an in-memory SQLite one, so nothing touches a network or Postgres."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.config import Settings
from app.core.errors import AppError
from app.modules.campaigns.schemas import SequenceOut
from app.modules.personalization.config_schema import PersonalizationConfig
from app.modules.personalization.fake_model import FakeModel
from app.modules.personalization.ports import (
    DraftedStep,
    MalformedOutput,
    ModelPermanentError,
    ModelRateLimited,
    ModelRefusal,
    ModelTimeout,
    ModelTransientError,
    SequenceDraftOutput,
)
from app.modules.personalization.reference_templates import (
    ReferenceTemplateService,
    role_for,
)
from app.modules.templates.variables import validate_template_content
from tests.support.personalization_fakes import CONFIG, WS

CAMPAIGN_ID = uuid.uuid4()
CTX = WorkspaceContext(workspace_id=WS, user_id=uuid.uuid4(), role_code="MEMBER")
BRAND = {
    "logo_url": "https://acme.example/logo.png",
    "primary": "#123456",
    "accent": "#ff5500",
    "cta_url": "https://acme.example/demo",
    "cta_label": "Book a demo",
}
HTML_CONFIG = {
    **CONFIG,
    "email_format": "HTML",
    "company": {"source": "MANUAL", "company_name": "Acme"},
    "brand": BRAND,
}


def _settings(**kw) -> Settings:
    base = {
        "database_url": "sqlite+pysqlite:///:memory:",
        "redis_url": "redis://x",
        "supabase_url": "https://x.supabase.co",
        "personalization_enabled": True,
        "personalization_model": "test-model",
        "personalization_openai_api_key": "sk-test-not-real",
        "sequence_progression_enabled": True,
    }
    base.update(kw)
    return Settings(**base)


def _email_step(
    position_in_sequence: int, subject: str, body: str, version: int = 1
) -> dict:
    return {
        "id": uuid.uuid4(),
        "kind": "EMAIL",
        "position": position_in_sequence,
        "version": version,
        "email_subject": subject,
        "email_body_html": body,
        "email_preheader": None,
    }


def _wait_step(position_in_sequence: int) -> dict:
    return {
        "id": uuid.uuid4(),
        "kind": "WAIT",
        "position": position_in_sequence,
        "version": 1,
    }


class World:
    """Mutable stand-in for the database state the service reads."""

    def __init__(
        self, config: dict | None = CONFIG, steps: list[dict] | None = None
    ) -> None:
        self.config = config
        self.steps = steps or []
        self.sequence_version = 1
        self.campaign_calls: list[dict] = []
        self.campaign_error: AppError | None = None

    def guard(self, context, campaign_id, **kw):
        self.campaign_calls.append(kw)
        if self.campaign_error:
            raise self.campaign_error
        return {
            "id": campaign_id,
            "status": "DRAFT",
            "campaign_type": "HYPER_PERSONALIZED",
        }

    def read(self, context, campaign_id):
        sequence = {
            "id": uuid.uuid4(),
            "version": self.sequence_version,
            "personalization_config": self.config,
        }
        return sequence, list(self.steps)


class RecordingSequences:
    def __init__(self) -> None:
        self.added: list = []
        self.updated: list = []

    def add_step(self, context, campaign_id, payload):
        self.added.append(payload)

    def update_step(self, context, campaign_id, step_id, payload):
        self.updated.append((step_id, payload))

    def get_sequence(self, context, campaign_id):
        return SequenceOut(
            id=None, campaign_id=campaign_id, revision=1, status="DRAFT", steps=[]
        )


def _service(
    world: World | None = None, model: FakeModel | None = None, settings=None, **kw
):
    world = world or World()
    model = model or FakeModel()
    sequences = RecordingSequences()
    audit = MagicMock()
    session = Session(create_engine("sqlite://"))
    session.commit = MagicMock(wraps=session.commit)  # type: ignore[method-assign]
    service = ReferenceTemplateService(
        session,
        settings or _settings(),
        campaign_guard=world.guard,
        sequence_reader=world.read,
        drafting_model_factory=lambda: model,
        sequence_service=sequences,  # type: ignore[arg-type]
        audit=audit,
        sleep=lambda s: None,
        **kw,
    )
    return SimpleNamespace(
        service=service,
        world=world,
        model=model,
        sequences=sequences,
        audit=audit,
        session=session,
    )


def _generate(env, *, scope="ALL", step_id=None, follow_up_count=None):
    return env.service.generate(
        CTX, CAMPAIGN_ID, scope=scope, step_id=step_id, follow_up_count=follow_up_count
    )


def _nothing_written(env) -> None:
    assert env.sequences.added == [] and env.sequences.updated == []
    env.audit.assert_not_called()


class TestCreate:
    def test_creates_alternating_emails_and_waits_atomically_in_order(self) -> None:
        env = _service()
        sequence, outcome, warnings = _generate(env, follow_up_count=3)
        assert [p.position for p in env.sequences.added] == [1, 2, 4, 6]
        assert [p.leading_wait_minutes for p in env.sequences.added] == [
            None,
            4320,
            4320,
            4320,
        ]
        assert all(p.kind == "EMAIL" for p in env.sequences.added)
        subjects = [p.email_subject for p in env.sequences.added]
        assert len(set(subjects)) == 4
        assert outcome.attempts == 1 and outcome.model == "fake-model"
        assert warnings == []
        env.audit.assert_called_once()
        assert (
            env.audit.call_args.kwargs["action"]
            == "campaign.reference_templates_generated"
        )
        assert isinstance(sequence, SequenceOut)

    def test_every_written_email_is_valid_template_content(self) -> None:
        env = _service()
        _generate(env, follow_up_count=2)
        for payload in env.sequences.added:
            schema = validate_template_content(
                payload.email_subject, payload.email_body_html, payload.email_preheader
            )
            assert schema["standard"]  # variables were used and validated
            assert "{{first_name|there}}" in payload.email_body_html

    def test_text_format_writes_unstyled_paragraphs(self) -> None:
        env = _service(World({**CONFIG, "email_format": "TEXT"}))
        _generate(env, follow_up_count=1)
        for payload in env.sequences.added:
            assert payload.email_body_html.startswith("<p>")
            assert "<table" not in payload.email_body_html

    def test_html_format_wraps_every_email_in_the_brand_layout(self) -> None:
        env = _service(World(HTML_CONFIG))
        _generate(env, follow_up_count=1)
        for payload in env.sequences.added:
            assert 'bgcolor="#123456"' in payload.email_body_html
            assert 'src="https://acme.example/logo.png"' in payload.email_body_html
            assert "Book a demo</a>" in payload.email_body_html

    @pytest.mark.parametrize("count", [None, 0, 6])
    def test_follow_up_count_is_required_and_bounded_for_an_empty_sequence(
        self, count
    ) -> None:
        env = _service()
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=count)
        assert (
            info.value.code == "follow_up_count_required"
            and info.value.status_code == 422
        )
        assert env.model.draft_requests == []

    def test_stray_waits_are_refused(self) -> None:
        env = _service(World(steps=[_wait_step(1)]))
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=2)
        assert info.value.code == "state_conflict" and info.value.status_code == 409

    def test_progression_flag_warning(self) -> None:
        env = _service(settings=_settings(sequence_progression_enabled=False))
        _, _, warnings = _generate(env, follow_up_count=1)
        assert warnings == ["followups_require_progression"]


class TestRewrite:
    def _world(self) -> World:
        return World(
            steps=[
                _email_step(1, "old one", "<p>old</p>", version=3),
                _wait_step(2),
                _email_step(3, "old two", "<p>old</p>", version=5),
            ]
        )

    def test_updates_existing_steps_in_place_and_leaves_waits_alone(self) -> None:
        world = self._world()
        env = _service(world)
        _generate(env)
        assert (
            env.sequences.added == []
        )  # never delete/recreate: previews reference step ids
        assert [(sid, p.expected_version) for sid, p in env.sequences.updated] == [
            (world.steps[0]["id"], 3),
            (world.steps[2]["id"], 5),
        ]
        assert all(
            p.email_preheader == "" for _, p in env.sequences.updated
        )  # old preheader cleared
        assert len(env.model.draft_requests[0].steps) == 2

    def test_regenerating_one_step_only_touches_that_step_and_sees_its_neighbours(
        self,
    ) -> None:
        world = self._world()
        env = _service(world)
        target = world.steps[2]
        # the second email is regenerated, so it must differ from the first
        _generate(env, scope="STEP", step_id=target["id"])
        assert [sid for sid, _ in env.sequences.updated] == [target["id"]]
        request = env.model.draft_requests[0]
        assert [s.position for s in request.steps] == [2]
        assert (
            request.context_emails[0]["position"] == 1
            and request.context_emails[0]["subject"] == "old one"
        )

    def test_step_scope_needs_a_step_id_that_is_an_email_in_this_sequence(self) -> None:
        env = _service(self._world())
        with pytest.raises(AppError) as info:
            _generate(env, scope="STEP")
        assert info.value.status_code == 422
        for bad in (uuid.uuid4(), self._world().steps[1]["id"]):
            with pytest.raises(AppError) as info:
                _generate(env, scope="STEP", step_id=bad)
            assert info.value.code == "not_found" and info.value.status_code == 404
        assert env.model.draft_requests == []


class TestGuardsAndPlanning:
    def test_objective_is_required(self) -> None:
        for config in (None, {}, {"objective": ""}):
            env = _service(World(config))
            with pytest.raises(AppError) as info:
                _generate(env, follow_up_count=2)
            assert info.value.code == "personalization_objective_missing"
            assert env.model.draft_requests == []

    def test_campaign_guard_errors_propagate_before_any_model_call(self) -> None:
        world = World()
        world.campaign_error = AppError("state_conflict", "no", status_code=409)
        env = _service(world)
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=2)
        assert info.value.code == "state_conflict" and env.model.draft_requests == []
        assert world.campaign_calls[0] == {
            "require_draft": True,
            "require_enabled": True,
        }

    def test_not_configured_is_a_503_and_builds_no_model(self) -> None:
        env = _service(settings=_settings(personalization_openai_api_key=""))
        built = []
        env.service._model_factory = lambda: built.append(1)  # type: ignore[assignment,return-value]
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=2)
        assert (
            info.value.code == "personalization_not_configured"
            and info.value.status_code == 503
        )
        assert built == []

    def test_request_carries_only_objective_and_company_never_lead_data(self) -> None:
        env = _service(World(HTML_CONFIG))
        _generate(env, follow_up_count=1)
        request = env.model.draft_requests[0]
        assert set(request.objective) == set(
            PersonalizationConfig.model_validate(CONFIG).objective_core()
        )
        assert request.company == {
            "company_name": "Acme",
            "summary": "",
            "services": [],
            "industries": [],
            "audience": "",
            "tone_of_voice": "",
            "key_messages": [],
        }
        assert (
            "first_name" in request.allowed_variables
            and "email" not in request.allowed_variables
        )
        assert request.workspace_ref == str(WS)

    def test_role_names(self) -> None:
        assert [role_for(i) for i in (1, 2, 3)] == [
            "initial",
            "follow_up_1",
            "follow_up_2",
        ]


def _bad_step(position: int, **overrides) -> DraftedStep:
    base = dict(
        position=position,
        role="r",
        subject=f"Subject {position}",
        paragraphs=("Hi {{first_name|there}},", "Short."),
        preheader="",
        wait_days_after=3,
    )
    base.update(overrides)
    return DraftedStep(**base)


def _output(*steps: DraftedStep) -> SequenceDraftOutput:
    return SequenceDraftOutput(theme="t", steps=tuple(steps))


class TestValidationAndRetry:
    def test_a_rejected_attempt_is_retried_with_codes_only(self) -> None:
        model = FakeModel(
            draft_script=[_output(_bad_step(1), _bad_step(2))]
        )  # too short, no CTA
        env = _service(model=model)
        _, outcome, _ = _generate(env, follow_up_count=1)
        assert outcome.attempts == 2
        first, second = model.draft_requests
        assert first.retry_codes == () and "body_too_short" in second.retry_codes
        assert "Short." not in repr(second)  # the rejected output is never sent back

    def test_exhausted_attempts_write_nothing_and_report_codes(self) -> None:
        bad = _output(_bad_step(1), _bad_step(2))
        env = _service(model=FakeModel(draft_script=[bad, bad, bad]))
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=1)
        assert (
            info.value.code == "reference_generation_rejected"
            and info.value.status_code == 422
        )
        assert "body_too_short" in info.value.details["codes"]  # type: ignore[index]
        assert len(env.model.draft_requests) == 3
        _nothing_written(env)

    def test_wrong_number_of_emails_is_rejected(self) -> None:
        env = _service(model=FakeModel(draft_script=[_output(_bad_step(1))] * 3))
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=2)
        assert info.value.details == {"codes": ["wrong_step_count"]}
        _nothing_written(env)

    def test_hallucinated_facts_and_never_say_are_rejected(self) -> None:
        good = FakeModel().default_draft
        env = _service()
        request_steps = env.model  # noqa: F841
        model = FakeModel()
        first = model.default_draft(
            SimpleNamespace(
                objective=PersonalizationConfig.model_validate(CONFIG).objective_core(),
                steps=tuple(SimpleNamespace(position=i, role="r") for i in (1, 2)),
            )  # type: ignore[arg-type]
        )
        tainted = _output(
            DraftedStep(
                1,
                "r",
                first.steps[0].subject,
                (
                    *first.steps[0].paragraphs,
                    "Join 5000 teams, it is guaranteed. Call 212 555 0100 or visit https://evil.test",
                ),
                "",
                3,
            ),
            first.steps[1],
        )
        env = _service(model=FakeModel(draft_script=[tainted, tainted, tainted]))
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=1)
        codes = set(info.value.details["codes"])  # type: ignore[index]
        assert {
            "unsupported_number",
            "unsupported_phone",
            "unsupported_url",
            "never_say_violation",
        } <= codes
        _nothing_written(env)
        assert good  # keep the reference alive for readability

    def test_variables_must_be_allowed_and_have_fallbacks(self) -> None:
        model = FakeModel()
        first = model.default_draft(
            SimpleNamespace(
                objective=PersonalizationConfig.model_validate(CONFIG).objective_core(),
                steps=tuple(SimpleNamespace(position=i, role="r") for i in (1, 2)),
            )  # type: ignore[arg-type]
        )
        for paragraph, code in (
            ("Hello {{first_name}}", "variable_without_fallback"),
            ("Hello {{email|there}}", "unknown_variable"),
            ("Hello {{ }}", "malformed_placeholder"),
            ("Hello {first_name}", "malformed_placeholder"),
        ):
            step = DraftedStep(
                1,
                "r",
                first.steps[0].subject,
                (*first.steps[0].paragraphs, paragraph),
                "",
                3,
            )
            env = _service(
                model=FakeModel(draft_script=[_output(step, first.steps[1])] * 3)
            )
            with pytest.raises(AppError) as info:
                _generate(env, follow_up_count=1)
            assert code in info.value.details["codes"], code  # type: ignore[index]

    def test_filler_and_repeated_followups_are_rejected(self) -> None:
        model = FakeModel()
        first = model.default_draft(
            SimpleNamespace(
                objective=PersonalizationConfig.model_validate(CONFIG).objective_core(),
                steps=tuple(SimpleNamespace(position=i, role="r") for i in (1, 2)),
            )  # type: ignore[arg-type]
        )
        filler = DraftedStep(
            2,
            "r",
            first.steps[0].subject,
            (*first.steps[1].paragraphs, "Just following up on my last email."),
            "",
            0,
        )
        env = _service(
            model=FakeModel(draft_script=[_output(first.steps[0], filler)] * 3)
        )
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=1)
        codes = set(info.value.details["codes"])  # type: ignore[index]
        assert {"filler_followup", "subject_repeated"} <= codes


class TestModelErrors:
    @pytest.mark.parametrize(
        "error,status,code,calls",
        [
            (ModelRateLimited(), 429, "model_rate_limited", 1),
            (ModelTimeout(), 504, "model_timeout", 1),
            (ModelPermanentError(), 503, "personalization_not_configured", 1),
        ],
    )
    def test_errors_map_and_are_not_retried(self, error, status, code, calls) -> None:
        model = FakeModel(draft_script=[error])
        env = _service(model=model)
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=1)
        assert (info.value.status_code, info.value.code) == (status, code)
        assert len(model.draft_requests) == calls
        _nothing_written(env)

    def test_one_transient_error_is_retried_without_using_a_quality_attempt(
        self,
    ) -> None:
        model = FakeModel(draft_script=[ModelTransientError()])
        env = _service(model=model)
        _, outcome, _ = _generate(env, follow_up_count=1)
        assert outcome.attempts == 1 and len(model.draft_requests) == 2

    def test_persistent_transient_errors_become_503(self) -> None:
        env = _service(
            model=FakeModel(draft_script=[ModelTransientError(), ModelTransientError()])
        )
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=1)
        assert info.value.code == "model_unavailable" and info.value.status_code == 503
        _nothing_written(env)

    def test_repeated_refusals_are_a_clear_422(self) -> None:
        env = _service(model=FakeModel(draft_script=[ModelRefusal()] * 3))
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=1)
        assert info.value.code == "model_refusal" and info.value.status_code == 422

    def test_malformed_output_is_retried_then_rejected(self) -> None:
        env = _service(model=FakeModel(draft_script=[MalformedOutput()] * 3))
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=1)
        assert info.value.code == "reference_generation_rejected"
        assert info.value.details == {"codes": ["malformed_output"]}
        assert len(env.model.draft_requests) == 3

    def test_recovers_after_a_malformed_output(self) -> None:
        env = _service(model=FakeModel(draft_script=[MalformedOutput()]))
        _, outcome, _ = _generate(env, follow_up_count=1)
        assert outcome.attempts == 2

    def test_deadline_stops_further_attempts(self) -> None:
        now = [0.0]

        def clock() -> float:
            return now[0]

        bad = _output(_bad_step(1), _bad_step(2))

        def slow(request):
            now[0] += 100.0  # the first attempt eats the whole budget
            return bad

        env = _service(model=FakeModel(draft_script=[slow]), clock=clock)
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=1)
        assert info.value.code == "model_timeout" and info.value.status_code == 504
        assert len(env.model.draft_requests) == 1
        _nothing_written(env)

    def test_error_messages_never_leak_provider_text(self) -> None:
        env = _service(
            model=FakeModel(
                draft_script=[ModelPermanentError("key sk-live-abc rejected")]
            )
        )
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=1)
        assert "sk-live" not in info.value.message


class TestConcurrencyAndConnections:
    def test_connection_is_released_before_the_model_and_scope_restored_after(
        self,
    ) -> None:
        order: list[str] = []
        env = _service()
        env.session.commit.side_effect = lambda: order.append("commit")
        original = env.model.draft_sequence

        def spy(request):
            order.append("model")
            return original(request)

        env.model.draft_sequence = spy  # type: ignore[method-assign]
        with patch(
            "app.modules.personalization.reference_templates.enter_api_scope",
            side_effect=lambda *a, **k: order.append("scope"),
        ) as scope:
            _generate(env, follow_up_count=1)
        assert order[0] == "commit" and order.index("commit") < order.index(
            "model"
        ) < order.index("scope")
        assert scope.call_args.kwargs == {
            "user_id": CTX.user_id,
            "workspace_id": CTX.workspace_id,
        }

    def test_state_is_rechecked_in_the_new_transaction(self) -> None:
        world = World()
        env = _service(world)
        _generate(env, follow_up_count=1)
        # guard runs once to plan and once again before writing
        assert len(world.campaign_calls) == 2

    def test_objective_edited_while_generating_is_a_conflict_and_writes_nothing(
        self,
    ) -> None:
        world = World()
        model = FakeModel()
        original = model.draft_sequence

        def edit_during(request):
            world.config = {**CONFIG, "offer": "Something else entirely"}
            return original(request)

        model.draft_sequence = edit_during  # type: ignore[method-assign]
        env = _service(world, model)
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=1)
        assert info.value.code == "conflict" and info.value.status_code == 409
        _nothing_written(env)

    def test_step_edited_while_generating_is_a_conflict_and_writes_nothing(
        self,
    ) -> None:
        world = World(steps=[_email_step(1, "a", "<p>a</p>", version=1)])
        model = FakeModel()
        original = model.draft_sequence

        def edit_during(request):
            world.steps[0]["version"] = 2
            return original(request)

        model.draft_sequence = edit_during  # type: ignore[method-assign]
        env = _service(world, model)
        with pytest.raises(AppError) as info:
            _generate(env)
        assert info.value.code == "conflict"
        _nothing_written(env)

    def test_step_deleted_while_generating_is_a_conflict(self) -> None:
        world = World(
            steps=[
                _email_step(1, "a", "<p>a</p>"),
                _wait_step(2),
                _email_step(3, "b", "<p>b</p>"),
            ]
        )
        model = FakeModel()
        original = model.draft_sequence

        def delete_during(request):
            del world.steps[1:]
            return original(request)

        model.draft_sequence = delete_during  # type: ignore[method-assign]
        env = _service(world, model)
        with pytest.raises(AppError) as info:
            _generate(env)
        assert info.value.code == "conflict"
        _nothing_written(env)

    def test_steps_appearing_while_creating_is_a_conflict(self) -> None:
        world = World()
        model = FakeModel()
        original = model.draft_sequence

        def add_during(request):
            world.steps.append(_email_step(1, "typed by someone", "<p>x</p>"))
            return original(request)

        model.draft_sequence = add_during  # type: ignore[method-assign]
        env = _service(world, model)
        with pytest.raises(AppError) as info:
            _generate(env, follow_up_count=1)
        assert info.value.code == "conflict"
        _nothing_written(env)

    def test_model_is_always_closed(self) -> None:
        model = FakeModel(draft_script=[ModelTimeout()])
        model.close = MagicMock()  # type: ignore[attr-defined]
        env = _service(model=model)
        with pytest.raises(AppError):
            _generate(env, follow_up_count=1)
        model.close.assert_called_once()  # type: ignore[attr-defined]

    def test_membership_is_rechecked_on_postgres_only(self) -> None:
        env = _service()
        fake_bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        env.session.get_bind = lambda: fake_bind  # type: ignore[method-assign]
        for role, expected in ((None, "not_found"), ("VIEWER", "forbidden")):
            env.session.execute = MagicMock(
                return_value=SimpleNamespace(scalar=lambda role=role: role)
            )  # type: ignore[method-assign]
            with patch(
                "app.modules.personalization.reference_templates.enter_api_scope"
            ):
                with pytest.raises(AppError) as info:
                    _generate(env, follow_up_count=1)
            assert info.value.code == expected
            _nothing_written(env)
