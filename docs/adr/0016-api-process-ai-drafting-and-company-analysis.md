# API-process AI drafting and company analysis for hyper-personalized campaigns

## Status

Accepted — 2026-09-30 (owner approved the plan). **Amends [ADR-0012](0012-llm-port-data-handling-and-validation.md)** (where the model key lives, what data may be sent) and **[ADR-0013](0013-research-sources-and-outbound-fetch.md)** (who may perform outbound fetches). [ADR-0011](0011-hyper-personalized-campaign-type.md) still governs the campaign type and the just-in-time per-lead pipeline; this ADR only changes how the *reference emails* and *objective* are authored.

## Context

Hyper-personalized campaigns ([ADR-0011](0011-hyper-personalized-campaign-type.md)) need a user-authored objective and one hand-written reference email per step. The team cannot maintain these reliably. The owner wants the platform to study the company (a website, or a name and description when there is no website), then draft the objective suggestions and every reference email, in one Sequence tab, for either a plain-text or a branded HTML style.

Existing constraints: ADR-0012 confines `PERSONALIZATION_OPENAI_API_KEY` to the `personalization` worker group; ADR-0013 places website fetches in that worker; `app_worker_general` cannot write `sequence_steps`; `app_api` cannot write `personalization_usage_daily`; the API's database connections are scarce (small pool shared through a Supabase pooler).

## Decision

1. **The API process may call the model.** Two operations run synchronously in an API request, each behind `campaigns.draft`, only for `HYPER_PERSONALIZED` campaigns in `DRAFT`, and only when `PERSONALIZATION_ENABLED` is true:
   - *company analysis*: turns a website (fetched) or a user-typed name and description into a structured company profile, a brand kit (website only) and objective suggestions. It **writes nothing**; the user reviews and saves through the existing objective endpoint.
   - *reference drafting*: turns the saved objective (and profile) into the reference emails for the whole sequence, or regenerates one step, and writes them into `sequence_steps` through `SequenceService` in a single transaction.
2. **Key location (amends ADR-0012).** The same `PERSONALIZATION_OPENAI_API_KEY` is delivered to the `backend` service (Compose loads `.env.personalization` there too, optional). The API refuses these operations with `personalization_not_configured` when the key or model is missing. The per-lead JIT path is unchanged and still runs only in `worker-personalization`.
3. **Data sent to the provider (amends ADR-0012).** Only workspace-authored text (objective, company name and description) and public website text. **No lead data, ever.** Emails and phone numbers are stripped from website text before it is sent. Website text is untrusted: it is placed in the JSON data object, never in instructions, the model has no tools, and output is length-capped, validated and shown to the user before use. Logs carry ids, codes, counts, timings and the website *host* only.
4. **Outbound fetch from the API (amends ADR-0013).** The API may fetch a user-supplied website using the same `SafeFetcher` policy (https/443 only, public addresses only, DNS pinned, at most three same-host redirects, bounded time and size). Per-call options allow same-host CSS and a logo image with tighter size caps; every other rule is unchanged. A redirect to another host is reported to the user, never followed. The fetch never runs inside a database transaction. Infrastructure-level egress restriction for the API is recommended and is an operator task.
5. **Connection discipline.** The request commits and releases its pooled connection before any fetch or model call, and drafting re-opens a fresh transaction (`SET LOCAL ROLE app_api` plus the transaction context) before writing. Nothing is written until the model output has passed the deterministic validator; a failure writes nothing.
6. **Optimistic concurrency.** Drafting records the versions of the steps it read and writes with `expected_version`; a concurrent edit yields `409 conflict` and no partial result. Existing steps are updated in place (never deleted and recreated) because preview rows reference step ids with `ON DELETE RESTRICT`.
7. **No spend guard.** By owner decision there is no per-workspace budget or rate limit for these operations (no `personalization_usage_daily` kind, no migration). Model calls are bounded per request (three quality attempts, one transient retry, a hard deadline). The residual risk is that any `campaigns.draft` user can trigger repeated paid calls.
8. **No schema change.** New data lives in the existing `campaign_sequences.personalization_config` jsonb (optional `email_format`, `company`, `brand`), inside the existing 16 KiB check. Unset new keys are omitted from the canonical form, so existing configs, approval digests and frozen-sequence digests are byte-identical.
9. **Per-lead prompt unchanged.** The per-lead prompt receives only the original objective fields, so the prompt bytes and `PROMPT_VERSION` do not change and existing approvals stay valid. The new prompts have their own version constants that are not part of the approval digest.

## Alternatives Considered

**Keep the key worker-only and make drafting asynchronous** (enqueue, poll): keeps ADR-0012 intact and avoids holding an API thread, but needs a job table or result channel, a write path the worker role does not have, and a second polling UI. The owner chose synchronous; the connection-release rule bounds its cost. **Fetch only in the worker**: consistent with ADR-0013, but the analysis result must reach the user interactively and nothing durable is produced. **A per-workspace budget table**: rejected by the owner (see 7). **New `sequence_steps` columns** for AI provenance: unnecessary.

## Consequences

- The API process now holds a provider secret and performs outbound HTTP to user-supplied hosts; the SSRF policy and the "no lead data" rule are the controls. Provider retention and terms remain an open pre-production item (ADR-0012).
- A synchronous request can occupy an API worker thread for up to its deadline (about 45 s). Connections are released, threads are not.
- Redeploy required: the `backend` service must load `.env.personalization` (Compose >= 2.24, already required).
- Rolling back to code that predates the new config keys makes such configs unparseable (`extra="forbid"`); deploy forward only, or clear the keys first.
