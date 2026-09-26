"""Company-website research source (ADR-0013).

Seeds only from the lead's own company website. Fetches the homepage and at
most one same-host "about" page, keeps visible text only (scripts, styles,
navigation, hidden and comment content are dropped), removes instruction-like
lines, and bounds the result. The text is treated as untrusted data downstream.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from app.modules.personalization.ports import ResearchDocument
from app.modules.personalization.research.egress import (
    FetchBlocked,
    FetchFailed,
    FetchResponse,
    SafeFetcher,
    validate_url,
)

MAX_TEXT_CHARS = 6000
MAX_SNIPPET_CHARS = 240
MIN_BLOCK_CHARS = 40
MAX_SNIPPETS = 6
_ABOUT_HINT_RE = re.compile(
    r"(^|/)(about|about-us|company|who-we-are|our-story)(/|$|\.)", re.IGNORECASE
)
_INJECTION_RE = re.compile(
    r"(ignore\s+(all|any|the|your|previous|prior|above)|disregard|system\s+prompt|"
    r"you\s+are\s+(a|an|now)\b|as\s+an\s+ai\b|assistant\s*:|<\|)",
    re.IGNORECASE,
)
_SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "nav",
        "footer",
        "form",
        "iframe",
        "head",
    }
)
_BLOCK_TAGS = frozenset({"p", "li", "h1", "h2", "h3", "title"})
_VOID_TAGS = frozenset(
    {
        "meta",
        "link",
        "br",
        "img",
        "input",
        "hr",
        "area",
        "base",
        "col",
        "embed",
        "source",
        "track",
        "wbr",
    }
)


def _is_hidden(attrs: list[tuple[str, str | None]]) -> bool:
    for name, value in attrs:
        name = name.lower()
        value = (value or "").lower()
        if name == "hidden":
            return True
        if name == "aria-hidden" and value == "true":
            return True
        if name == "style" and re.search(
            r"display\s*:\s*none|visibility\s*:\s*hidden", value
        ):
            return True
    return False


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.meta_description: str | None = None
        self.links: list[str] = []
        self._skip = 0
        self._stack: list[bool] = []  # per open non-void tag: does it count toward skip
        self._buffer: list[str] = []
        self._in_block = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "meta":
            attr = {k.lower(): (v or "") for k, v in attrs}
            if (
                attr.get("name", "").lower() in ("description", "og:description")
                or attr.get("property", "").lower() == "og:description"
            ):
                self.meta_description = self.meta_description or attr.get("content", "")
            return
        if tag == "a":
            href = dict((k.lower(), v or "") for k, v in attrs).get("href", "")
            if href:
                self.links.append(href)
        if tag in _VOID_TAGS:
            return
        skipped = tag in _SKIP_TAGS or _is_hidden(attrs)
        self._stack.append(skipped)
        if skipped:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self._flush()
            self._in_block += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS or not self._stack:
            return
        skipped = self._stack.pop()
        if skipped:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self._flush()
            self._in_block = max(0, self._in_block - 1)

    def handle_data(self, data: str) -> None:
        if not self._skip and self._in_block:
            self._buffer.append(data)

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
        self._buffer = []
        if text:
            self.blocks.append(text)

    def close(self) -> None:
        super().close()
        self._flush()


def _clean_block(text: str) -> str | None:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < MIN_BLOCK_CHARS or _INJECTION_RE.search(text):
        return None
    if len(text) > MAX_SNIPPET_CHARS:
        cut = text[:MAX_SNIPPET_CHARS].rsplit(" ", 1)[0]
        text = cut.rstrip(",;:") + "..."
    return text


def extract_blocks(html_text: str) -> tuple[list[str], list[str]]:
    """(useful text blocks in priority order, raw link hrefs)."""
    parser = _PageParser()
    parser.feed(html_text)
    parser.close()
    ordered: list[str] = []
    if parser.meta_description:
        ordered.append(parser.meta_description)
    ordered.extend(parser.blocks)
    seen: set[str] = set()
    blocks: list[str] = []
    for raw in ordered:
        cleaned = _clean_block(raw)
        if cleaned is None or cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        blocks.append(cleaned)
    return blocks, parser.links


def snippets_from_text(text: str) -> tuple[str, ...]:
    return tuple(line for line in text.split("\n") if line.strip())[:MAX_SNIPPETS]


def _decode(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")


def find_about_url(base_url: str, links: list[str]) -> str | None:
    base_host = (urlsplit(base_url).hostname or "").lower()
    for href in links:
        absolute = urljoin(base_url, href)
        try:
            parts = urlsplit(absolute)
            host = (parts.hostname or "").lower()
            validate_url(absolute)
        except (ValueError, FetchBlocked):
            continue
        if host.removeprefix("www.") != base_host.removeprefix("www."):
            continue
        if _ABOUT_HINT_RE.search(parts.path or ""):
            return f"https://{host}{parts.path}"
    return None


def normalize_company_url(raw: str) -> str | None:
    """Accepts 'acme.com', 'http://acme.com' or 'https://acme.com/x'; returns an
    https URL or None. http is upgraded, never fetched."""
    value = (raw or "").strip()
    if not value:
        return None
    if "://" not in value:
        value = f"https://{value}"
    elif value.lower().startswith("http://"):
        value = "https://" + value[len("http://") :]
    try:
        _, _, normalized = validate_url(value)
    except FetchBlocked:
        return None
    return normalized


class WebsiteResearchSource:
    def __init__(self, fetcher: SafeFetcher) -> None:
        self._fetcher = fetcher

    def fetch(self, url: str) -> ResearchDocument:
        try:
            home = self._fetcher.fetch(url)
        except FetchBlocked:
            return ResearchDocument(
                source="WEBSITE", url=url, text="", status="BLOCKED"
            )
        except FetchFailed:
            return ResearchDocument(source="WEBSITE", url=url, text="", status="ERROR")

        status = self._status_for(home)
        if status != "OK":
            return ResearchDocument(source="WEBSITE", url=url, text="", status=status)  # type: ignore[arg-type]

        blocks, links = extract_blocks(_decode(home.body))
        about_url = find_about_url(home.final_url, links)
        if about_url and about_url != home.final_url:
            try:
                about = self._fetcher.fetch(about_url)
                if self._status_for(about) == "OK":
                    about_blocks, _ = extract_blocks(_decode(about.body))
                    seen = {b.lower() for b in blocks}
                    blocks.extend(b for b in about_blocks if b.lower() not in seen)
            except (FetchBlocked, FetchFailed):
                pass  # the homepage alone is enough

        text = ""
        kept: list[str] = []
        for block in blocks:
            if len(text) + len(block) + 1 > MAX_TEXT_CHARS:
                break
            kept.append(block)
            text = "\n".join(kept)
        if not text:
            return ResearchDocument(
                source="WEBSITE", url=home.final_url, text="", status="EMPTY"
            )
        return ResearchDocument(
            source="WEBSITE",
            url=home.final_url,
            text=text,
            status="OK",
            snippets=snippets_from_text(text),
        )

    @staticmethod
    def _status_for(response: FetchResponse) -> str:
        code = response.status_code
        if code == 200:
            return "OK"
        if code in (404, 410):
            return "EMPTY"
        if code in (401, 403, 429):
            return "BLOCKED"
        return "ERROR"
