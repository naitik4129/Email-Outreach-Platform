# Research sources and outbound website fetch policy

## Status

Accepted for implementation — 2026-09-26, together with [ADR-0011](0011-hyper-personalized-campaign-type.md). Satisfies the requirement in [SECURITY_ARCHITECTURE](../security/SECURITY_ARCHITECTURE.md) that any "enrichment integration requires its own URL/egress policy".

## Context

Personalization quality depends on facts about the lead's company. The lead snapshot already carries company fields and a website URL. The owner wants v1 to also read the company website. That is an outbound fetch to a user-supplied URL, which the security architecture forbids doing implicitly (SSRF, internal scanning, data leakage, resource exhaustion).

## Decision

- **Port.** `ResearchSource.fetch()` returns bounded, sanitized text plus source metadata. `WebsiteResearchSource` is the only v1 implementation; enrichment APIs can be added later as separate sources under their own ADR.
- **Seed.** Only the lead's own `company_website` value. No search engines, no links followed except one same-host "about" page discovered on the homepage.
- **Egress policy** (extends the SMTP/IMAP rules and reuses `mailboxes/providers/ssrf.py`, whose unsafe-address check becomes a public function without behaviour change):
  - `https` only, port 443 only, no credentials in URL.
  - Resolve every A/AAAA answer; deny the request if **any** answer is loopback, private, link-local, multicast, unspecified, IPv4-mapped equivalents or a cloud metadata address. Pin one permitted IP for the connection and preserve hostname TLS verification (SNI/Host), so DNS rebinding cannot change the target.
  - Redirects are handled manually: at most 3, same registrable host only, each hop re-resolved and re-validated.
  - `trust_env=False` (no proxies), no cookies, no authorization headers, fixed User-Agent.
  - Total deadline about 8 s; response streamed and capped at 512 KiB; only `text/html` or `text/plain`.
  - The fetch never runs inside a database transaction.
- **Extraction.** Standard-library HTML parsing keeps visible text only (drops script, style, hidden and comment content), strips instruction-like lines, and caps the result at about 6,000 characters. The text is treated as untrusted data in prompts ([ADR-0012](0012-llm-port-data-handling-and-validation.md)).
- **Cache.** `personalization_research_cache` is per workspace (never shared across tenants), keyed by workspace, source and normalized-URL hash. Positive TTL 7 days; negative results (empty/blocked/error) about 1 hour. Rows are readable/writable only by the personalization worker role; expired rows are purged opportunistically by the personalization worker for its own workspace (the cache is workspace-scoped, so a cross-tenant maintenance sweep cannot see it).
- **Budget.** Fetches count against a per-workspace daily cap in `personalization_usage_daily`.
- **Best effort.** A failed or blocked fetch degrades the context; it never fails a message by itself. Thin resulting context follows the fallback in ADR-0011.
- **Open items to settle before production enablement:** whether `robots.txt` must be honoured; whether to expose the researched excerpt in previews (planned: yes, ≤500 characters, with source URL).

## Alternatives Considered

**Fetch through a third-party scraping/enrichment API** — new subprocessor, cost and secrets; deferred behind the port. **Share the cache across tenants** — cheaper but weakens isolation; rejected. **Follow arbitrary links / crawl** — expands SSRF and cost surface; rejected. **Headless browser rendering** — heavy, larger attack surface; rejected for v1.

## Consequences

- Sites that need JavaScript rendering or block bots yield thin context and use the reference fallback.
- The egress helper must be tested with a fake resolver (mixed public/private answers, mapped IPv6, metadata address, redirect to a private host, oversize body, wrong content type, DNS timeout).
- The worker's network egress should additionally be restricted at the infrastructure layer where available; the application check is not the only control.
