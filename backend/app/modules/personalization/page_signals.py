"""Read one web page into the signals company analysis needs (ADR-0016).

Standard-library `HTMLParser` only. Nothing here does I/O; it turns already
fetched HTML into text and hints (title, description, headings, navigation,
logo/icon/stylesheet links, colours declared inline). All text is untrusted and is
cleaned again before a model sees it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

MAX_INLINE_CSS_CHARS = 100_000
MAX_JSONLD_CHARS = 20_000
_MAX_HEADINGS = 16
_MAX_NAV = 20
_MAX_BLOCKS = 120
_MAX_LINKS = 400

_TEXT_SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "iframe",
        "form",
        "head",
        "footer",
    }
)
_BLOCK_TAGS = frozenset({"p", "li", "h1", "h2", "h3", "blockquote"})
_HEADING_TAGS = frozenset({"h1", "h2", "h3"})
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
_LOGO_HINT_RE = re.compile(r"logo|brand(?:mark|ing)?\b|wordmark", re.IGNORECASE)


@dataclass
class LogoCandidate:
    url: str
    in_header: bool
    hinted: bool


@dataclass
class PageSignals:
    title: str = ""
    meta_description: str = ""
    site_name: str = ""
    og_image: str | None = None
    theme_color: str | None = None
    icons: list[tuple[str, str, int]] = field(default_factory=list)  # rel, href, size
    stylesheets: list[str] = field(default_factory=list)
    inline_css: str = ""
    jsonld_logo: str | None = None
    jsonld_name: str = ""
    logo_images: list[LogoCandidate] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    nav_labels: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


def _hidden(attrs: dict[str, str]) -> bool:
    if "hidden" in attrs or attrs.get("aria-hidden", "").lower() == "true":
        return True
    return bool(
        re.search(
            r"display\s*:\s*none|visibility\s*:\s*hidden",
            attrs.get("style", "").lower(),
        )
    )


def _icon_size(sizes: str) -> int:
    match = re.match(r"(\d{1,4})x\d{1,4}", (sizes or "").strip().lower())
    return int(match.group(1)) if match else 0


def _jsonld_values(node: Any, out: dict[str, str]) -> None:
    """Pull an Organization's logo and name out of a JSON-LD document."""
    if isinstance(node, list):
        for item in node:
            _jsonld_values(item, out)
    elif isinstance(node, dict):
        kind = node.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if any(
            isinstance(k, str)
            and k in {"Organization", "Corporation", "LocalBusiness", "WebSite"}
            for k in kinds
        ):
            logo = node.get("logo")
            if isinstance(logo, dict):
                logo = logo.get("url") or logo.get("contentUrl")
            if isinstance(logo, str) and "logo" not in out:
                out["logo"] = logo
            name = node.get("name")
            if isinstance(name, str) and "name" not in out:
                out["name"] = name
        graph = node.get("@graph")
        if graph:
            _jsonld_values(graph, out)


class _SignalParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.signals = PageSignals()
        self._stack: list[str] = []  # open non-void tags
        self._skip = 0
        self._skip_flags: list[bool] = []
        self._header = 0
        self._nav = 0
        self._capture: str | None = None  # "title" | "style" | "jsonld"
        self._capture_buffer: list[str] = []
        self._block_depth = 0
        self._block_tag = ""
        self._block_buffer: list[str] = []
        self._anchor_buffer: list[str] | None = None
        self._css_total = 0

    # -- helpers --------------------------------------------------------------

    def _absolute(self, href: str) -> str:
        return urljoin(self.base_url, href.strip())

    def _flush_block(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._block_buffer)).strip()
        self._block_buffer = []
        if not text:
            return
        if self._block_tag in _HEADING_TAGS:
            if len(self.signals.headings) < _MAX_HEADINGS:
                self.signals.headings.append(text)
        elif len(self.signals.blocks) < _MAX_BLOCKS:
            self.signals.blocks.append(text)

    # -- parser callbacks -----------------------------------------------------

    def handle_starttag(
        self, tag: str, attrs_list: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        attrs = {k.lower(): (v or "") for k, v in attrs_list}
        s = self.signals

        if tag == "meta":
            name = attrs.get("name", "").lower()
            prop = attrs.get("property", "").lower()
            content = attrs.get("content", "").strip()
            if name == "description" or prop == "og:description":
                s.meta_description = s.meta_description or content
            elif prop == "og:site_name":
                s.site_name = s.site_name or content
            elif prop == "og:image" or name == "twitter:image":
                s.og_image = s.og_image or (
                    self._absolute(content) if content else None
                )
            elif name == "theme-color":
                s.theme_color = s.theme_color or content
            return
        if tag == "link":
            rel = attrs.get("rel", "").lower()
            href = attrs.get("href", "")
            if href:
                if "stylesheet" in rel.split():
                    s.stylesheets.append(self._absolute(href))
                elif "icon" in rel:
                    s.icons.append(
                        (rel, self._absolute(href), _icon_size(attrs.get("sizes", "")))
                    )
            return
        if tag == "img":
            src = attrs.get("src") or attrs.get("data-src") or ""
            if src and not src.startswith("data:"):
                hint_text = " ".join(
                    attrs.get(k, "") for k in ("class", "id", "alt", "src", "data-src")
                )
                s.logo_images.append(
                    LogoCandidate(
                        url=self._absolute(src),
                        in_header=self._header > 0 or self._nav > 0,
                        hinted=bool(_LOGO_HINT_RE.search(hint_text)),
                    )
                )
            return
        if tag in _VOID_TAGS:
            return

        if tag == "title":
            self._capture, self._capture_buffer = "title", []
        elif tag == "style":
            self._capture, self._capture_buffer = "style", []
        elif tag == "script" and "ld+json" in attrs.get("type", "").lower():
            self._capture, self._capture_buffer = "jsonld", []

        skipped = tag in _TEXT_SKIP_TAGS or _hidden(attrs)
        self._stack.append(tag)
        self._skip_flags.append(skipped)
        if skipped:
            self._skip += 1
        if tag == "header":
            self._header += 1
        if tag == "nav":
            self._nav += 1
        if tag == "a":
            if len(s.links) < _MAX_LINKS and attrs.get("href"):
                s.links.append(attrs["href"])
            if (self._nav or self._header) and not self._skip:
                self._anchor_buffer = []
        if tag in _BLOCK_TAGS and not self._skip and not self._nav:
            self._flush_block()
            self._block_depth += 1
            self._block_tag = tag

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS or not self._stack:
            return
        if self._capture and (
            (tag == "title" and self._capture == "title")
            or (tag == "style" and self._capture == "style")
            or (tag == "script" and self._capture == "jsonld")
        ):
            self._finish_capture()
        if tag in self._stack:
            # Pop up to the matching open tag (tolerates unclosed <li>/<p>).
            while self._stack:
                popped = self._stack.pop()
                skipped = self._skip_flags.pop()
                if skipped:
                    self._skip = max(0, self._skip - 1)
                if popped == "header":
                    self._header = max(0, self._header - 1)
                if popped == "nav":
                    self._nav = max(0, self._nav - 1)
                if popped == "a" and self._anchor_buffer is not None:
                    label = re.sub(r"\s+", " ", "".join(self._anchor_buffer)).strip()
                    if 1 < len(label) <= 40 and len(self.signals.nav_labels) < _MAX_NAV:
                        if label not in self.signals.nav_labels:
                            self.signals.nav_labels.append(label)
                    self._anchor_buffer = None
                if popped in _BLOCK_TAGS and self._block_depth:
                    self._flush_block()
                    self._block_depth -= 1
                if popped == tag:
                    break

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._capture_buffer.append(data)
            return
        if self._skip:
            return
        if self._anchor_buffer is not None:
            self._anchor_buffer.append(data)
        if self._block_depth and not self._nav:
            self._block_buffer.append(data)

    def _finish_capture(self) -> None:
        text = "".join(self._capture_buffer)
        kind, self._capture, self._capture_buffer = self._capture, None, []
        s = self.signals
        if kind == "title":
            s.title = re.sub(r"\s+", " ", text).strip()
        elif kind == "style":
            room = MAX_INLINE_CSS_CHARS - self._css_total
            if room > 0:
                s.inline_css += text[:room] + "\n"
                self._css_total += min(len(text), room)
        elif kind == "jsonld" and len(text) <= MAX_JSONLD_CHARS:
            try:
                found: dict[str, str] = {}
                _jsonld_values(json.loads(text), found)
            except ValueError:
                return
            if found.get("logo") and not s.jsonld_logo:
                s.jsonld_logo = self._absolute(found["logo"])
            if found.get("name") and not s.jsonld_name:
                s.jsonld_name = found["name"].strip()

    def close(self) -> None:
        super().close()
        self._flush_block()


def parse_page(html_text: str, base_url: str) -> PageSignals:
    """Never raises on malformed HTML (HTMLParser is lenient); returns whatever
    could be read."""
    parser = _SignalParser(base_url)
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:  # noqa: BLE001 - a hostile/garbled page must not fail analysis
        pass
    return parser.signals
