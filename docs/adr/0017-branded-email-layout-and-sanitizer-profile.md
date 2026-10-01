# Branded HTML email layout and the scoped sanitizer profile

## Status

Accepted — 2026-09-30 (owner approved the plan). Companion to [ADR-0016](0016-api-process-ai-drafting-and-company-analysis.md); builds on [ADR-0010](0010-step-attachments-and-inline-images.md) and [ADR-0012](0012-llm-port-data-handling-and-validation.md).

## Context

A hyper-personalized campaign can be authored as **Text** (unstyled paragraphs, what the platform sends today) or **HTML** (a layout that matches the company's brand: logo, colours, font, optional call-to-action button). `sanitize_email_html` runs on every step write and deliberately strips padding, margin, border, border-radius, widths, `display`, `<style>`, `<button>`, `bgcolor`, `cellpadding` and web fonts, so a branded layout cannot be saved through it. The model never emits HTML ([ADR-0012](0012-llm-port-data-handling-and-validation.md)), and per-lead output is rebuilt from validated paragraphs by `assemble_body_html`.

## Decision

1. **One platform-authored layout, two callers.** `render_branded_email` builds a table-based, email-client-safe container (max width 600 px, brand-coloured header band with logo or company name, body in the brand font and text colour, links in the accent colour, optional bulletproof CTA button, footer with company name and site). It is used (a) when drafting writes a reference email into a step and (b) in `PersonalizationService.attempt()` to wrap the validated, sanitized paragraph fragment of a generated per-lead email, before the preheader is injected and the content digest is computed. Previews use the same seam, so a preview is exactly what will be sent. The thin-context fallback renders the reference step, which is already branded.
2. **Every brand token is validated, then escaped.** Colours must be `#rrggbb`; the font is a key into a fixed map of email-safe stacks; URLs (logo, CTA, site) must be `https`, at most 1000 characters, without whitespace, quotes, angle brackets or backslashes; company name, alt text and CTA label are HTML-escaped. Tokens are validated when the configuration is saved and again immediately before each render, so a hand-edited row cannot inject markup.
3. **Scoped sanitizer profile.** `sanitize_email_html` gains an opt-in `branded` profile. The default profile is unchanged byte for byte. `SequenceService` selects the profile **server-side** for a step write only when the campaign is `HYPER_PERSONALIZED` and its saved configuration has `email_format = "HTML"`, for AI writes and human edits alike; the client cannot request it. The profile additionally allows the CSS properties `padding`, `margin`, `border`, `border-radius`, `width`, `max-width` and `display` (only `block` or `inline-block`) with strict value patterns (numbers with `px`/`%`, `auto`, colours, `solid`; never `url()`, `expression`, `@`, `\` or `!important`), and the attributes `width` on `table`/`td`, `cellpadding`, `cellspacing`, `border`, `bgcolor`, `valign` and `role="presentation"` with strict value patterns. Scripts, `<style>`, `<link>`, event handlers, non-https image sources and every other rule remain forbidden.
4. **Editing.** A designed email is edited in source mode only; the visual editor cannot represent it and would flatten it. Text edits in the reference are always honoured by per-lead generation because the model reads the reference text; edits to layout markup affect the reference and the fallback only, since per-lead output always uses the platform layout.
5. **Logo.** Hot-linked from the company's own site (raster only: PNG, JPEG, GIF, WebP; SVG is not rendered by email clients). No upload or storage in this version. Many mail clients block remote images by default; the layout shows the company name as text so the message reads correctly without the image.
6. **Renderer version.** Branded output keeps the existing renderer versions; the digest already covers the full body. `messages.renderer_version` needs no change.

## Alternatives Considered

**Apply the layout only at send time from the brand kit**: keeps steps simple and the sanitizer untouched, but the step editor would not show what recipients get. The owner chose HTML in the step. **Relax the shared sanitizer for everyone**: widens Standard campaigns' attack surface for no benefit. **Let the model emit HTML**: violates ADR-0012. **Inline `cid:` logo attachments**: preflight rejects inline images in reference emails and per-lead generation has no attachment plumbing.

## Consequences

- A change to a shared security control, mitigated by scoping (hyper + HTML only), strict value patterns, and an XSS and attribute-injection test matrix that also proves the default profile is unchanged.
- Changing the brand, format or company after drafting changes the approval digest and requires regenerating emails and samples.
- No web fonts, no unsubscribe footer, no click tracking (all pre-existing platform gaps).
