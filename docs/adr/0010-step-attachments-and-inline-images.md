# Step attachments and inline images via private Storage and MIME parts

## Status

Accepted for implementation — 2026-09-25. Extends [PROJECT_CONTEXT §94](../product/PROJECT_CONTEXT.md) ("campaign attachments if supported") and satisfies the bounds required by [SECURITY_ARCHITECTURE](../security/SECURITY_ARCHITECTURE.md).

## Context

The sequence editor needs file attachments and inline images. Nothing stored files for messages, and the security architecture forbids implicit fetching of user-provided URLs, requires an egress policy for any attachment fetch, and requires bounded MIME depth, part count and size.

## Decision

- Files live in a **private** Supabase Storage bucket (`email-attachments`), written and read only by the backend with the service-role key, under `{workspace}/{campaign}/{step}/{random}-{name}`. The browser never talks to Storage; it uploads through the API and displays private images through short-lived signed URLs.
- `campaign_step_attachments` (migration 0025) is the metadata and integrity record: filename, allow-listed content type, size, SHA-256, disposition (`ATTACHMENT`/`INLINE`) and a stable content id. It is tenant-scoped with RLS, immutable (no `UPDATE`), and frozen with its sequence by a trigger. Copies (duplicate step/campaign) share the storage object and content id, and an object is deleted only when no row references it.
- **Inline images are sent as `cid:` MIME parts** (`multipart/related`; Graph `isInline`/`contentId`), not as URLs. A private-bucket URL expires and a public bucket would expose customer assets, create a hotlink/tracking surface and violate the egress rule. With `cid:` the only fetch is the server reading its own bucket at send time.
- Limits (server-enforced): 2.5 MiB per file and per step in total (keeps a message under Microsoft Graph's ~4 MB request limit once base64-encoded), 5 attachments and 10 inline images per step. Types are an extension allow-list (PNG/JPEG/GIF/WebP, PDF, TXT/CSV, DOCX/XLSX/PPTX); the stored content type comes from the extension, and the bytes must match it (signatures / OOXML structure inspected by member names only, nothing extracted). SVG, HTML, executables and archives are rejected.
- Send time: the worker loads the step's rows (immutable once frozen), downloads each object, verifies size and SHA-256, and builds the envelope. Missing/unreadable/corrupt → the message fails without invoking the provider; storage unavailable → retry. Test sends load and verify the same files first.
- The frozen sequence digest includes each attachment's content id, SHA-256 and disposition (only when present, so existing digests are unchanged). The message content digest is unchanged: the bytes are pinned by the frozen rows and re-verified at send time.

## Alternatives Considered

**Signed or public URLs for images** — rejected (expiry; exposure; egress policy). **Base64 `data:` images in the HTML** — rejected (mail-client support, size, sanitizer strips `data:`). **A snapshot column on `messages`** — rejected: it needs changes to the message snapshot guard and to the render path for no additional integrity, since the source rows are already immutable.

## Consequences

- The bucket must be created (private) in the Supabase dashboard, like `imports`, before the feature is used; migration 0025 does not create it.
- Storage existence is verified at send/test time, not at activation.
- Orphan objects (a step removed by cascade, an abandoned upload) are not cleaned up by a job; a lifecycle/cleanup task is an operations follow-up.
- Plain-text alternatives and an unsubscribe footer remain out of scope.
