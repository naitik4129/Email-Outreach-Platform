"""Suggest a brand kit from a website (ADR-0016/0017).

Everything here is a *suggestion* the user reviews and edits; extraction from
arbitrary sites is heuristic. Pure functions on already-fetched text, standard
library only. The one network step (checking that a logo is a real raster image)
lives in company_analysis so this module stays I/O free.
"""

from __future__ import annotations

import colorsys
import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.modules.personalization.brand_kit import (
    DEFAULT_ACCENT,
    DEFAULT_PRIMARY,
    BrandKit,
    FontKey,
    is_https_url,
)
from app.modules.personalization.page_signals import LogoCandidate, PageSignals

_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_RGB_RE = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*(?:[,/][^)]*)?\)"
)
_VAR_RE = re.compile(
    r"--([a-z0-9_-]{1,60})\s*:\s*(#[0-9a-fA-F]{3,8}|rgba?\([^)]{5,40}\))", re.IGNORECASE
)
_FONT_RE = re.compile(r"font-family\s*:\s*([^;}{]{1,200})", re.IGNORECASE)
_BODY_RULE_RE = re.compile(r"(?:^|})\s*(?:html|body)[^{}]*\{([^}]*)\}", re.IGNORECASE)
_PRIMARY_NAME_RE = re.compile(r"primary|brand|main", re.IGNORECASE)
_ACCENT_NAME_RE = re.compile(r"accent|secondary|highlight|cta|link", re.IGNORECASE)

_FONT_HOSTS_TO_SKIP = ("fonts.googleapis.com", "fonts.bunny.net", "use.typekit.net")
_RASTER_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


@dataclass
class BrandSuggestion:
    brand: BrandKit
    warnings: list[str] = field(default_factory=list)
    logo_candidates: list[str] = field(default_factory=list)
    stylesheet_urls: list[str] = field(default_factory=list)


def _to_hex(raw: str) -> str | None:
    raw = raw.strip()
    match = _RGB_RE.fullmatch(raw)
    if match:
        r, g, b = (min(255, int(x)) for x in match.groups())
        return f"#{r:02x}{g:02x}{b:02x}"
    if not raw.startswith("#"):
        return None
    digits = raw[1:]
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    elif len(digits) == 8:
        digits = digits[:6]  # drop alpha
    if len(digits) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", digits):
        return None
    return "#" + digits.lower()


def _is_neutral(color: str) -> bool:
    r, g, b = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    _, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    return saturation < 0.18 or lightness > 0.93 or lightness < 0.07


def _named_var_colors(css: str, name_re: re.Pattern[str]) -> list[str]:
    found: list[str] = []
    for name, value in _VAR_RE.findall(css):
        if name_re.search(name):
            color = _to_hex(value)
            if color and not _is_neutral(color) and color not in found:
                found.append(color)
    return found


def extract_colors(css: str, theme_color: str | None) -> tuple[str | None, str | None]:
    """(primary, accent) suggestions, each None when nothing trustworthy was
    found. Preference: named custom properties, then theme-color, then the most
    frequent non-neutral colour in the stylesheet."""
    primaries = _named_var_colors(css, _PRIMARY_NAME_RE)
    accents = [c for c in _named_var_colors(css, _ACCENT_NAME_RE) if c not in primaries]

    theme = _to_hex(theme_color) if theme_color else None
    frequent: list[str] = []
    counts: Counter[str] = Counter()
    for match in [*_HEX_RE.findall(css), *(m.group(0) for m in _RGB_RE.finditer(css))]:
        color = _to_hex(match)
        if color and not _is_neutral(color):
            counts[color] += 1
    frequent = [c for c, _ in counts.most_common(6)]

    ordered: list[str] = []
    for source in (
        primaries,
        [theme] if theme and not _is_neutral(theme) else [],
        frequent,
    ):
        for color in source:
            if color not in ordered:
                ordered.append(color)

    primary = ordered[0] if ordered else None
    accent = next((c for c in accents if c != primary), None)
    if accent is None:
        accent = next((c for c in ordered[1:] if c != primary), None)
    return primary, accent


def classify_font(css: str) -> FontKey | None:
    """Map the page's body font to one of our email-safe stacks."""
    families: list[str] = []
    for rule in _BODY_RULE_RE.findall(css):
        match = _FONT_RE.search(rule)
        if match:
            families.append(match.group(1))
    if not families:
        first = _FONT_RE.search(css)
        if first:
            families.append(first.group(1))
    if not families:
        return None
    stack = families[0].lower()
    if re.search(r"mono|courier|consolas|menlo", stack):
        return "mono"
    if "sans-serif" in stack or re.search(
        r"arial|helvetica|inter\b|roboto|segoe|system-ui", stack
    ):
        return "sans"
    if re.search(r"georgia|garamond|cambria|playfair|merriweather|lora|serif", stack):
        return "serif"
    if "times" in stack:
        return "times"
    if re.search(r"verdana|geneva|open sans|lato|nunito", stack):
        return "humanist"
    if "tahoma" in stack:
        return "modern"
    return "sans"


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def logo_candidates(signals: PageSignals) -> list[str]:
    """Logo URLs in preference order (https only, no duplicates). SVG and .ico
    candidates are kept so the caller can report *why* a logo was rejected."""
    ordered: list[str] = []

    def add(url: str | None) -> None:
        if url and is_https_url(url) and url not in ordered:
            ordered.append(url)

    add(signals.jsonld_logo)
    images: list[LogoCandidate] = signals.logo_images
    for candidate in (c for c in images if c.hinted and c.in_header):
        add(candidate.url)
    for candidate in (c for c in images if c.hinted and not c.in_header):
        add(candidate.url)
    for _, href, _ in sorted(
        (i for i in signals.icons if "apple-touch-icon" in i[0]),
        key=lambda i: -i[2],
    ):
        add(href)
    add(signals.og_image)
    return ordered[:6]


def is_raster_url_hint(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith(_RASTER_EXTENSIONS) or "." not in path.rsplit("/", 1)[-1]


def stylesheet_urls(signals: PageSignals, site_host: str, limit: int = 2) -> list[str]:
    """Same-site stylesheets first, then any other public one, never a font host."""
    usable = [
        u
        for u in signals.stylesheets
        if is_https_url(u) and _host(u) not in _FONT_HOSTS_TO_SKIP
    ]
    same = [
        u
        for u in usable
        if _host(u).removeprefix("www.") == site_host.removeprefix("www.")
    ]
    other = [u for u in usable if u not in same]
    return (same + other)[:limit]


def suggest_brand(
    signals: PageSignals,
    extra_css: str = "",
    *,
    logo_url: str | None = None,
    company_name: str = "",
    site_host: str = "",
) -> BrandSuggestion:
    """Combine what was read into a brand kit. Anything not found keeps its
    default and adds a warning, so the UI can ask the user to set it."""
    css = f"{signals.inline_css}\n{extra_css}"
    primary, accent = extract_colors(css, signals.theme_color)
    font = classify_font(css)
    warnings: list[str] = []
    if primary is None:
        warnings.append("colors_defaulted")
    if logo_url is None:
        warnings.append("logo_not_found")

    kit = BrandKit(
        logo_url=logo_url,
        logo_alt=(company_name or signals.site_name or "")[:120]
        .replace("{{", "")
        .replace("}}", ""),
        primary=primary or DEFAULT_PRIMARY,
        accent=accent or primary or DEFAULT_ACCENT,
        font_key=font or "sans",
    )
    return BrandSuggestion(
        brand=kit,
        warnings=warnings,
        logo_candidates=logo_candidates(signals),
        stylesheet_urls=stylesheet_urls(signals, site_host),
    )
