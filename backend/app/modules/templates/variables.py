from __future__ import annotations

import re
from typing import Any

from app.core.errors import AppError
from app.modules.leads.fields import PROFILE_FIELD_NAMES

# Canonical set of supported lead fields in email outreach templates
STANDARD_VARIABLES: frozenset[str] = frozenset({
    "first_name",
    "last_name",
    "company",
    "company_name",
    "title",
    "job_title",
    "email",
    *PROFILE_FIELD_NAMES,
})

# Canonical alias mapping
VARIABLE_ALIASES: dict[str, str] = {
    "company_name": "company",
    "job_title": "title",
}

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)(?:\s*\|\s*([^}]*?))?\s*\}\}")


def is_valid_variable_name(name: str) -> bool:
    """Check if variable name is a known standard field or valid custom field."""
    if name in STANDARD_VARIABLES:
        return True
    if name.startswith("custom."):
        field_part = name[len("custom.") :]
        return bool(_IDENTIFIER_RE.match(field_part))
    if name.startswith("custom_fields."):
        field_part = name[len("custom_fields.") :]
        return bool(_IDENTIFIER_RE.match(field_part))
    return False


def parse_and_validate_variables(
    text: str, field_name: str
) -> tuple[set[str], dict[str, list[str]]]:
    """Parse all {{ variable | fallback }} placeholders from text and validate them.

    Raises AppError(422) if:
    - Placeholders are malformed (unclosed '{{', rogue '}}', empty '{{}}')
    - Variables are unsupported or attempt arbitrary property traversal (e.g. __class__)
    """
    if not text:
        return set(), {"standard": [], "custom": []}

    # Check for obvious malformed brace patterns
    # 1. Empty braces {{}} or {{   }}
    if re.search(r"\{\{\s*\}\}", text):
        raise AppError(
            "validation_error",
            f"Empty variable placeholder in {field_name}",
            status_code=422,
        )

    # 2. Count matched occurrences vs raw {{ and }}
    matches = list(_PLACEHOLDER_RE.finditer(text))
    open_count = text.count("{{")
    close_count = text.count("}}")

    if open_count != len(matches) or close_count != len(matches):
        raise AppError(
            "validation_error",
            f"Malformed variable placeholder in {field_name}",
            status_code=422,
        )

    detected_vars: set[str] = set()
    standard_vars: set[str] = set()
    custom_vars: set[str] = set()

    for m in matches:
        raw_name = m.group(1).strip()
        if not is_valid_variable_name(raw_name):
            raise AppError(
                "validation_error",
                f"Unknown variable '{raw_name}' in {field_name}",
                status_code=422,
            )

        detected_vars.add(raw_name)
        if raw_name in STANDARD_VARIABLES:
            canonical = VARIABLE_ALIASES.get(raw_name, raw_name)
            standard_vars.add(canonical)
        elif raw_name.startswith("custom."):
            custom_vars.add(raw_name[len("custom.") :])
        elif raw_name.startswith("custom_fields."):
            custom_vars.add(raw_name[len("custom_fields.") :])

    schema = {
        "standard": sorted(standard_vars),
        "custom": sorted(custom_vars),
    }
    return detected_vars, schema


def validate_template_content(
    subject: str, body_html: str
) -> dict[str, Any]:
    """Validate subject and body_html bounds and placeholder syntax.

    Returns combined variable_schema suitable for template_versions.variable_schema.
    """
    clean_subject = subject.strip()
    if not clean_subject:
        raise AppError("validation_error", "Subject is required", status_code=422)
    if len(clean_subject) > 500:
        raise AppError(
            "validation_error",
            "Subject must be 500 characters or fewer",
            status_code=422,
        )

    if len(body_html) > 200_000:
        raise AppError(
            "validation_error",
            "Body HTML must be 200,000 characters or fewer",
            status_code=422,
        )

    _, subject_schema = parse_and_validate_variables(clean_subject, "subject")
    _, body_schema = parse_and_validate_variables(body_html, "body")

    combined_standard = sorted(
        set(subject_schema["standard"]) | set(body_schema["standard"])
    )
    combined_custom = sorted(
        set(subject_schema["custom"]) | set(body_schema["custom"])
    )

    return {
        "standard": combined_standard,
        "custom": combined_custom,
    }
