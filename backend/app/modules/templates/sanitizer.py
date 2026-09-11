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
