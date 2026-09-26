# Hyper-personalized campaign type and just-in-time generation

## Status

Accepted for implementation — 2026-09-26 (owner approved the plan and M0 documentation set). No implementation may deviate without amending this ADR. **Amends [PROJECT_CONTEXT §4](../product/PROJECT_CONTEXT.md)** ("no separate AI Outreach campaign type") and moves "Advanced Personalization" from *V1.1 / Later* to *planned V1.1* in [MVP](../product/MVP.md).

## Context

Every campaign email is mail-merge: variables from the frozen lead snapshot are rendered into the authored subject/body when the message is planned ([CAMPAIGN_ENGINE](../architecture/CAMPAIGN_ENGINE.md)). The product owner wants a *Hyper-Personalized* campaign: the user defines one objective and one **reference template** per email step, and the platform writes an individual email per lead shortly before it is due, preserving the core offer, CTA and claims.

Constraints from existing decisions:

- The rendered snapshot (subject, body, digest, renderer version) must exist **before QUEUED**; the send worker never renders and retries reuse identical content ([MESSAGE_STATE_MACHINE](../architecture/MESSAGE_STATE_MACHINE.md), ADR-0007). `messages` snapshots are immutable once `rendered_at` is set.
- Activation freezes sequence content and audience. Per-lead output cannot exist at activation.
- "Missing data is a review error, never an invented personalization value" (CAMPAIGN_ENGINE).
- The engine must not need a separate AI state machine (USER_FLOWS §56). There is no `PREPARING`/`NEEDS_REVIEW` state and unlisted edges are forbidden.

## Decision

1. **Campaign type.** `campaigns.campaign_type` is `STANDARD` (default, all existing rows) or `HYPER_PERSONALIZED`. It is chosen at creation and **immutable** (the `app_api` role has no `UPDATE` grant on the column). The campaign engine, campaign/enrollment/message state machines, scheduler and send path are shared. The type only changes *how a message's content snapshot is produced*. This deliberately departs from PROJECT_CONTEXT §4, which preferred a template-level "Smart" mode; the owner chose a campaign-level entry point. Standard campaigns are unaffected.
2. **What the user authors.** A campaign objective (`campaign_sequences.personalization_config`, jsonb, size-capped: offer, target, problem solved, CTA, must-mention, never-say, tone) and, per email step, a **reference template**. The reference template reuses `sequence_steps.email_subject/email_body_html/email_preheader`, so editor, variable validation, attachments and the frozen-step guard keep working; its meaning is decided by `campaign_type`.
3. **What activation freezes.** Objective config, reference templates, attachments, audience, and the prompt/policy version (all covered by the frozen sequence digest, included only when present so existing digests are unchanged). Per-lead output is frozen **per message, before QUEUED**, exactly as today.
4. **Just-in-time generation with no new lifecycle state.** A hyper-personalized message is inserted `PLANNED` with an intended `due_at`/`anchor_at` and a durable `message_generations` row (job, lease, attempt counters, provenance). It becomes `SCHEDULED` only when a validated snapshot is written in one transaction (`renderer_version=2` for generated content; the digest formula and the send gate's recomputation are unchanged). Permanent failure is `PLANNED→FAILED` with `terminal_reason='personalization_failed:<code>'` (an already-legal edge for unrecoverable render error); the existing progression sweeper then fails the enrollment. `ERROR` remains reserved for campaign-level invariant failures.
5. **Timing.** Generation runs about `PERSONALIZATION_LEAD_TIME_SECONDS` (default 3600) before `due_at`, not at planning or activation, so context is fresh and no spend occurs for leads who reply or unsubscribe first. If generation completes late, the final `due_at` is `project_into_window(max(intended_due, now))`.
6. **Thin context fallback.** If a lead has fewer than the configured minimum usable facts, the message is rendered from the reference template with ordinary variable substitution (`renderer_version=1`) and the generation row records `fallback_used=true`. If context is sufficient but generation/validation fails after bounded attempts, the message is **not sent** (`FAILED`).
7. **Eligibility.** Generation rechecks eligibility (message still `PLANNED`, campaign `RUNNING`/`READY` with the same schedule generation, enrollment `ACTIVE` at this step, no suppression/reply/stop) inside the final transaction. The send gates remain authoritative at send time and are unchanged.
8. **Placement.** Generation runs as `app_worker_general` in a dedicated `personalization` queue/worker group (see [WORKERS](../architecture/WORKERS.md), [QUEUES](../architecture/QUEUES.md)); never in the send worker, which cannot write content and holds the authorization window.
9. **Follow-ups** use the previous message's stored content, the facts and angle recorded for it, and "no reply yet". They still require the progression sweeper (`SEQUENCE_PROGRESSION_ENABLED`); preflight rejects a multi-step hyper-personalized campaign while it is off.
10. **Human control.** Before activation the user generates sample previews (bounded number of leads, chained across steps) and a manager approves them. The approval is bound to a server-computed digest of objective + reference templates + prompt version + model; any edit makes it stale. After approval the remainder is generated just in time.
11. **Permissions.** No new capability: objective/previews use `campaigns.draft`, approval uses `campaigns.execute`, progress reads use `product.read`.

## Alternatives Considered

- **Per-step/template "Smart" mode only (PROJECT_CONTEXT §4).** Matches the older text but gives no wizard entry point and allows mixed campaigns; rejected by the owner.
- **A separate AI state machine/entity.** Duplicates lifecycle logic and contradicts USER_FLOWS §56.
- **Generate at activation or when planned.** Stale context, large wasted spend on leads who reply first, and an audience-sized LLM burst at launch.
- **Generate in the send worker.** Violates snapshot-before-QUEUED and the role grants, and would hold the 30 s authorization window during an LLM call.
- **`NEEDS_REVIEW` state / per-message human approval.** Undocumented state; deferred.
- **Job state columns on `messages`.** Mixes lease/attempt state into the hottest table; a separate `message_generations` row keeps it isolated.

## Consequences

- `PLANNED` now has two meanings distinguished by `campaign_type`; `fetch_planned_messages_chunk` and the progression renderer must branch, and standard behaviour must remain byte-identical.
- `FAILED` is terminal, so there is no manual regenerate in v1.
- A resume after a long pause makes many generations due at once, bounded by rate/budget limits (same known limitation as ADR-0009 for sends).
- Migrations 0026–0029 (see [DATABASE](../database/DATABASE.md)) are prepared and reviewed separately; none is applied by this decision.
- Provider, data-handling and validation policy are in [ADR-0012](0012-llm-port-data-handling-and-validation.md); website research and egress policy in [ADR-0013](0013-research-sources-and-outbound-fetch.md).
