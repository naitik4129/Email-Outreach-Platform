"""AI drafting of a campaign's reference emails (ADR-0016).

One request, three phases:

A. inside the request's transaction: authorize, read the objective and steps,
   decide what to write, then COMMIT so the pooled connection is released;
B. with no connection held: ask the model, validate deterministically, retry a
   bounded number of times;
C. a fresh transaction (role and context re-established, state re-checked):
   write every email through SequenceService, or nothing at all.

Nothing is written unless every email passed validation, and existing steps are
updated in place (preview rows reference step ids with ON DELETE RESTRICT).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import RowMapping, text
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.config import Settings
from app.core.errors import AppError
from app.core.permissions import has_permission
from app.db.context import enter_api_scope
from app.modules.campaigns.schemas import (
    SequenceOut,
    SequenceStepCreateIn,
    SequenceStepUpdateIn,
)
from app.modules.campaigns.sequence_service import SequenceService
from app.modules.personalization.brand_layout import render_for_config
from app.modules.personalization.config_schema import (
    PersonalizationConfig,
    parse_config,
)
from app.modules.personalization.ports import (
    DraftingModel,
    MalformedOutput,
    ModelError,
    ModelPermanentError,
    ModelRateLimited,
    ModelRefusal,
    ModelTimeout,
    ModelTransientError,
    SequenceDraftRequest,
    StepBlueprint,
)
from app.modules.personalization.reference_validator import (
    ContextEmail,
    DraftContext,
    ValidatedStep,
    default_wait_days,
    validate_draft,
)
from app.modules.personalization.text_utils import html_to_text

logger = logging.getLogger(__name__)

# Merge variables a reference email may use. Each needs a fallback (validator).
ALLOWED_VARIABLES: tuple[str, ...] = ("first_name", "last_name", "company", "title")
MAX_FOLLOW_UPS = 5
_MIN_SECONDS_FOR_ANOTHER_ATTEMPT = 8.0
_TRANSIENT_RETRIES = 1
_CONTEXT_TEXT_CHARS = 1200


@dataclass(frozen=True)
class _Plan:
    scope: str  # "CREATE" | "REWRITE_ALL" | "STEP"
    config: PersonalizationConfig
    config_snapshot: dict[str, Any]
    expected_positions: tuple[int, ...]
    blueprint: tuple[StepBlueprint, ...]
    # email ordinal -> (step id, version) for steps that will be rewritten
    targets: dict[int, tuple[UUID, int]]
    context_emails: tuple[ContextEmail, ...]
    email_count: int
    # STEP only: the user's requested changes and the email they apply to.
    instructions: str | None = None
    current_email: ContextEmail | None = None


@dataclass(frozen=True)
class DraftOutcome:
    steps: tuple[ValidatedStep, ...]
    theme: str
    model: str
    attempts: int


def role_for(position: int) -> str:
    return "initial" if position == 1 else f"follow_up_{position - 1}"


def map_model_error(exc: ModelError) -> AppError:
    """A provider failure as a safe, user-actionable API error. Never includes
    provider text, prompts or outputs."""
    if isinstance(exc, ModelRateLimited):
        return AppError(
            "model_rate_limited",
            "The AI service is busy. Wait a moment and try again.",
            status_code=429,
        )
    if isinstance(exc, ModelTimeout):
        return AppError(
            "model_timeout",
            "The AI service took too long to respond. Try again, or write the "
            "emails yourself.",
            status_code=504,
        )
    if isinstance(exc, ModelPermanentError):
        return AppError(
            "personalization_not_configured",
            "AI drafting isn't available right now. You can still write the emails "
            "by hand.",
            status_code=503,
        )
    if isinstance(exc, ModelRefusal):
        return AppError(
            "model_refusal",
            "The AI couldn't write these emails. Review the objective, or write "
            "them yourself.",
            status_code=422,
        )
    return AppError(
        "model_unavailable",
        "The AI service is temporarily unavailable. Try again shortly.",
        status_code=503,
    )


class ReferenceTemplateService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        campaign_guard: Callable[..., RowMapping],
        sequence_reader: Callable[
            [WorkspaceContext, UUID], tuple[RowMapping | None, list[RowMapping]]
        ],
        drafting_model_factory: Callable[[], DraftingModel],
        sequence_service: SequenceService | None = None,
        audit: Callable[..., None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session
        self.settings = settings
        self._campaign_guard = campaign_guard
        self._read = sequence_reader
        self._model_factory = drafting_model_factory
        self._sequences = sequence_service or SequenceService(session)
        self._audit = audit
        self._clock = clock
        self._sleep = sleep

    # ------------------------------------------------------------------ entry

    def generate(
        self,
        context: WorkspaceContext,
        campaign_id: UUID,
        *,
        scope: str,
        step_id: UUID | None,
        follow_up_count: int | None,
        instructions: str | None = None,
    ) -> tuple[SequenceOut, DraftOutcome, list[str]]:
        plan = self._plan(
            context, campaign_id, scope, step_id, follow_up_count, instructions
        )
        workspace_ref = str(context.workspace_id)
        # Phase A ends: free the pooled connection before any model call.
        self.session.commit()

        outcome = self._draft(plan, workspace_ref)

        self._write(context, campaign_id, plan, outcome)
        sequence = self._sequences.get_sequence(context, campaign_id)
        warnings: list[str] = []
        if plan.email_count > 1 and not self.settings.sequence_progression_enabled:
            warnings.append("followups_require_progression")
        logger.info(
            "reference emails drafted",
            extra={
                "campaign_id": str(campaign_id),
                "scope": plan.scope,
                "emails": len(outcome.steps),
                "attempts": outcome.attempts,
            },
        )
        return sequence, outcome, warnings

    # ------------------------------------------------------------- phase A: plan

    def _plan(
        self,
        context: WorkspaceContext,
        campaign_id: UUID,
        scope: str,
        step_id: UUID | None,
        follow_up_count: int | None,
        instructions: str | None,
    ) -> _Plan:
        self._campaign_guard(
            context, campaign_id, require_draft=True, require_enabled=True
        )
        if not self.settings.personalization_drafting_ready:
            raise AppError(
                "personalization_not_configured",
                "AI drafting isn't available in this environment. You can still "
                "write the emails by hand.",
                status_code=503,
            )
        sequence, steps = self._read(context, campaign_id)
        raw = sequence["personalization_config"] if sequence is not None else None
        try:
            config = parse_config(raw)
        except ValidationError:
            config = None
        if config is None:
            raise AppError(
                "personalization_objective_missing",
                "Save the campaign objective first.",
                status_code=422,
            )
        email_steps = [s for s in steps if s["kind"] == "EMAIL"]

        if scope == "STEP":
            if step_id is None:
                raise AppError(
                    "validation_error",
                    "step_id is required to regenerate one email.",
                    status_code=422,
                )
            index = next(
                (
                    i
                    for i, s in enumerate(email_steps, start=1)
                    if str(s["id"]) == str(step_id)
                ),
                None,
            )
            if index is None:
                raise AppError("not_found", "Sequence step not found", status_code=404)
            context_emails = tuple(
                ContextEmail(
                    position=i,
                    subject=str(s["email_subject"] or ""),
                    body_text=html_to_text(str(s["email_body_html"] or "")),
                )
                for i, s in enumerate(email_steps, start=1)
                if i != index
            )
            target = email_steps[index - 1]
            current_email = (
                ContextEmail(
                    position=index,
                    subject=str(target["email_subject"] or ""),
                    body_text=html_to_text(str(target["email_body_html"] or "")),
                )
                if instructions
                else None
            )
            return _Plan(
                scope="STEP",
                config=config,
                config_snapshot=dict(raw or {}),
                expected_positions=(index,),
                blueprint=(StepBlueprint(index, role_for(index)),),
                targets={index: (UUID(str(target["id"])), int(target["version"]))},
                context_emails=context_emails,
                email_count=len(email_steps),
                instructions=instructions or None,
                current_email=current_email,
            )

        if not email_steps:
            if steps:
                # Stray waits with no email: refuse rather than guess.
                raise AppError(
                    "state_conflict",
                    "Remove the empty wait steps before generating emails.",
                    status_code=409,
                )
            if follow_up_count is None or not 1 <= follow_up_count <= MAX_FOLLOW_UPS:
                raise AppError(
                    "follow_up_count_required",
                    f"Choose how many follow-ups to add (1 to {MAX_FOLLOW_UPS}).",
                    status_code=422,
                )
            count = 1 + follow_up_count
            return _Plan(
                scope="CREATE",
                config=config,
                config_snapshot=dict(raw or {}),
                expected_positions=tuple(range(1, count + 1)),
                blueprint=tuple(
                    StepBlueprint(i, role_for(i)) for i in range(1, count + 1)
                ),
                targets={},
                context_emails=(),
                email_count=count,
            )

        count = len(email_steps)
        return _Plan(
            scope="REWRITE_ALL",
            config=config,
            config_snapshot=dict(raw or {}),
            expected_positions=tuple(range(1, count + 1)),
            blueprint=tuple(StepBlueprint(i, role_for(i)) for i in range(1, count + 1)),
            targets={
                i: (UUID(str(s["id"])), int(s["version"]))
                for i, s in enumerate(email_steps, start=1)
            },
            context_emails=(),
            email_count=count,
        )

    # ------------------------------------------------------ phase B: model + check

    def _draft(self, plan: _Plan, workspace_ref: str) -> DraftOutcome:
        config = plan.config
        company = (
            config.company.model_dump(
                mode="json", exclude={"source", "url"}, exclude_none=True
            )
            if config.company
            else None
        )
        context = DraftContext(
            objective=config,
            company=config.company,
            expected_positions=plan.expected_positions,
            allowed_variables=frozenset(ALLOWED_VARIABLES),
            context_emails=plan.context_emails,
        )
        neighbours = tuple(
            {
                "position": e.position,
                "subject": e.subject,
                "text": e.body_text[:_CONTEXT_TEXT_CHARS],
            }
            for e in plan.context_emails
        )
        current = (
            {
                "position": plan.current_email.position,
                "subject": plan.current_email.subject,
                "text": plan.current_email.body_text[:_CONTEXT_TEXT_CHARS],
            }
            if plan.current_email
            else None
        )
        deadline = self._clock() + self.settings.personalization_draft_deadline_seconds
        max_attempts = self.settings.personalization_max_attempts
        retry_codes: tuple[str, ...] = ()
        last_codes: tuple[str, ...] = ()
        transient_left = _TRANSIENT_RETRIES
        attempt = 0

        model = self._model_factory()
        try:
            while attempt < max_attempts:
                if (
                    self._clock() + _MIN_SECONDS_FOR_ANOTHER_ATTEMPT > deadline
                    and attempt
                ):
                    raise map_model_error(ModelTimeout())
                attempt += 1
                request = SequenceDraftRequest(
                    workspace_ref=workspace_ref,
                    objective=config.objective_core(),
                    company=company,
                    steps=plan.blueprint,
                    allowed_variables=ALLOWED_VARIABLES,
                    context_emails=neighbours,
                    user_instructions=plan.instructions,
                    current_email=current,
                    retry_codes=retry_codes,
                    email_count=plan.email_count,
                )
                try:
                    result = model.draft_sequence(request)
                except (ModelRefusal, MalformedOutput) as exc:
                    # A rejected attempt, like a validation failure.
                    last_codes = (exc.code,)
                    retry_codes = ()
                    if attempt >= max_attempts:
                        if isinstance(exc, ModelRefusal):
                            raise map_model_error(exc) from exc
                        break
                    continue
                except ModelRateLimited as exc:
                    raise map_model_error(exc) from exc
                except ModelTransientError as exc:
                    if transient_left > 0 and self._clock() + 2 < deadline:
                        transient_left -= 1
                        attempt -= 1  # a provider hiccup is not a quality attempt
                        self._sleep(1.0)
                        continue
                    raise map_model_error(exc) from exc
                except ModelError as exc:  # timeout, permanent
                    raise map_model_error(exc) from exc

                validation = validate_draft(result.output, context)
                if validation.ok:
                    return DraftOutcome(
                        steps=validation.steps,
                        theme=result.output.theme[:200],
                        model=result.model,
                        attempts=attempt,
                    )
                last_codes = validation.codes
                retry_codes = validation.codes
        finally:
            close = getattr(model, "close", None)
            if callable(close):
                close()

        logger.info(
            "reference drafting rejected",
            extra={"attempts": attempt, "codes": list(last_codes)},
        )
        raise AppError(
            "reference_generation_rejected",
            "The AI couldn't produce emails that meet the checks. Try again, or "
            "write them yourself.",
            status_code=422,
            details={"codes": list(last_codes)},
        )

    # ------------------------------------------------------------ phase C: write

    def _write(
        self,
        context: WorkspaceContext,
        campaign_id: UUID,
        plan: _Plan,
        outcome: DraftOutcome,
    ) -> None:
        # The connection was released; this is a new transaction. Re-establish the
        # identity and re-verify everything read earlier: time has passed.
        enter_api_scope(
            self.session, user_id=context.user_id, workspace_id=context.workspace_id
        )
        self._recheck_membership(context)
        self._campaign_guard(
            context, campaign_id, require_draft=True, require_enabled=True
        )
        sequence, steps = self._read(context, campaign_id)
        current_config = (
            dict(sequence["personalization_config"] or {})
            if sequence is not None
            else {}
        )
        changed = AppError(
            "conflict",
            "The campaign changed while the emails were being written. Nothing "
            "was replaced. Please try again.",
            status_code=409,
        )
        if current_config != plan.config_snapshot:
            raise changed
        email_steps = [s for s in steps if s["kind"] == "EMAIL"]

        by_position = {step.position: step for step in outcome.steps}
        if plan.scope == "CREATE":
            if steps:
                raise changed
            self._create(context, campaign_id, plan, outcome)
        else:
            current = {
                i: (UUID(str(s["id"])), int(s["version"]))
                for i, s in enumerate(email_steps, start=1)
            }
            for position, target in plan.targets.items():
                if current.get(position) != target:
                    raise changed
            for position, (step_id, version) in plan.targets.items():
                drafted = by_position[position]
                self._sequences.update_step(
                    context,
                    campaign_id,
                    step_id,
                    SequenceStepUpdateIn(
                        expected_version=version,
                        email_subject=drafted.subject,
                        email_body_html=render_for_config(
                            plan.config, drafted.fragment_html
                        ),
                        # "" clears an old pre-header the new draft doesn't have.
                        email_preheader=drafted.preheader or "",
                    ),
                )
        if self._audit is not None:
            self._audit(
                workspace_id=context.workspace_id,
                actor_id=context.user_id,
                action="campaign.reference_templates_generated",
                target_id=campaign_id,
            )

    def _create(
        self,
        context: WorkspaceContext,
        campaign_id: UUID,
        plan: _Plan,
        outcome: DraftOutcome,
    ) -> None:
        position = 1
        for index, drafted in enumerate(outcome.steps):
            previous_wait_days = (
                outcome.steps[index - 1].wait_days if index else 0
            ) or default_wait_days(index - 1)
            self._sequences.add_step(
                context,
                campaign_id,
                SequenceStepCreateIn(
                    kind="EMAIL",
                    position=position,
                    email_subject=drafted.subject,
                    email_body_html=render_for_config(
                        plan.config, drafted.fragment_html
                    ),
                    email_preheader=drafted.preheader or None,
                    # The wait BEFORE this email is the previous email's gap; the
                    # server inserts it atomically with the email.
                    leading_wait_minutes=(previous_wait_days * 24 * 60)
                    if index
                    else None,
                ),
            )
            position += 1 if index == 0 else 2

    def _recheck_membership(self, context: WorkspaceContext) -> None:
        bind = self.session.get_bind()
        if bind is None or getattr(bind.dialect, "name", "") == "sqlite":
            return  # no roles in unit tests; RLS enforces this in Postgres
        role = self.session.execute(
            text("SELECT public.app_current_workspace_role()")
        ).scalar()
        if role is None:
            raise AppError("not_found", "Workspace not found", status_code=404)
        if not has_permission(str(role), "campaigns.draft"):
            raise AppError(
                "forbidden",
                "You no longer have permission to edit this campaign.",
                status_code=403,
            )
