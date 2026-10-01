"""Validated brand tokens for branded HTML emails (ADR-0017).

Everything a user, a website or a model can influence about an email's look is
reduced to these tokens, and every token is validated here *before* it can reach
the layout renderer: the renderer is platform-authored HTML that bypasses the
default sanitizer profile, so an unvalidated token would be an injection path.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FontKey = Literal["sans", "humanist", "serif", "times", "modern", "mono"]

# Email-safe stacks only: most clients ignore web fonts, and the sanitizer's
# font-family pattern (letters, digits, space, comma, quotes) must accept them.
FONT_STACKS: dict[str, str] = {
    "sans": "Arial, Helvetica, sans-serif",
    "humanist": "Verdana, Geneva, sans-serif",
    "serif": "Georgia, 'Times New Roman', serif",
    "times": "'Times New Roman', Times, serif",
    "modern": "Tahoma, Verdana, sans-serif",
    "mono": "'Courier New', Courier, monospace",
}

DEFAULT_PRIMARY = "#1f2937"
DEFAULT_ACCENT = "#2563eb"
DEFAULT_TEXT = "#111827"
DEFAULT_BACKGROUND = "#ffffff"
DEFAULT_FONT_KEY: FontKey = "sans"

MAX_URL_CHARS = 1000
MAX_CTA_LABEL_CHARS = 40

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# https only; no whitespace, quotes, angle brackets, backslashes or braces, so the
# value can be placed inside a double-quoted attribute and can never be read as a
# template variable by render_template_content.
_URL_TAIL_MAX = MAX_URL_CHARS - len("https://")
_HTTPS_URL_RE = re.compile(rf"^https://[^\s\"'<>\\{{}}`]{{3,{_URL_TAIL_MAX}}}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

ShortText = Annotated[str, Field(max_length=120)]


def is_https_url(value: str) -> bool:
    return bool(_HTTPS_URL_RE.match(value)) and "." in value.split("/")[2]


def clean_url(value: str) -> str:
    value = value.strip()
    if not is_https_url(value):
        raise ValueError("must be an https URL without spaces or quotes")
    return value


def clean_display_text(value: str) -> str:
    """Text that is HTML-escaped when rendered. `{{`/`}}` are rejected because the
    reference step is later passed through the variable renderer, which would
    treat them as placeholders."""
    if _CONTROL_RE.search(value):
        raise ValueError("must not contain control characters")
    if "{{" in value or "}}" in value:
        raise ValueError("must not contain template braces")
    return value.strip()


def clean_color(value: str) -> str:
    value = value.strip()
    if not _HEX_COLOR_RE.match(value):
        raise ValueError("must be a #rrggbb colour")
    return value.lower()


class BrandKit(BaseModel):
    """Look-and-feel tokens for an HTML email. All optional, all defaulted."""

    model_config = ConfigDict(extra="forbid")

    logo_url: str | None = Field(default=None, max_length=MAX_URL_CHARS)
    logo_alt: ShortText = ""
    primary: str = DEFAULT_PRIMARY
    accent: str = DEFAULT_ACCENT
    text: str = DEFAULT_TEXT
    background: str = DEFAULT_BACKGROUND
    font_key: FontKey = DEFAULT_FONT_KEY
    cta_url: str | None = Field(default=None, max_length=MAX_URL_CHARS)
    cta_label: str = Field(default="", max_length=MAX_CTA_LABEL_CHARS)

    @field_validator("logo_url", "cta_url")
    @classmethod
    def _urls(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return clean_url(value)

    @field_validator("primary", "accent", "text", "background")
    @classmethod
    def _colors(cls, value: str) -> str:
        return clean_color(value)

    @field_validator("logo_alt", "cta_label")
    @classmethod
    def _labels(cls, value: str) -> str:
        return clean_display_text(value)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio of two #rrggbb colours (1.0 to 21.0)."""

    def luminance(color: str) -> float:
        channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [
            c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            for c in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted(
        (luminance(foreground), luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def readable_on(background: str) -> str:
    """Black or white, whichever reads better on `background` (e.g. header text
    over the primary colour, or a button label over the accent colour)."""
    return (
        "#ffffff"
        if contrast_ratio("#ffffff", background)
        >= contrast_ratio("#000000", background)
        else "#000000"
    )


def brand_warnings(brand: BrandKit) -> list[str]:
    """Non-blocking problems worth telling the user about."""
    warnings: list[str] = []
    if contrast_ratio(brand.text, brand.background) < 4.5:
        warnings.append("text_contrast_low")
    if brand.cta_url and contrast_ratio(readable_on(brand.accent), brand.accent) < 4.5:
        warnings.append("button_contrast_low")
    return warnings
