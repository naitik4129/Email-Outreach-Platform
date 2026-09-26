from __future__ import annotations

import html
import re
from collections.abc import Mapping
from typing import Any

from app.modules.leads.fields import PROFILE_FIELD_NAMES
from app.modules.templates.variables import (
    _PLACEHOLDER_RE,
    VARIABLE_ALIASES,
)

DEFAULT_SAMPLE_DATA: dict[str, Any] = {
    "first_name": "Alex",
    "last_name": "Taylor",
    "company": "Acme Corp",
    "company_name": "Acme Corp",
    "title": "Head of Growth",
    "job_title": "Head of Growth",
    "email": "alex.taylor@acme.example.com",
    "phone": "+1 (415) 555-0132",
    "department": "Growth",
    "experience_years": 12,
    "linkedin_url": "https://www.linkedin.com/in/alex-taylor",
    "website": "https://alextaylor.example.com",
    "city": "San Francisco",
    "state": "California",
    "country": "United States",
    "company_website": "https://acme.example.com",
    "company_industry": "Software",
    "company_founded_year": 2010,
    "company_linkedin_url": "https://www.linkedin.com/company/acme",
    "custom_fields": {
        "industry": "Software",
        "city": "San Francisco",
    },
}


def build_lead_render_context(lead: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a lead database row or dict into a standardized render context."""
    custom_fields = lead.get("custom_fields") or {}
    if not isinstance(custom_fields, dict):
        custom_fields = {}

    return {
        "first_name": lead.get("first_name"),
        "last_name": lead.get("last_name"),
        "company": lead.get("company"),
        "company_name": lead.get("company"),
        "title": lead.get("title"),
        "job_title": lead.get("title"),
        "email": lead.get("original_address") or lead.get("canonical_address") or lead.get("email"),
        **{name: lead.get(name) for name in PROFILE_FIELD_NAMES},
        "custom_fields": custom_fields,
    }


def resolve_variable_value(
    name: str, fallback: str | None, context_data: Mapping[str, Any]
) -> str:
    """Resolve single variable from context, falling back gracefully."""
    raw_value: Any = None

    if name in VARIABLE_ALIASES:
        canonical = VARIABLE_ALIASES[name]
        raw_value = context_data.get(canonical) or context_data.get(name)
    elif name in context_data:
        raw_value = context_data.get(name)
    elif name.startswith("custom."):
        key = name[len("custom.") :]
        custom_dict = context_data.get("custom_fields")
        if isinstance(custom_dict, dict):
            raw_value = custom_dict.get(key)
    elif name.startswith("custom_fields."):
        key = name[len("custom_fields.") :]
        custom_dict = context_data.get("custom_fields")
        if isinstance(custom_dict, dict):
            raw_value = custom_dict.get(key)

    if raw_value is not None and str(raw_value).strip() != "":
        return str(raw_value)

    if fallback is not None:
        return fallback

    return ""


_PREHEADER_STYLE = (
    "display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;"
    "font-size:1px;line-height:1px;color:transparent"
)
_PREHEADER_TARGET_LENGTH = 110


def render_preheader(preheader: str | None, context_data: Mapping[str, Any]) -> str:
    """Resolve variables in a pre-header. Returns plain text (not HTML-escaped)."""
    if not preheader or not preheader.strip():
        return ""
    rendered, _ = render_template_content(
        subject=preheader.strip(), body_html="", context_data=context_data
    )
    return rendered.strip()


def inject_preheader(body_html: str, rendered_preheader: str) -> str:
    """Prepend a hidden preview-text element to an already-rendered body.

    Mail clients show the first visible text after the subject as inbox preview
    text; a hidden leading element lets the sender control it. The padding of
    zero-width/nbsp entities stops clients pulling body text in after a short
    pre-header. Deterministic, so the content digest stays stable. An empty
    pre-header returns the body unchanged (legacy rows hash identically).
    """
    if not rendered_preheader:
        return body_html
    text = html.escape(rendered_preheader, quote=True)
    padding = "&zwnj;&nbsp;" * max(
        0, (_PREHEADER_TARGET_LENGTH - len(rendered_preheader)) // 2
    )
    return f'<div style="{_PREHEADER_STYLE}">{text}{padding}</div>{body_html}'


def render_template_content(
    *, subject: str, body_html: str, context_data: Mapping[str, Any]
) -> tuple[str, str]:
    """Render subject and body_html with provided context data deterministically.

    In subject, placeholders are replaced with raw string values.
    In body_html, placeholders are replaced with HTML-escaped string values
    to ensure lead variables cannot inject arbitrary HTML tags into the email body.
    """

    def _replace_subject(m: re.Match[str]) -> str:
        var_name = m.group(1).strip()
        fallback = m.group(2)
        return resolve_variable_value(var_name, fallback, context_data)

    def _replace_body(m: re.Match[str]) -> str:
        var_name = m.group(1).strip()
        fallback = m.group(2)
        resolved = resolve_variable_value(var_name, fallback, context_data)
        return html.escape(resolved, quote=True)

    rendered_subject = _PLACEHOLDER_RE.sub(_replace_subject, subject)
    rendered_body = _PLACEHOLDER_RE.sub(_replace_body, body_html)

    return rendered_subject, rendered_body
