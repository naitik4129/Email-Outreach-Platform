"""Understand a company from its website or from a typed description (ADR-0016).

Runs in the API process, holds no database session and writes nothing: the
result is returned to the user, who reviews and saves it through the ordinary
objective endpoint. Outbound fetches use the SSRF-hardened SafeFetcher with the
same policy as worker research (ADR-0013); page text is redacted of contact
details and instruction-like lines before a model sees it.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from app.core.errors import AppError
from app.modules.personalization.brand_extractor import (
    logo_candidates,
    stylesheet_urls,
    suggest_brand,
)
from app.modules.personalization.brand_kit import BrandKit, is_https_url
from app.modules.personalization.config_schema import CompanyProfile
from app.modules.personalization.page_signals import PageSignals, parse_page
from app.modules.personalization.ports import (
    CompanyAnalysisOutput,
    CompanyAnalysisRequest,
    DraftingModel,
    ModelError,
    PageText,
)
from app.modules.personalization.research.egress import (
    FetchBlocked,
    FetchFailed,
    FetchResponse,
    SafeFetcher,
    validate_url,
)
from app.modules.personalization.research.website import (
    looks_like_injection,
    normalize_company_url,
)
from app.modules.personalization.text_utils import redact_contacts

logger = logging.getLogger(__name__)

MAX_EXTRA_PAGES = 2
MAX_STYLESHEETS = 2
MAX_LOGO_TRIES = 3
MAX_LOGO_BYTES = 256 * 1024
MAX_CSS_BYTES = 256 * 1024
MAX_SITEMAP_BYTES = 256 * 1024
MIN_BLOCK_CHARS = 25
MAX_BLOCK_CHARS = 400
MAX_PAGE_TEXT_CHARS = 4_000
MAX_TOTAL_TEXT_CHARS = 12_000
MIN_USEFUL_TEXT_CHARS = 200
# Below MIN_USEFUL but at least this much (title + description + body): the site
# renders in the browser (a single-page app) or is very sparse. Still enough for
# the model to describe the company, so the user reviews it instead of hitting a
# dead end. Below this there is genuinely nothing to analyze.
MIN_MINIMAL_TEXT_CHARS = 60
BUSINESS_NAME_MIN, BUSINESS_NAME_MAX = 2, 120
BUSINESS_DESCRIPTION_MIN, BUSINESS_DESCRIPTION_MAX = 40, 1500

_RASTER_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
_LOGO_CONTENT_TYPES = (*_RASTER_TYPES, "image/svg+xml")
_CSS_TYPES = ("text/css",)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PAGE_HINTS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)(about|about-us|company|who-we-are|our-story)(/|$|\.)", re.I),
    re.compile(
        r"(^|/)(products?|services?|solutions?|platform|features?|what-we-do)(/|$|\.)",
        re.I,
    ),
    re.compile(r"(^|/)(customers?|why-[a-z-]+|use-cases?|pricing)(/|$|\.)", re.I),
)
_TITLE_SPLIT_RE = re.compile(r"\s+[|–—:\-]\s+")
_SITEMAP_LOC_RE = re.compile(r"<loc>\s*(https?://[^<\s]+)\s*</loc>", re.I)
_SITEMAP_SKIP_RE = re.compile(
    r"(^|/)(privacy|terms|cookies?|legal|login|log-in|sign-?in|sign-?up|register|"
    r"cart|checkout|account|unsubscribe)([a-z-]*)(/|$|\.)",
    re.I,
)
_SITEMAP_TYPES = ("text/xml", "application/xml", "text/plain")


@dataclass(frozen=True)
class AnalysisOutcome:
    source: str  # WEBSITE | MANUAL
    final_url: str | None
    profile: CompanyProfile
    brand: BrandKit | None
    suggestions: dict[str, str]
    pages_read: int
    warnings: tuple[str, ...]
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    brand_warnings: tuple[str, ...] = field(default_factory=tuple)


def _decode(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")


def _clean_text(value: str, limit: int, *, redact: bool = True) -> str:
    value = _CONTROL_RE.sub(" ", value or "")
    value = value.replace("{{", "").replace("}}", "")
    if redact:
        value = redact_contacts(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit].rstrip()


def _clean_list(
    values: tuple[str, ...], limit_items: int, limit_chars: int
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = _clean_text(raw, limit_chars)
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
        if len(out) >= limit_items:
            break
    return out


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _pick_pages(home_url: str, links: list[str], limit: int) -> list[str]:
    """Up to `limit` same-site pages, preferring about, then product/service,
    then customer/pricing pages."""
    home_host = _host(home_url)
    candidates: list[tuple[int, str]] = []
    seen: set[str] = {urlsplit(home_url).path or "/"}
    for href in links:
        try:
            absolute = urljoin(home_url, href.strip())
            parts = urlsplit(absolute)
            if (parts.hostname or "").lower().removeprefix("www.") != home_host:
                continue
            validate_url(absolute)
        except (ValueError, FetchBlocked):
            continue
        path = parts.path or "/"
        if path in seen or parts.query:
            continue
        for rank, pattern in enumerate(_PAGE_HINTS):
            if pattern.search(path):
                seen.add(path)
                candidates.append((rank, f"https://{parts.hostname}{path}"))
                break
    candidates.sort(key=lambda item: item[0])
    return [url for _, url in candidates[:limit]]


def _sitemap_pages(home_url: str, xml_text: str, limit: int) -> list[str]:
    """Up to `limit` same-site pages named by a sitemap. Used when the homepage
    carries almost no text (a site that renders in the browser): its sitemap still
    lists pages that were generated ahead of time. Product/about pages first, then
    the shallowest remaining pages; legal and account pages never."""
    home_host = _host(home_url)
    seen: set[str] = {urlsplit(home_url).path or "/"}
    ranked: list[tuple[int, int, int, str]] = []
    for order, loc in enumerate(_SITEMAP_LOC_RE.findall(xml_text)):
        try:
            parts = urlsplit(loc.strip())
            if (parts.hostname or "").lower().removeprefix("www.") != home_host:
                continue
            path = parts.path or "/"
            if (
                path in seen
                or parts.query
                or path.endswith((".xml", ".pdf"))
                or _SITEMAP_SKIP_RE.search(path)
            ):
                continue
            url = f"https://{parts.hostname}{path}"
            validate_url(url)
        except (ValueError, FetchBlocked):
            continue
        seen.add(path)
        rank = next(
            (i for i, pattern in enumerate(_PAGE_HINTS) if pattern.search(path)),
            len(_PAGE_HINTS),
        )
        ranked.append((rank, path.count("/"), order, url))
    ranked.sort()
    return [url for *_, url in ranked[:limit]]


def _page_text(signals: PageSignals, label: str, budget: int) -> PageText:
    blocks: list[str] = []
    used = 0
    seen: set[str] = set()
    for raw in signals.blocks:
        text = _clean_text(raw, MAX_BLOCK_CHARS)
        if len(text) < MIN_BLOCK_CHARS or looks_like_injection(text):
            continue
        key = text.lower()
        if key in seen:
            continue
        if used + len(text) > min(MAX_PAGE_TEXT_CHARS, budget):
            break
        seen.add(key)
        blocks.append(text)
        used += len(text)
    headings = [
        h
        for h in (_clean_text(x, 160) for x in signals.headings[:10])
        if h and not looks_like_injection(h)
    ]
    return PageText(
        label=label,
        title=_clean_text(signals.title, 160),
        meta_description=_clean_text(signals.meta_description, 400),
        headings=tuple(headings),
        nav_labels=tuple(
            n for n in (_clean_text(x, 40) for x in signals.nav_labels[:12]) if n
        ),
        blocks=tuple(blocks),
    )


def _text_size(page: PageText) -> int:
    return (
        len(page.meta_description)
        + sum(len(h) for h in page.headings)
        + sum(len(b) for b in page.blocks)
    )


def _guess_name(signals: PageSignals) -> str:
    for candidate in (signals.site_name, signals.jsonld_name):
        cleaned = _clean_text(candidate, BUSINESS_NAME_MAX)
        if cleaned:
            return cleaned
    parts = _TITLE_SPLIT_RE.split(signals.title or "")
    for part in parts:
        cleaned = _clean_text(part, 60)
        if cleaned:
            return cleaned
    return ""


def _magic_ok(body: bytes) -> bool:
    return (
        body.startswith(b"\x89PNG\r\n\x1a\n")
        or body.startswith(b"\xff\xd8\xff")
        or body.startswith(b"GIF8")
        or (body[:4] == b"RIFF" and body[8:12] == b"WEBP")
    )


class CompanyAnalyzer:
    def __init__(
        self,
        *,
        model: DraftingModel,
        fetcher: SafeFetcher | None = None,
        fetch_deadline_seconds: float = 20.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._model = model
        self._fetcher = fetcher or SafeFetcher()
        self._deadline = fetch_deadline_seconds
        self._clock = clock

    # ------------------------------------------------------------------ website

    def analyze_website(self, *, workspace_ref: str, raw_url: str) -> AnalysisOutcome:
        url = normalize_company_url(raw_url)
        if url is None:
            raise AppError(
                "invalid_website_url",
                "Enter a public website address such as acme.com.",
                status_code=422,
            )
        started = self._clock()
        home = self._fetch_home(url)
        signals = parse_page(_decode(home.body), home.final_url)
        site_host = _host(home.final_url)

        warnings: list[str] = []
        pages = [_page_text(signals, "home", MAX_TOTAL_TEXT_CHARS)]
        css = ""
        logo: str | None = None
        logo_reason: str | None = None

        extra_urls = _pick_pages(home.final_url, signals.links, MAX_EXTRA_PAGES)
        if (
            len(extra_urls) < MAX_EXTRA_PAGES
            and _text_size(pages[0]) < MIN_USEFUL_TEXT_CHARS
        ):
            # A browser-rendered homepage has no body text and no links to follow.
            # Its sitemap usually still lists pages that have real text.
            extra_urls.extend(
                u for u in self._sitemap_urls(home.final_url) if u not in extra_urls
            )
            del extra_urls[MAX_EXTRA_PAGES:]
        sheets = stylesheet_urls(signals, site_host, MAX_STYLESHEETS)
        candidates = logo_candidates(signals)[:MAX_LOGO_TRIES]

        remaining = max(1.0, self._deadline - (self._clock() - started))
        deadline_at = self._clock() + remaining
        # Not a context manager: leaving it would wait for straggling fetches and
        # defeat the deadline. Each fetch still has its own bounded deadline, so
        # an abandoned thread ends on its own and its result is ignored.
        pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="company-analysis")
        try:
            page_futures = [pool.submit(self._fetch_page, u) for u in extra_urls]
            sheet_futures = [pool.submit(self._fetch_css, u) for u in sheets]
            logo_future = pool.submit(self._pick_logo, candidates, deadline_at)
            done, not_done = wait(
                [*page_futures, *sheet_futures, logo_future], timeout=remaining
            )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if not_done:
            warnings.append("site_read_partial")

        budget = MAX_TOTAL_TEXT_CHARS - _text_size(pages[0])
        for future, page_url in zip(page_futures, extra_urls, strict=True):
            if future not in done:
                continue
            extra = future.result()
            if extra is None:
                continue
            label = urlsplit(page_url).path or "/"
            page = _page_text(parse_page(extra, page_url), label, max(budget, 0))
            if _text_size(page):
                pages.append(page)
                budget -= _text_size(page)
        for future in sheet_futures:
            if future in done and (text := future.result()):
                css += "\n" + text
        if logo_future in done:
            logo, logo_reason = logo_future.result()

        body_chars = sum(_text_size(p) for p in pages)
        # The title and description are real signal on sites that render their
        # content in the browser, where the fetched HTML has no body text.
        usable_chars = body_chars + sum(len(p.title) for p in pages)
        if usable_chars < MIN_MINIMAL_TEXT_CHARS:
            raise AppError(
                "website_thin",
                "We couldn't read enough from this website. Enter your business "
                "details instead.",
                status_code=422,
                details={"company_name": _guess_name(signals)},
            )

        if body_chars < MIN_USEFUL_TEXT_CHARS:
            warnings.append("site_text_limited")

        suggestion = suggest_brand(
            signals,
            css,
            logo_url=logo,
            company_name=_guess_name(signals),
            site_host=site_host,
        )
        warnings.extend(suggestion.warnings)
        if logo is None and logo_reason == "logo_svg_only":
            warnings = [w for w in warnings if w != "logo_not_found"]
            warnings.append("logo_svg_only")

        request = CompanyAnalysisRequest(
            workspace_ref=workspace_ref,
            source="WEBSITE",
            company_name=_guess_name(signals),
            pages=tuple(pages),
        )
        origin = f"https://{urlsplit(home.final_url).hostname}/"
        outcome = self._run_model(request, warnings)
        if outcome is None:
            profile = self._deterministic_profile(signals, origin)
            suggestions: dict[str, str] = {}
            model_name = None
            tokens = (0, 0, 0)
        else:
            profile = self._profile_from_output(outcome[0], "WEBSITE", origin, signals)
            suggestions = self._suggestions(outcome[0])
            model_name, tokens = outcome[1], outcome[2]

        logger.info(
            "company analysis completed",
            extra={
                "source": "WEBSITE",
                "host": site_host,
                "pages": len(pages),
                "warnings": sorted(set(warnings)),
            },
        )
        return AnalysisOutcome(
            source="WEBSITE",
            final_url=origin,
            profile=profile,
            brand=suggestion.brand,
            suggestions=suggestions,
            pages_read=len(pages),
            warnings=tuple(dict.fromkeys(warnings)),
            model=model_name,
            input_tokens=tokens[0],
            output_tokens=tokens[1],
            latency_ms=tokens[2],
        )

    # ------------------------------------------------------------------- manual

    def analyze_business(
        self, *, workspace_ref: str, company_name: str, description: str
    ) -> AnalysisOutcome:
        name = _CONTROL_RE.sub("", company_name or "").strip()
        text = _CONTROL_RE.sub("", description or "").strip()
        if (
            not BUSINESS_NAME_MIN <= len(name) <= BUSINESS_NAME_MAX
            or not BUSINESS_DESCRIPTION_MIN <= len(text) <= BUSINESS_DESCRIPTION_MAX
            or "{{" in name
            or "{{" in text
        ):
            raise AppError(
                "invalid_business_info",
                "Enter a company name and a short description of what the company "
                f"does ({BUSINESS_DESCRIPTION_MIN} to {BUSINESS_DESCRIPTION_MAX} "
                "characters).",
                status_code=422,
            )
        warnings: list[str] = []
        request = CompanyAnalysisRequest(
            workspace_ref=workspace_ref,
            source="MANUAL",
            company_name=name,
            description=text,
        )
        outcome = self._run_model(request, warnings)
        if outcome is None:
            profile = CompanyProfile(
                source="MANUAL",
                company_name=name,
                summary=_clean_text(text, 1000, redact=False),
            )
            suggestions: dict[str, str] = {}
            model_name, tokens = None, (0, 0, 0)
        else:
            profile = self._profile_from_output(outcome[0], "MANUAL", None, None)
            # The user's own name is authoritative for a typed description.
            profile = profile.model_copy(update={"company_name": name})
            suggestions = self._suggestions(outcome[0])
            model_name, tokens = outcome[1], outcome[2]
            if not any(suggestions.values()):
                warnings.append("business_info_thin")

        logger.info(
            "company analysis completed",
            extra={"source": "MANUAL", "warnings": sorted(set(warnings))},
        )
        return AnalysisOutcome(
            source="MANUAL",
            final_url=None,
            profile=profile,
            brand=None,
            suggestions=suggestions,
            pages_read=0,
            warnings=tuple(dict.fromkeys(warnings)),
            model=model_name,
            input_tokens=tokens[0],
            output_tokens=tokens[1],
            latency_ms=tokens[2],
        )

    # ----------------------------------------------------------------- internals

    def _fetch_home(self, url: str) -> FetchResponse:
        try:
            home = self._fetcher.fetch(url)
        except FetchBlocked as exc:
            if exc.reason == "offsite_redirect":
                raise AppError(
                    "website_redirected_offsite",
                    f"This website redirects to {exc.detail or 'another address'}. "
                    "Enter that address instead.",
                    status_code=422,
                    details={"host": exc.detail},
                ) from exc
            raise AppError(
                "website_blocked",
                "We can't read this website. Enter your business details instead.",
                status_code=422,
            ) from exc
        except FetchFailed as exc:
            raise AppError(
                "website_unreachable",
                "We couldn't reach this website. Check the address and try again, "
                "or enter your business details instead.",
                status_code=422,
            ) from exc
        if home.status_code in (401, 403, 429):
            raise AppError(
                "website_blocked",
                "This website doesn't allow automated reading. Enter your business "
                "details instead.",
                status_code=422,
            )
        if home.status_code != 200:
            raise AppError(
                "website_unreachable",
                "We couldn't load this website. Check the address and try again, or "
                "enter your business details instead.",
                status_code=422,
            )
        return home

    def _fetch_page(self, url: str) -> str | None:
        try:
            response = self._fetcher.fetch(url)
        except (FetchBlocked, FetchFailed):
            return None
        return _decode(response.body) if response.status_code == 200 else None

    def _sitemap_urls(self, home_url: str) -> list[str]:
        sitemap = f"https://{urlsplit(home_url).hostname}/sitemap.xml"
        try:
            response = self._fetcher.fetch(
                sitemap, content_types=_SITEMAP_TYPES, max_bytes=MAX_SITEMAP_BYTES
            )
        except (FetchBlocked, FetchFailed):
            return []
        if response.status_code != 200:
            return []
        return _sitemap_pages(home_url, _decode(response.body), MAX_EXTRA_PAGES)

    def _fetch_css(self, url: str) -> str | None:
        try:
            response = self._fetcher.fetch(
                url, content_types=_CSS_TYPES, max_bytes=MAX_CSS_BYTES
            )
        except (FetchBlocked, FetchFailed):
            return None
        return _decode(response.body) if response.status_code == 200 else None

    def _pick_logo(
        self, candidates: list[str], deadline_at: float
    ) -> tuple[str | None, str | None]:
        """First candidate that is really a raster image within the size cap.
        Returns (url, None), or (None, reason) explaining the last rejection."""
        reason: str | None = None
        for url in candidates:
            if not is_https_url(url) or self._clock() >= deadline_at:
                continue
            try:
                response = self._fetcher.fetch(
                    url, content_types=_LOGO_CONTENT_TYPES, max_bytes=MAX_LOGO_BYTES
                )
            except (FetchBlocked, FetchFailed):
                continue
            if response.status_code != 200:
                continue
            if response.content_type == "image/svg+xml":
                reason = reason or "logo_svg_only"
                continue
            if len(response.body) >= MAX_LOGO_BYTES or not _magic_ok(response.body):
                continue
            return url, None
        return None, reason

    def _run_model(
        self, request: CompanyAnalysisRequest, warnings: list[str]
    ) -> tuple[CompanyAnalysisOutput, str, tuple[int, int, int]] | None:
        try:
            result = self._model.analyze_company(request)
        except ModelError as exc:
            # Never block the user on the model: log the safe code and fall back
            # to a deterministic profile they can edit.
            logger.warning(
                "company analysis model call failed", extra={"code": exc.code}
            )
            warnings.append("analysis_model_failed")
            return None
        return (
            result.output,
            result.model,
            (result.input_tokens, result.output_tokens, result.latency_ms),
        )

    @staticmethod
    def _suggestions(output: CompanyAnalysisOutput) -> dict[str, str]:
        return {
            "objective": _clean_text(output.suggested_objective, 1000),
            "offer": _clean_text(output.suggested_offer, 1000),
            "cta": _clean_text(output.suggested_cta, 500),
            "tone": _clean_text(output.tone_of_voice, 200),
        }

    @staticmethod
    def _profile_from_output(
        output: CompanyAnalysisOutput,
        source: str,
        url: str | None,
        signals: PageSignals | None,
    ) -> CompanyProfile:
        name = _clean_text(output.company_name, BUSINESS_NAME_MAX)
        if not name and signals is not None:
            name = _guess_name(signals)
        return CompanyProfile(
            source=source,  # type: ignore[arg-type]
            url=url if url and is_https_url(url) else None,
            company_name=name or "Your company",
            summary=_clean_text(output.description, 1000),
            services=_clean_list(output.services, 8, 80),
            industries=_clean_list(output.industries, 6, 60),
            audience=_clean_text(output.target_audience, 400),
            tone_of_voice=_clean_text(output.tone_of_voice, 200),
            key_messages=_clean_list(output.key_messages, 5, 150),
        )

    @staticmethod
    def _deterministic_profile(signals: PageSignals, origin: str) -> CompanyProfile:
        """What can be said without a model: the site's own name and description."""
        return CompanyProfile(
            source="WEBSITE",
            url=origin if is_https_url(origin) else None,
            company_name=_guess_name(signals) or "Your company",
            summary=_clean_text(signals.meta_description, 1000),
        )
