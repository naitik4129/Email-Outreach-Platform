"""The branded HTML email layout (ADR-0017).

One platform-authored, table-based layout used by both reference drafting (the
result is written into a step) and per-lead generation (the validated paragraph
fragment is wrapped before the preheader and digest). The model never emits
markup; this module is the only place the layout is built.

Two properties are load-bearing and tested:

* every token is validated *again* here, so a hand-edited row cannot inject
  markup into an email that skips the default sanitizer profile;
* the output is a fixed point of ``sanitize_email_html(profile="branded")``, so
  saving it into a step through SequenceService does not alter it.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.modules.personalization.brand_kit import (
    FONT_STACKS,
    MAX_CTA_LABEL_CHARS,
    BrandKit,
    clean_display_text,
    clean_url,
    readable_on,
)
from app.modules.personalization.config_schema import PersonalizationConfig

_CONTAINER_WIDTH = 600
_OUTER_BACKGROUND = "#f4f4f5"
_LINK_TAG_RE = re.compile(r"<a (?![^>]*\bstyle=)")
_DEFAULT_CTA_LABEL = "Learn more"


def _esc_text(value: str) -> str:
    """Text-node escaping exactly as the sanitizer re-emits it (only & < >)."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(value: str) -> str:
    """Attribute escaping exactly as the sanitizer re-emits it."""
    return _esc_text(value).replace('"', "&quot;")


def _style(**declarations: str) -> str:
    """A style attribute value in exactly the form the sanitizer re-emits
    ("prop: value; prop: value"), so the layout is a sanitizer fixed point."""
    return "; ".join(
        f"{name.replace('_', '-')}: {value}" for name, value in declarations.items()
    )


def _style_links(fragment_html: str, accent: str) -> str:
    """Colour the fragment's links with the accent colour. The fragment comes from
    assemble_body_html + sanitize_email_html, whose only anchors are
    ``<a href="...">``."""
    return _LINK_TAG_RE.sub(f'<a style="{_style(color=accent)}" ', fragment_html)


def default_cta_label(cta_text: str) -> str:
    """A short button label from the objective's CTA sentence."""
    text = " ".join(cta_text.split()).strip(" .!")
    if not text:
        return _DEFAULT_CTA_LABEL
    if len(text) <= MAX_CTA_LABEL_CHARS:
        return text
    cut = text[:MAX_CTA_LABEL_CHARS].rsplit(" ", 1)[0].strip(" ,;:-")
    return cut or _DEFAULT_CTA_LABEL


def _tag(name: str, attrs: list[tuple[str, str]], inner: str = "") -> str:
    """One element. Attribute values are escaped here, once, exactly as the
    sanitizer re-emits them; `img` is void (no closing tag), as the sanitizer
    writes it."""
    rendered = "".join(f' {key}="{_esc_attr(value)}"' for key, value in attrs)
    if name == "img":
        return f"<img{rendered}>"
    return f"<{name}{rendered}>{inner}</{name}>"


def render_branded_email(
    brand: BrandKit,
    fragment_html: str,
    *,
    company_name: str = "",
    site_url: str | None = None,
    cta_label: str | None = None,
) -> str:
    """Wrap an already-sanitized body fragment in the branded layout.

    Raises ValueError if any token is invalid; callers treat that as a
    configuration error, never as something to send anyway."""
    brand = BrandKit.model_validate(brand.model_dump())
    company_name = clean_display_text(company_name)
    site = clean_url(site_url) if site_url else None
    # An explicit label chosen by the user wins over the derived default.
    label = brand.cta_label or (clean_display_text(cta_label) if cta_label else "")

    stack = FONT_STACKS[brand.font_key]
    text_style = _style(
        padding="32px",
        font_family=stack,
        font_size="16px",
        line_height="1.6",
        color=brand.text,
        text_align="left",
    )
    footer_style = _style(
        padding="16px 32px",
        font_family=stack,
        font_size="12px",
        line_height="1.5",
        color=brand.text,
        text_align="left",
    )
    body_cell = _tag(
        "td",
        [("style", text_style)],
        _style_links(fragment_html, brand.accent)
        + _cta_button(brand, label or _DEFAULT_CTA_LABEL, stack),
    )
    footer = _footer_text(company_name, site, brand)
    cells = [
        _header_cell(brand, company_name, stack),
        body_cell,
        _tag("td", [("style", footer_style)], footer) if footer else "",
    ]
    rows = "".join(_tag("tr", [], cell) for cell in cells if cell)

    container_style = _style(
        width="100%",
        max_width=f"{_CONTAINER_WIDTH}px",
        background_color=brand.background,
    )
    inner = _tag(
        "table",
        _table_attrs(brand.background, str(_CONTAINER_WIDTH), container_style),
        rows,
    )
    outer_cell = _tag(
        "td", [("align", "center"), ("style", _style(padding="24px 12px"))], inner
    )
    outer_style = _style(background_color=_OUTER_BACKGROUND)
    return _tag(
        "table",
        _table_attrs(_OUTER_BACKGROUND, "100%", outer_style),
        _tag("tr", [], outer_cell),
    )


def _table_attrs(background: str, width: str, style: str) -> list[tuple[str, str]]:
    return [
        ("role", "presentation"),
        ("width", width),
        ("cellpadding", "0"),
        ("cellspacing", "0"),
        ("border", "0"),
        ("bgcolor", background),
        ("style", style),
    ]


def _header_cell(brand: BrandKit, company_name: str, stack: str) -> str:
    if brand.logo_url:
        alt = brand.logo_alt or company_name
        content = _tag(
            "img",
            [
                ("src", brand.logo_url),
                ("alt", alt),
                ("height", "40"),
                # Alt text is what a mail client shows when it blocks remote
                # images (the default), so make it readable on the header colour.
                (
                    "style",
                    _style(
                        display="block",
                        border="0",
                        font_family=stack,
                        font_size="20px",
                        font_weight="bold",
                        color=readable_on(brand.primary),
                    ),
                ),
            ],
        )
    elif company_name:
        name_style = _style(
            font_family=stack,
            font_size="20px",
            font_weight="bold",
            color=readable_on(brand.primary),
        )
        content = _tag("span", [("style", name_style)], _esc_text(company_name))
    else:
        return ""
    cell_style = _style(background_color=brand.primary, padding="20px 32px")
    return _tag("td", [("bgcolor", brand.primary), ("style", cell_style)], content)


def _cta_button(brand: BrandKit, label: str, stack: str) -> str:
    """A bulletproof button: a coloured cell around an inline-block link, so it
    still renders as a button where padding on the link itself is ignored."""
    if not brand.cta_url:
        return ""
    link_style = _style(
        display="inline-block",
        padding="12px 24px",
        font_family=stack,
        font_size="16px",
        font_weight="bold",
        color=readable_on(brand.accent),
        text_decoration="none",
    )
    link = _tag("a", [("href", brand.cta_url), ("style", link_style)], _esc_text(label))
    cell_style = _style(background_color=brand.accent, border_radius="6px")
    cell = _tag("td", [("bgcolor", brand.accent), ("style", cell_style)], link)
    table_attrs = [
        ("role", "presentation"),
        ("cellpadding", "0"),
        ("cellspacing", "0"),
        ("border", "0"),
        ("style", _style(margin="24px 0 0 0")),
    ]
    return _tag("table", table_attrs, _tag("tr", [], cell))


def _footer_text(company_name: str, site: str | None, brand: BrandKit) -> str:
    parts: list[str] = []
    if company_name:
        parts.append(_esc_text(company_name))
    if site:
        host = urlsplit(site).netloc or site
        link_style = _style(color=brand.accent)
        parts.append(
            _tag("a", [("href", site), ("style", link_style)], _esc_text(host))
        )
    return " · ".join(parts)


def render_for_config(config: PersonalizationConfig, fragment_html: str) -> str:
    """The body to store or send for a saved configuration: the fragment as is for
    a Text campaign, wrapped in the brand layout for an HTML one. The single
    entry point for both reference drafting and per-lead generation, so the two
    can never disagree about what a branded email looks like."""
    if config.email_format != "HTML":
        return fragment_html
    company = config.company
    return render_branded_email(
        config.brand or BrandKit(),
        fragment_html,
        company_name=company.company_name if company else "",
        site_url=company.url if company else None,
        cta_label=default_cta_label(config.cta),
    )


_SAMPLE_FRAGMENT = (
    "<p>Hi there,</p>"
    "<p>This is how your emails will look. Your logo, colours and font are applied "
    "to every message, and each recipient gets a version written for them.</p>"
    "<p>Best regards</p>"
)


def render_layout_preview(
    brand: BrandKit,
    *,
    company_name: str = "",
    site_url: str | None = None,
    cta_label: str | None = None,
) -> str:
    """The layout around fixed sample text, for the Brand card's live preview.
    Uses the real renderer so the preview cannot drift from what is sent."""
    return render_branded_email(
        brand,
        _SAMPLE_FRAGMENT,
        company_name=company_name,
        site_url=site_url,
        cta_label=cta_label,
    )
