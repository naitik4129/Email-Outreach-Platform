from __future__ import annotations

import html
import re
from collections.abc import Mapping
from typing import Any

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
