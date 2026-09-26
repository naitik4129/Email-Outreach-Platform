# Message-level tracking events and open tracking

## Status

Accepted for implementation — 2026-09-26. Extends [EVENT_SYSTEM](../architecture/EVENT_SYSTEM.md) and [REPLY_SYNC](../architecture/REPLY_SYNC.md); consistent with the estimate-only stance on opens in [PROJECT_CONTEXT](../product/PROJECT_CONTEXT.md) (§62).

## Context

The platform recorded outcomes per *enrollment* (`recipient_outcomes`: REPLIED, HARD_BOUNCE, …) and had no notion of an open at all. The requested analytics — Sent, Bounced, Bounce Rate, Opened, Open Rate, Replied — are per *email*: a lead who receives three steps can have one bounce, two opens and one reply. Counting enrollments against messages gave rates whose numerator and denominator measured different things, bounces could not be attached to the step that was sent, and a bounce arriving after an enrollment finished was lost.

## Decision

- **New table `message_events`** (migration 0030): one row per `(workspace, message, kind)`, `kind` ∈ `OPENED`, `BOUNCED`, with `occurrence_count` and first/last timestamps. Idempotent upserts make duplicates, retries and late notifications unable to inflate distinct-message counts. Lead/campaign/step/mailbox are reached through the message FK rather than copied.
- **Open tracking** uses a signed pixel added to the outgoing envelope at send time (not at render time, which would change the frozen, digest-verified body). The public endpoint verifies an HMAC token without a database read, upserts one row, and returns an identical GIF for every outcome. It is served under `/api/v1/t/o/…`, already routed to the backend, so no proxy change is needed. It is opt-in (`OPEN_TRACKING_ENABLED`) and inert unless the base URL and signing key are set.
- **One bounce service** owns "an email bounced" for signed webhooks and for delivery-status notifications found during mailbox sync, so both apply identical hard/soft, suppression and sequence-stop rules. A DSN is attached to the original email by Message-ID; its claimed recipient is never trusted to suppress an address.
- **Metric definitions** live in one SQL helper. *Delivered* is an estimate (sent − bounced) because no supported provider returns delivery receipts; *opened* counts distinct delivered emails; open rate = opened ÷ delivered (estimated).
- **Correlation prerequisites**: every send records the Message-ID the provider actually used (Gmail read-back, Graph draft-then-send, SMTP header) and the provider thread id, because replies and DSNs can only be matched through them. This changes Microsoft sending from `sendMail` to create-draft-then-send and widens OAuth scopes to `gmail.readonly` / `Mail.ReadWrite` (existing connections must be reconnected).

## Consequences

- **Benefits:** correct, consistent per-email analytics; bounces and opens tied to campaign, step and lead; idempotent by construction; reply/bounce matching becomes possible for campaign mail for the first time.
- **Trade-offs:** an extra table and a public write endpoint (tightly scoped: signed token, opens only, counter-only column grants, workspace-scoped RLS); broader OAuth scopes (Google restricted-scope verification applies to public apps); Microsoft sending makes two API calls and a Graph draft-send flow that must be validated on a controlled mailbox before production use.
- **Risks:** open counts are inflated or hidden by privacy proxies (documented as an estimate). `message_events` rows for emails sent before this change do not exist, and their Message-IDs were never recorded, so replies/bounces for those emails cannot be matched by Message-ID.
- **Migration impact:** 0030 (new table, one column grant) and 0031 (SELECT grants for the reply worker). Both are prepared, not applied, and must be applied before the code that uses them runs.

## Alternatives Considered

**Store opens in `recipient_outcomes`** — keyed by enrollment, so per-email uniqueness and step attribution are impossible. **Append one row per open** — unbounded writes from scanners and no cheap distinct count. **Copy lead/campaign/step onto each event** — duplicates data the message already owns and needs privileges the public endpoint should not have. **Count bounces from `recipient_outcomes`** — enrollment-level and lost for finished enrollments. **Provider delivery webhooks for "Delivered"** — not available on Gmail, Graph or SMTP.
