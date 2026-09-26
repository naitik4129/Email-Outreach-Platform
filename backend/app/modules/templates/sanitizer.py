from __future__ import annotations

import re
from html.parser import HTMLParser

_DISALLOWED_TAGS = frozenset({
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "applet",
    "form",
    "input",
    "button",
    "select",
    "textarea",
    "link",
    "meta",
    "base",
    "frame",
    "frameset",
})

_UNSAFE_SCHEMES_RE = re.compile(
    r"^(?:\s*(?:javascript|vbscript|data):)", re.IGNORECASE
)
_DANGEROUS_STYLE_RE = re.compile(
    r"(?:expression|behavior|javascript:|vbscript:)", re.IGNORECASE
)


class _SafePreviewHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in _DISALLOWED_TAGS:
            self._skip_depth += 1
            return

        if self._skip_depth > 0:
            return

        safe_attrs: list[str] = []
        for attr_name, attr_val in attrs:
            attr_lower = attr_name.lower()

            # Strip all event handlers (onclick, onerror, onload, etc.)
            if attr_lower.startswith("on"):
                continue

            val_str = attr_val or ""

            # Check URI schemes for href and src
            if attr_lower in {"href", "src", "action", "formaction"}:
                # Decode and strip control characters
                clean_val = re.sub(r"[\x00-\x1f\s]+", "", val_str)
                if _UNSAFE_SCHEMES_RE.search(clean_val):
                    continue

            # Strip dangerous style constructs
            if attr_lower == "style":
                if _DANGEROUS_STYLE_RE.search(val_str):
                    continue

            # Escape attribute value
            escaped_val = (
                val_str.replace("&", "&amp;")
                .replace('"', "&quot;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            safe_attrs.append(f'{attr_name}="{escaped_val}"')

        attr_str = f" {' '.join(safe_attrs)}" if safe_attrs else ""
        self.output.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in _DISALLOWED_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return

        if self._skip_depth > 0:
            return

        self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        # Escape raw characters in data
        escaped_data = (
            data.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        self.output.append(escaped_data)

    def handle_entityref(self, name: str) -> None:
        if self._skip_depth > 0:
            return
        self.output.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._skip_depth > 0:
            return
        self.output.append(f"&#{name};")


_EMAIL_ALLOWED_TAGS = frozenset({
    "p", "br", "div", "span", "strong", "b", "em", "i", "u", "s", "strike",
    "a", "ul", "ol", "li", "blockquote", "h1", "h2", "h3", "hr", "img",
    "pre", "code", "table", "thead", "tbody", "tr", "td", "th",
})
_EMAIL_VOID_TAGS = frozenset({"br", "hr", "img"})
# Tags whose whole subtree (not just the tag) is discarded. Void elements such
# as <input>/<link>/<meta> are deliberately absent: they never get an end tag,
# so they are simply dropped like any other unknown tag.
_EMAIL_DROP_CONTENT_TAGS = frozenset({
    "script", "style", "iframe", "object", "applet", "form", "button",
    "select", "textarea", "frameset", "noscript", "template", "svg", "math",
    "head", "title",
})

_EMAIL_LINK_RE = re.compile(r"^(?:https?://|mailto:)", re.IGNORECASE)
_EMAIL_IMG_SRC_RE = re.compile(
    r"^(?:https://|cid:[A-Za-z0-9_.@-]{1,128}$)", re.IGNORECASE
)
_EMAIL_DIMENSION_RE = re.compile(r"^\d{1,4}%?$")
_EMAIL_ALIGN_RE = re.compile(r"^(?:left|right|center|justify)$", re.IGNORECASE)
_EMAIL_SPAN_RE = re.compile(r"^\d{1,2}$")

_COLOR = r"(?:#[0-9a-fA-F]{3,8}|rgba?\(\s*[\d.,\s%]{1,40}\)|[a-zA-Z]{3,30})"
_LENGTH = r"\d{1,3}(?:\.\d{1,2})?(?:px|pt|em|rem|%)"
_EMAIL_STYLE_PROPERTIES: dict[str, re.Pattern[str]] = {
    "color": re.compile(rf"^{_COLOR}$"),
    "background-color": re.compile(rf"^{_COLOR}$"),
    "font-family": re.compile(r"^[A-Za-z0-9 ,'\"_-]{1,200}$"),
    "font-size": re.compile(rf"^{_LENGTH}$"),
    "text-align": _EMAIL_ALIGN_RE,
    "font-weight": re.compile(r"^(?:normal|bold|[1-9]00)$", re.IGNORECASE),
    "font-style": re.compile(r"^(?:normal|italic)$", re.IGNORECASE),
    "text-decoration": re.compile(
        r"^(?:none|underline|line-through)(?: (?:underline|line-through))?$",
        re.IGNORECASE,
    ),
    "line-height": re.compile(rf"^(?:\d(?:\.\d{{1,2}})?|{_LENGTH})$"),
}


def _filter_email_style(style: str) -> str:
    kept: list[str] = []
    for declaration in style.split(";"):
        prop, sep, value = declaration.partition(":")
        if not sep:
            continue
        prop = prop.strip().lower()
        value = value.strip()
        pattern = _EMAIL_STYLE_PROPERTIES.get(prop)
        if pattern is not None and pattern.match(value):
            kept.append(f"{prop}: {value}")
    return "; ".join(kept)


def _escape_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class _EmailHTMLParser(HTMLParser):
    """Allow-list sanitizer for HTML that will actually be sent to recipients.

    Unlike the preview parser (deny-list), unknown tags/attributes are dropped
    so nothing outside the editor's vocabulary can reach a recipient. Text nodes
    keep `{{variable|fallback}}` placeholders intact; a `{{` at the start of an
    href/src is rejected because a variable could then supply the URL scheme
    (e.g. javascript:) after the render-time HTML escaping, which does not
    neutralise schemes.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self._skip_depth = 0

    def _safe_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        parts: list[str] = []
        for name, raw in attrs:
            name = name.lower()
            value = raw or ""
            compact = re.sub(r"[\x00-\x1f\s]+", "", value)
            if name == "style":
                filtered = _filter_email_style(value)
                if filtered:
                    parts.append(f'style="{_escape_attr(filtered)}"')
            elif tag == "a" and name == "href":
                if _EMAIL_LINK_RE.match(compact):
                    parts.append(f'href="{_escape_attr(value.strip())}"')
            elif tag == "a" and name == "title":
                parts.append(f'title="{_escape_attr(value)}"')
            elif tag == "img" and name == "src":
                if _EMAIL_IMG_SRC_RE.match(compact):
                    parts.append(f'src="{_escape_attr(value.strip())}"')
            elif tag == "img" and name in {"alt", "title"}:
                parts.append(f'{name}="{_escape_attr(value)}"')
            elif tag == "img" and name in {"width", "height"}:
                if _EMAIL_DIMENSION_RE.match(value.strip()):
                    parts.append(f'{name}="{value.strip()}"')
            elif name == "align" and _EMAIL_ALIGN_RE.match(value.strip()):
                parts.append(f'align="{value.strip().lower()}"')
            elif tag in {"td", "th"} and name in {"colspan", "rowspan"}:
                if _EMAIL_SPAN_RE.match(value.strip()):
                    parts.append(f'{name}="{value.strip()}"')
        if tag == "img" and not any(p.startswith("src=") for p in parts):
            # An <img> without a permitted src is meaningless and would render
            # as a broken/unsafe element.
            return "\x00drop"
        return f" {' '.join(parts)}" if parts else ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _EMAIL_DROP_CONTENT_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0 or tag not in _EMAIL_ALLOWED_TAGS:
            return
        attr_str = self._safe_attrs(tag, attrs)
        if attr_str == "\x00drop":
            return
        self.output.append(f"<{tag}{attr_str}>")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        # A self-closed drop-content tag (<script/>) has no matching end tag,
        # so it must not open a skip region.
        if tag.lower() in _EMAIL_DROP_CONTENT_TAGS:
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _EMAIL_DROP_CONTENT_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth > 0 or tag not in _EMAIL_ALLOWED_TAGS:
            return
        if tag in _EMAIL_VOID_TAGS:
            return
        self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        self.output.append(
            data.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )


def sanitize_email_html(html_content: str) -> str:
    """Sanitize authored email body HTML for storage and sending (allow-list).

    Applied when a step's body is written, never to legacy rows on unrelated
    saves. See _EmailHTMLParser for the policy.
    """
    if not html_content:
        return ""
    parser = _EmailHTMLParser()
    parser.feed(html_content)
    parser.close()
    return "".join(parser.output)


def sanitize_html_preview(html_content: str) -> str:
    """Sanitize customer-authored HTML for safe application preview.

    Removes active scripts, dangerous tags, event handlers, and unsafe URL schemes.
    Preserves formatting elements (p, div, a, img, table, b, i, span, etc.).
    """
    if not html_content:
        return ""

    parser = _SafePreviewHTMLParser()
    parser.feed(html_content)
    parser.close()
    return "".join(parser.output)
