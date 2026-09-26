"""Personalization API service, campaign-type rules and preflight, against mocked
repositories (no database). Also the preview runner. Authorization is enforced by
route dependencies and RLS; here we check that every lookup is workspace-scoped
and that cross-tenant ids resolve to nothing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.api.deps import WorkspaceContext
from app.core.config import Settings
from app.core.errors import AppError
from app.modules.campaigns.preflight import PreflightService
from app.modules.campaigns.schemas import (
    CampaignCreateIn,
    CampaignUpdateIn,
)
from app.modules.campaigns.service import CampaignService
from app.modules.personalization.api_service import PersonalizationApiService
from app.modules.personalization.budget import AllowAllLimiter
from app.modules.personalization.config_schema import compute_approval_digest
from app.modules.personalization.fake_model import FakeModel
from app.modules.personalization.ports import (
    GenerationOutput,
    ModelPermanentError,
    ModelTimeout,
)
from app.modules.personalization.previews import PreviewRunner
from app.modules.personalization.schemas import (
    ApproveIn,
    PersonalizationConfigIn,
    PreviewCreateIn,
)
from app.modules.personalization.service import PersonalizationService
from tests.support.personalization_fakes import (
    CONFIG,
    FROZEN,
    REF_BODY,
    REF_SUBJECT,
    WS,
    Clock,
    FakeResearch,
    FakeStore,
)

CAMPAIGN_ID = uuid.uuid4()
USER = uuid.uuid4()
MODEL = "test-model"
CTX = WorkspaceContext(workspace_id=WS, user_id=USER, role_code="MANAGER")


def _settings(**kw) -> Settings:
    base = {
        "database_url": "sqlite+pysqlite:///:memory:",
        "redis_url": "redis://x",
        "supabase_url": "https://x.supabase.co",
        "personalization_enabled": True,
        "personalization_model": MODEL,
        "personalization_daily_preview_cap": 20,
    }
    base.update(kw)
    return Settings(**base)


STEPS = [
    {
        "id": uuid.uuid4(),
        "position": 1,
        "kind": "EMAIL",
        "email_subject": REF_SUBJECT,
        "email_body_html": REF_BODY,
        "email_preheader": None,
    },
    {"id": uuid.uuid4(), "position": 2, "kind": "WAIT", "wait_duration_minutes": 2880},
    {
        "id": uuid.uuid4(),
        "position": 3,
        "kind": "EMAIL",
        "email_subject": "Another idea for {{company}}",
        "email_body_html": REF_BODY,
        "email_preheader": None,
    },
]
SEQUENCE = {
    "id": uuid.uuid4(),
    "version": 4,
    "personalization_config": CONFIG,
}


def _campaign(**kw) -> dict:
    row = {
        "id": CAMPAIGN_ID,
        "workspace_id": WS,
        "campaign_type": "HYPER_PERSONALIZED",
        "status": "DRAFT",
        "draft_sequence_id": SEQUENCE["id"],
        "draft_audience_id": uuid.uuid4(),
    }
    row.update(kw)
    return row


def _service(campaign=None, sequence=SEQUENCE, steps=STEPS, **settings_kw):
    service = PersonalizationApiService(MagicMock(), _settings(**settings_kw))
    service.campaigns = MagicMock()
    service.campaigns.get_campaign.return_value = campaign
    service.campaigns.get_sequence.return_value = sequence
    service.campaigns.list_steps_ordered.return_value = steps
    service.repo = MagicMock()
    service.repo.list_approvals.return_value = []
    service.repo.usage_today.return_value = {}
    return service


def _digest(config=CONFIG, steps=STEPS) -> str:
    return compute_approval_digest(config=config, steps=steps, model=MODEL)


def _preview_row(member, step, *, state="OK", digest=None, **kw) -> dict:
    row = {
        "id": uuid.uuid4(),
        "audience_member_id": member,
        "lead_id": uuid.uuid4(),
        "step_id": step["id"],
        "step_position": step["position"],
        "config_digest": digest or _digest(),
        "state": state,
        "subject": "S" if state == "OK" else None,
        "body_html": "<p>B</p>" if state == "OK" else None,
        "facts": [],
        "research_summary": None,
        "fallback_used": False,
        "failure_codes": [] if state == "OK" else ["x"],
        "created_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC) + timedelta(days=7),
        "frozen_variables": FROZEN,
    }
    row.update(kw)
    return row


def _full_batch(members=3, **kw):
    ids = [uuid.uuid4() for _ in range(members)]
    email_steps = [s for s in STEPS if s["kind"] == "EMAIL"]
    return [_preview_row(m, s, **kw) for m in ids for s in email_steps]


class TestScoping:
    def test_unknown_or_other_tenant_campaign_is_404(self) -> None:
        service = _service(campaign=None)
        with pytest.raises(AppError) as info:
            service.get_state(CTX, CAMPAIGN_ID)
        assert info.value.status_code == 404
        # The lookup itself carries the caller's workspace, never a client value.
        service.campaigns.get_campaign.assert_called_once_with(
            workspace_id=WS, campaign_id=CAMPAIGN_ID
        )

    def test_standard_campaigns_have_no_personalization_surface(self) -> None:
        service = _service(campaign=_campaign(campaign_type="STANDARD"))
        with pytest.raises(AppError) as info:
            service.get_state(CTX, CAMPAIGN_ID)
        assert info.value.code == "not_personalized_campaign"

    def test_foreign_audience_member_resolves_to_nothing(self) -> None:
        service = _service(campaign=_campaign())
        service.repo.list_batch.return_value = []
        service.campaigns.get_accepted_audience_member.return_value = None
        with pytest.raises(AppError) as info:
            service.create_previews(
                CTX,
                CAMPAIGN_ID,
                PreviewCreateIn(
                    batch_id=uuid.uuid4(), audience_member_ids=[uuid.uuid4()]
                ),
            )
        assert info.value.status_code == 404
        kwargs = service.campaigns.get_accepted_audience_member.call_args.kwargs
        assert kwargs["workspace_id"] == WS and kwargs["campaign_id"] == CAMPAIGN_ID


class TestState:
    def test_state_reports_config_version_digest_and_approval(self) -> None:
        service = _service(campaign=_campaign())
        state = service.get_state(CTX, CAMPAIGN_ID)
        assert state.config is not None and state.config.cta == CONFIG["cta"]
        assert state.config_version == 4
        assert state.config_digest == _digest()
        assert state.approval.status == "NONE"
        assert state.enabled and state.model == MODEL

    def test_approval_status_approved_stale_none(self) -> None:
        service = _service(campaign=_campaign())
        assert service.approval_status(CTX, CAMPAIGN_ID).status == "NONE"
        service.repo.list_approvals.return_value = [
            {
                "config_digest": "f" * 64,
                "approved_at": datetime.now(UTC),
                "approved_by": USER,
            }
        ]
        assert service.approval_status(CTX, CAMPAIGN_ID).status == "STALE"
        service.repo.list_approvals.return_value.append(
            {
                "config_digest": _digest(),
                "approved_at": datetime.now(UTC),
                "approved_by": USER,
            }
        )
        assert service.approval_status(CTX, CAMPAIGN_ID).status == "APPROVED"

    def test_editing_the_objective_or_templates_makes_an_approval_stale(self) -> None:
        approved = _digest()
        edited_cta = _digest(config={**CONFIG, "cta": "Reply with a time"})
        edited_step = _digest(
            steps=[{**STEPS[0], "email_subject": "Changed"}, *STEPS[1:]]
        )
        assert approved != edited_cta and approved != edited_step

    def test_capabilities_expose_only_flag_and_model(self) -> None:
        caps = _service(campaign=_campaign()).capabilities()
        assert caps.model_dump() == {"enabled": True, "model": MODEL}
        off = PersonalizationApiService(
            MagicMock(), _settings(personalization_enabled=False)
        )
        assert off.capabilities().model_dump() == {"enabled": False, "model": None}


class TestPutConfig:
    def _payload(self, **kw) -> PersonalizationConfigIn:
        return PersonalizationConfigIn(config=CONFIG, **kw)

    def test_saves_and_audits_without_logging_content(self) -> None:
        service = _service(campaign=_campaign())
        service.campaigns.set_sequence_personalization_config.return_value = SEQUENCE
        service.put_config(CTX, CAMPAIGN_ID, self._payload(expected_version=4))
        kwargs = service.campaigns.set_sequence_personalization_config.call_args.kwargs
        assert kwargs["workspace_id"] == WS and kwargs["expected_version"] == 4
        assert kwargs["config"]["cta"] == CONFIG["cta"]
        audit = service.campaigns.record_audit_event.call_args.kwargs
        assert audit["action"] == "campaign.personalization_config"
        assert (
            audit.get("after_state") is None
        )  # objective text is not copied to the audit log

    def test_version_conflict_is_409(self) -> None:
        service = _service(campaign=_campaign())
        service.campaigns.set_sequence_personalization_config.return_value = None
        with pytest.raises(AppError) as info:
            service.put_config(CTX, CAMPAIGN_ID, self._payload(expected_version=1))
        assert info.value.status_code == 409

    def test_creates_the_draft_sequence_on_first_save(self) -> None:
        service = _service(campaign=_campaign(), sequence=None)
        service.campaigns.create_sequence.return_value = SEQUENCE
        service.campaigns.set_sequence_personalization_config.return_value = SEQUENCE
        service.put_config(CTX, CAMPAIGN_ID, self._payload())
        service.campaigns.create_sequence.assert_called_once()

    def test_only_editable_while_draft_and_only_when_enabled(self) -> None:
        with pytest.raises(AppError) as info:
            _service(campaign=_campaign(status="RUNNING")).put_config(
                CTX, CAMPAIGN_ID, self._payload()
            )
        assert info.value.code == "state_conflict"
        with pytest.raises(AppError) as info:
            _service(
                campaign=_campaign(),
                personalization_enabled=False,
                personalization_model="",
            ).put_config(CTX, CAMPAIGN_ID, self._payload())
        assert info.value.code == "personalization_disabled"

    def test_invalid_objectives_are_rejected_by_the_schema(self) -> None:
        with pytest.raises(ValidationError):
            PersonalizationConfigIn(config={**CONFIG, "cta": ""})
        with pytest.raises(ValidationError):
            PersonalizationConfigIn(config={**CONFIG, "unknown": "x"})


class TestCreatePreviews:
    def _members(self, n=3):
        return [{"id": uuid.uuid4(), "lead_id": uuid.uuid4()} for _ in range(n)]

    def test_creates_one_row_per_member_and_email_step_and_dispatches_once(
        self,
    ) -> None:
        service = _service(campaign=_campaign())
        service.repo.list_batch.side_effect = [[], _full_batch(3)]
        service.campaigns.list_accepted_audience_members.return_value = self._members()
        producer = MagicMock()
        with patch(
            "app.services.task_dispatch.get_task_producer", return_value=producer
        ):
            batch = uuid.uuid4()
            out = service.create_previews(
                CTX, CAMPAIGN_ID, PreviewCreateIn(batch_id=batch)
            )
        rows = service.repo.insert_previews.call_args.kwargs["rows"]
        assert len(rows) == 3 * 2  # 3 leads x 2 email steps (the WAIT is skipped)
        assert (
            service.repo.insert_previews.call_args.kwargs["config_digest"] == _digest()
        )
        service.session.commit.assert_called_once()  # committed BEFORE dispatch
        ((args, kwargs),) = producer.send_task.call_args_list
        assert args == ("personalization.generate_previews",)
        assert kwargs["kwargs"] == {
            "workspace_id": str(WS),
            "campaign_id": str(CAMPAIGN_ID),
            "batch_id": str(batch),
        }
        assert kwargs["queue"] == "personalization"
        assert len(out.items) == 6

    def test_a_retried_request_returns_the_same_batch_without_spending(self) -> None:
        service = _service(campaign=_campaign())
        service.repo.list_batch.return_value = _full_batch(1)
        with patch("app.services.task_dispatch.get_task_producer") as producer:
            service.create_previews(
                CTX, CAMPAIGN_ID, PreviewCreateIn(batch_id=uuid.uuid4())
            )
        service.repo.insert_previews.assert_not_called()
        producer.assert_not_called()

    def test_requires_an_objective_a_sequence_and_an_audience(self) -> None:
        for kwargs, code in [
            (
                {"sequence": {**SEQUENCE, "personalization_config": None}},
                "objective_missing",
            ),
            ({"steps": []}, "sequence_empty"),
            ({"campaign": _campaign(draft_audience_id=None)}, "audience_not_selected"),
        ]:
            args = {"campaign": _campaign(), **kwargs}
            service = _service(**args)
            service.repo.list_batch.return_value = []
            with pytest.raises(AppError) as info:
                service.create_previews(
                    CTX, CAMPAIGN_ID, PreviewCreateIn(batch_id=uuid.uuid4())
                )
            assert info.value.code == code

    def test_daily_preview_budget_is_enforced_before_any_work(self) -> None:
        service = _service(campaign=_campaign(), personalization_daily_preview_cap=5)
        service.repo.list_batch.return_value = []
        service.campaigns.list_accepted_audience_members.return_value = self._members()
        service.repo.usage_today.return_value = {"PREVIEW": 0}
        with pytest.raises(AppError) as info:
            service.create_previews(
                CTX, CAMPAIGN_ID, PreviewCreateIn(batch_id=uuid.uuid4())
            )
        assert info.value.status_code == 429  # 6 needed > cap 5
        service.repo.insert_previews.assert_not_called()

    def test_sample_size_is_capped_by_the_schema(self) -> None:
        with pytest.raises(ValidationError):
            PreviewCreateIn(
                batch_id=uuid.uuid4(), audience_member_ids=[uuid.uuid4()] * 6
            )


class TestApprove:
    def _approve(self, service, rows, *, digest=None, accepted=10):
        service.repo.list_batch.return_value = rows
        service.campaigns.get_audience_member_counts.return_value = {
            "accepted": accepted
        }
        return service.approve(
            CTX,
            CAMPAIGN_ID,
            ApproveIn(batch_id=uuid.uuid4(), config_digest=digest or _digest()),
        )

    def test_approves_a_complete_current_batch_and_audits(self) -> None:
        service = _service(campaign=_campaign())
        self._approve(service, _full_batch(3))
        kwargs = service.repo.insert_approval.call_args.kwargs
        assert kwargs["config_digest"] == _digest() and kwargs["approved_by"] == USER
        assert kwargs["workspace_id"] == WS
        audit = service.campaigns.record_audit_event.call_args.kwargs
        assert audit["action"] == "campaign.personalization_approved"

    @pytest.mark.parametrize(
        ("mutate", "code", "status"),
        [
            (lambda rows: [], "not_found", 404),
            (
                lambda rows: [{**rows[0], "state": "PENDING"}, *rows[1:]],
                "previews_incomplete",
                409,
            ),
            (
                lambda rows: [{**rows[0], "state": "FAILED"}, *rows[1:]],
                "previews_failed",
                409,
            ),
            (lambda rows: rows[:2], "previews_insufficient", 409),  # one lead only
            (
                lambda rows: [{**r, "config_digest": "0" * 64} for r in rows],
                "approval_stale",
                409,
            ),
        ],
    )
    def test_refuses_unsafe_approvals(self, mutate, code, status) -> None:
        service = _service(campaign=_campaign())
        rows = mutate(_full_batch(3))
        with pytest.raises(AppError) as info:
            self._approve(service, rows)
        assert info.value.code == code and info.value.status_code == status
        service.repo.insert_approval.assert_not_called()

    def test_a_digest_that_is_not_the_current_one_is_stale(self) -> None:
        service = _service(campaign=_campaign())
        with pytest.raises(AppError) as info:
            self._approve(service, _full_batch(3), digest="1" * 64)
        assert info.value.code == "approval_stale"
        service.repo.insert_approval.assert_not_called()

    def test_a_small_audience_needs_only_as_many_leads_as_it_has(self) -> None:
        service = _service(campaign=_campaign())
        self._approve(service, _full_batch(1), accepted=1)
        service.repo.insert_approval.assert_called_once()

    def test_every_email_step_of_a_lead_must_be_covered(self) -> None:
        service = _service(campaign=_campaign())
        rows = _full_batch(3)
        # Drop each lead's second-step sample.
        rows = [r for r in rows if r["step_position"] == 1]
        with pytest.raises(AppError) as info:
            self._approve(service, rows)
        assert info.value.code == "previews_insufficient"

    def test_only_a_draft_campaign_can_be_approved(self) -> None:
        with pytest.raises(AppError) as info:
            self._approve(
                _service(campaign=_campaign(status="RUNNING")), _full_batch(3)
            )
        assert info.value.code == "state_conflict"


class TestBatchOutput:
    def test_latest_batch_flags_staleness_and_completion(self) -> None:
        service = _service(campaign=_campaign())
        rows = _full_batch(1, digest="9" * 64)
        rows[0]["state"] = "PENDING"
        rows[0]["subject"] = rows[0]["body_html"] = None
        service.repo.latest_batch_id.return_value = uuid.uuid4()
        service.repo.list_batch.return_value = rows
        out = service.latest_batch(CTX, CAMPAIGN_ID)
        assert out is not None and out.stale and not out.complete and not out.all_ok
        assert out.items[0].recipient.first_name == "Sarah"
        assert (
            out.items[0].recipient.audience_member_id == rows[0]["audience_member_id"]
        )

    def test_no_batch_yet_is_none(self) -> None:
        service = _service(campaign=_campaign())
        service.repo.latest_batch_id.return_value = None
        assert service.latest_batch(CTX, CAMPAIGN_ID) is None

    def test_progress_reports_counts_and_budget(self) -> None:
        service = _service(campaign=_campaign())
        service.repo.progress.return_value = {
            "pending": 2,
            "succeeded": 5,
            "failed": 1,
            "superseded": 0,
            "fallback": 1,
            "oldest_pending_at": None,
            "failure_codes": {"unsupported_number": 1},
        }
        service.repo.usage_today.return_value = {"GENERATION": 7, "PREVIEW": 3}
        out = service.progress(CTX, CAMPAIGN_ID)
        assert (out.pending, out.succeeded, out.failed) == (2, 5, 1)
        assert out.budget.generation_used == 7 and out.budget.preview_used == 3
        assert out.failure_codes == {"unsupported_number": 1}


class TestCampaignType:
    def _service(self, **settings_kw):
        with patch(
            "app.modules.campaigns.service.Settings.current",
            return_value=_settings(**settings_kw),
        ):
            pass
        service = CampaignService(MagicMock())
        service.repo = MagicMock()
        return service

    def _row(self, campaign_type="HYPER_PERSONALIZED") -> dict:
        now = datetime.now(UTC)
        return {
            "id": CAMPAIGN_ID,
            "workspace_id": WS,
            "name": "N",
            "description": None,
            "creator_id": USER,
            "status": "DRAFT",
            "campaign_type": campaign_type,
            "start_at": None,
            "draft_sequence_id": None,
            "draft_audience_id": None,
            "current_settings_id": None,
            "planning_status": "PENDING",
            "archived_at": None,
            "error_reason": None,
            "version": 1,
            "created_at": now,
            "updated_at": now,
        }

    def test_creating_a_hyper_campaign_requires_the_feature_flag(self) -> None:
        service = self._service()
        payload = CampaignCreateIn(name="Outbound", campaign_type="HYPER_PERSONALIZED")
        with patch(
            "app.modules.campaigns.service.Settings.current",
            return_value=_settings(
                personalization_enabled=False, personalization_model=""
            ),
        ):
            with pytest.raises(AppError) as info:
                service.create_campaign(CTX, payload)
        assert info.value.code == "personalization_disabled"
        service.repo.create_campaign.assert_not_called()

    def test_hyper_campaign_is_created_with_its_type_and_reported(self) -> None:
        service = self._service()
        service.repo.create_campaign.return_value = self._row()
        with patch(
            "app.modules.campaigns.service.Settings.current", return_value=_settings()
        ):
            out = service.create_campaign(
                CTX,
                CampaignCreateIn(name="Outbound", campaign_type="HYPER_PERSONALIZED"),
            )
        assert (
            service.repo.create_campaign.call_args.kwargs["campaign_type"]
            == "HYPER_PERSONALIZED"
        )
        assert out.campaign_type == "HYPER_PERSONALIZED"

    def test_standard_is_the_default_and_needs_no_flag(self) -> None:
        service = self._service()
        service.repo.create_campaign.return_value = self._row("STANDARD")
        payload = CampaignCreateIn(name="Plain")
        assert payload.campaign_type == "STANDARD"
        out = service.create_campaign(CTX, payload)
        assert out.campaign_type == "STANDARD"

    def test_campaign_type_is_immutable_through_the_update_schema(self) -> None:
        with pytest.raises(ValidationError):
            CampaignUpdateIn(expected_version=1, campaign_type="STANDARD")
        with pytest.raises(ValidationError):
            CampaignCreateIn(name="x", campaign_type="AI_OUTREACH")

    def test_duplicate_copies_the_objective_but_never_the_approval(self) -> None:
        service = self._service()
        source = self._row()
        service.repo.get_campaign.side_effect = [source, self._row()]
        service.repo.create_campaign.return_value = {**self._row(), "id": uuid.uuid4()}
        service.repo.get_sequence.return_value = {
            "id": uuid.uuid4(),
            "personalization_config": CONFIG,
        }
        service.repo.create_sequence.return_value = {"id": uuid.uuid4()}
        service.repo.list_steps_ordered.return_value = []
        service.repo.list_campaign_mailboxes.return_value = []
        service.repo.list_settings_versions.return_value = []
        from app.modules.campaigns.schemas import CampaignDuplicateIn

        with patch(
            "app.modules.campaigns.service.Settings.current", return_value=_settings()
        ):
            service.duplicate_campaign(CTX, CAMPAIGN_ID, CampaignDuplicateIn())
        assert (
            service.repo.create_campaign.call_args.kwargs["campaign_type"]
            == "HYPER_PERSONALIZED"
        )
        assert (
            service.repo.set_sequence_personalization_config.call_args.kwargs["config"]
            == CONFIG
        )


class TestPreflight:
    def _run(
        self,
        *,
        settings=None,
        approval="APPROVED",
        config=CONFIG,
        steps=STEPS,
        members=(),
        progression=True,
    ):
        preflight = PreflightService.__new__(PreflightService)
        preflight.session = MagicMock()
        preflight.repo = MagicMock()
        preflight.repo.list_accepted_audience_members.return_value = list(members)
        api = MagicMock()
        api.current_digest.return_value = (
            "d" * 64,
            {"personalization_config": config},
            steps,
        )
        api.approval_status.return_value = MagicMock(status=approval)
        errors, warnings = [], []
        settings = settings or _settings(sequence_progression_enabled=progression)
        campaign = _campaign()
        with (
            patch(
                "app.modules.campaigns.preflight.Settings.current",
                return_value=settings,
            ),
            patch(
                "app.modules.campaigns.preflight.PersonalizationApiService",
                return_value=api,
            ),
        ):
            preflight._check_personalization(CTX, campaign, errors, warnings)
        return [e.code for e in errors], [w.code for w in warnings]

    def test_a_ready_campaign_has_no_personalization_errors(self) -> None:
        assert self._run() == ([], [])

    def test_disabled_deployment_blocks_activation(self) -> None:
        codes, _ = self._run(
            settings=_settings(personalization_enabled=False, personalization_model="")
        )
        assert codes == ["personalization_disabled"]

    def test_missing_objective_and_missing_or_stale_approval_block_activation(
        self,
    ) -> None:
        assert "personalization_objective_missing" in self._run(config=None)[0]
        assert "personalization_not_approved" in self._run(approval="NONE")[0]
        assert "personalization_approval_stale" in self._run(approval="STALE")[0]

    def test_follow_ups_need_the_progression_sweeper(self) -> None:
        assert "followups_require_progression" in self._run(progression=False)[0]
        single = [STEPS[0]]
        assert (
            "followups_require_progression"
            not in self._run(progression=False, steps=single)[0]
        )

    def test_inline_images_are_not_supported_in_reference_emails(self) -> None:
        with_image = [
            {**STEPS[0], "email_body_html": '<p>x</p><img src="cid:abcdefgh">'}
        ]
        assert "reference_inline_image_unsupported" in self._run(steps=with_image)[0]

    def test_thin_context_leads_produce_a_warning_not_an_error(self) -> None:
        members = [
            {"frozen_variables": {"first_name": "A"}},
            {"frozen_variables": FROZEN},
        ]
        errors, warnings = self._run(members=members)
        assert errors == [] and warnings == ["leads_missing_context_warning"]


class TestPreviewRunner:
    def _runner(self, model=None, *, cap=50, rpm=None, max_attempts=2):
        clock = Clock()
        store = FakeStore(clock)
        member = uuid.uuid4()
        step1, step2 = uuid.uuid4(), uuid.uuid4()
        store.step_waits = [
            {"position": 1, "kind": "EMAIL", "wait_duration_minutes": None},
            {"position": 2, "kind": "WAIT", "wait_duration_minutes": 4320},
            {"position": 3, "kind": "EMAIL", "wait_duration_minutes": None},
        ]
        follow_up_body = (
            "<p>Hi {{first_name}},</p><p>Teams like yours often waste hours on "
            "manual list building each week, so we automated the tedious parts "
            "of outbound prospecting for busy sales people.</p>"
            "<p>Would you be open to a quick conversation?</p><p>Best,<br>John</p>"
        )
        store.previews = [
            {
                "id": uuid.uuid4(),
                "audience_member_id": member,
                "step_id": sid,
                "config_digest": "a" * 64,
                "step_position": pos,
                "email_subject": subject,
                "email_body_html": body,
                "email_preheader": None,
                "frozen_variables": dict(FROZEN),
                "state": "PENDING",
            }
            for sid, pos, subject, body in (
                (step1, 1, REF_SUBJECT, REF_BODY),
                (step2, 3, "A different angle for {{company}}", follow_up_body),
            )
        ]
        model = model or FakeModel()
        service = PersonalizationService(
            model=model, research=FakeResearch(), min_facts=2
        )
        runner = PreviewRunner(
            db=store,
            service=service,
            rpm=rpm or AllowAllLimiter(),
            max_attempts=max_attempts,
            daily_preview_cap=cap,
            clock=clock,
        )
        return runner, store, model

    def _run(self, runner):
        return runner.run_batch(
            workspace_id=WS, campaign_id=CAMPAIGN_ID, batch_id=uuid.uuid4()
        )

    def test_generates_each_step_in_order_and_chains_the_previous_email(self) -> None:
        runner, store, model = self._runner()
        summary = self._run(runner)
        assert summary.ok == 2 and summary.failed == 0
        first, second = store.previews
        assert first["state"] == "OK" and second["state"] == "OK"
        assert first["subject"] and "<p>" in first["body_html"]
        assert model.requests[0].previous is None
        follow_up = model.requests[1].previous
        assert follow_up is not None and follow_up.subject == first["subject"]
        assert follow_up.days_since_sent == 3  # from the WAIT between the steps
        assert store.usage["PREVIEW"] == 2

    def test_failed_first_step_fails_the_follow_up_without_calling_the_model(
        self,
    ) -> None:
        runner, store, model = self._runner(FakeModel([ModelTimeout()]))
        summary = self._run(runner)
        assert summary.failed == 2
        assert [p["failure_codes"] for p in store.previews] == [
            ["model_timeout"],
            ["previous_step_failed"],
        ]
        assert model.calls == 1

    def test_provider_configuration_errors_are_reported_not_retried(self) -> None:
        runner, store, model = self._runner(
            FakeModel([ModelPermanentError(code="provider_401")])
        )
        self._run(runner)
        assert (
            store.previews[0]["failure_codes"] == ["provider_401"] and model.calls == 1
        )

    def test_rejected_output_is_retried_a_bounded_number_of_times(self) -> None:
        model = FakeModel()

        def bad(request):
            return GenerationOutput("", ("x",), (), "")

        runner, store, model = self._runner(model, max_attempts=2)
        model._script = [bad, bad]
        self._run(runner)
        assert store.previews[0]["state"] == "FAILED" and model.calls == 2
        assert "empty_subject" in store.previews[0]["failure_codes"]

    def test_daily_preview_cap_and_rate_limit_stop_generation(self) -> None:
        runner, store, model = self._runner(cap=0)
        self._run(runner)
        assert store.previews[0]["failure_codes"] == ["preview_budget_exhausted"]
        assert model.calls == 0

        class Deny:
            def allow(self) -> bool:
                return False

        runner, store, model = self._runner(rpm=Deny())
        self._run(runner)
        assert (
            store.previews[0]["failure_codes"] == ["rate_limited"] and model.calls == 0
        )

    def test_non_draft_campaign_or_missing_objective_fails_every_row(self) -> None:
        runner, store, model = self._runner()
        store.sequence_head["status"] = "RUNNING"
        self._run(runner)
        assert all(p["failure_codes"] == ["campaign_not_draft"] for p in store.previews)
        runner, store, model = self._runner()
        store.sequence_head["personalization_config"] = None
        self._run(runner)
        assert all(p["failure_codes"] == ["objective_invalid"] for p in store.previews)
        assert model.calls == 0
