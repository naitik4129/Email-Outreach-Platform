"""Builds the clean Personalization Context handed to the model (ADR-0011/0012).

The model never receives the raw lead row. Only an allow-list of fields is used,
and email address, phone number and LinkedIn URLs are never included. Missing
data is left out, never invented ("Missing data is a review error, never an
invented personalization value", CAMPAIGN_ENGINE).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.modules.personalization.ports import Fact

# (variable name in frozen_variables, label) -- names only, never contact data.
_IDENTITY_FIELDS: tuple[tuple[str, str], ...] = (
    ("first_name", "first_name"),
    ("last_name", "last_name"),
    ("company", "company"),
    ("title", "title"),
)

# Fields that become facts the model may build the email on.
_FACT_FIELDS: tuple[tuple[str, str], ...] = (
    ("company", "Company"),
    ("title", "Job title"),
    ("department", "Department"),
    ("company_industry", "Company industry"),
    ("city", "City"),
    ("state", "State"),
    ("country", "Country"),
    ("experience_years", "Years of experience"),
    ("company_founded_year", "Company founded"),
)

_MAX_FIELD_CHARS = 200
_MAX_CUSTOM_FIELDS = 15
_MAX_WEBSITE_FACTS = 6
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# Custom-field names/values that look like contact or secret data are dropped,
# not sent: the allow-list is about what is safe to disclose, not what is present.
_SENSITIVE_KEY_RE = re.compile(
    r"(email|e-mail|phone|mobile|linkedin|password|passwd|secret|token|ssn|social|"
    r"salary|iban|card|account|dob|birth)",
    re.IGNORECASE,
)
_EMAIL_LIKE_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_URL_LIKE_RE = re.compile(r"https?://", re.IGNORECASE)


def _clean_value(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (list, dict)):
        return None
    text = _CONTROL_RE.sub(" ", str(value)).strip()
    if not text:
        return None
    return text[:_MAX_FIELD_CHARS]


@dataclass(frozen=True)
class PersonalizationContext:
    recipient: Mapping[str, str]
    facts: tuple[Fact, ...]
    thin: bool
    context_digest: str
    research_status: str

    def fact_by_id(self) -> dict[str, Fact]:
        return {fact.id: fact for fact in self.facts}


def build_context(
    frozen_variables: Mapping[str, Any],
    *,
    website_snippets: tuple[str, ...] = (),
    research_status: str = "NONE",
    min_facts: int,
) -> PersonalizationContext:
    recipient: dict[str, str] = {}
    for name, label in _IDENTITY_FIELDS:
        cleaned = _clean_value(frozen_variables.get(name))
        if cleaned:
            recipient[label] = cleaned

    facts: list[Fact] = []

    def add(source: str, text: str) -> None:
        facts.append(Fact(id=f"F{len(facts) + 1}", source=source, text=text))  # type: ignore[arg-type]

    for name, label in _FACT_FIELDS:
        cleaned = _clean_value(frozen_variables.get(name))
        if cleaned:
            add("LEAD", f"{label}: {cleaned}")

    custom = frozen_variables.get("custom_fields")
    if isinstance(custom, Mapping):
        used = 0
        for key in sorted(custom, key=str):
            if used >= _MAX_CUSTOM_FIELDS:
                break
            key_text = _CONTROL_RE.sub(" ", str(key)).strip()[:60]
            if not key_text or _SENSITIVE_KEY_RE.search(key_text):
                continue
            value = _clean_value(custom[key])
            if not value or _EMAIL_LIKE_RE.search(value) or _URL_LIKE_RE.search(value):
                continue
            add("LEAD", f"{key_text}: {value}")
            used += 1

    for snippet in website_snippets[:_MAX_WEBSITE_FACTS]:
        cleaned = _clean_value(snippet)
        if cleaned:
            add("WEBSITE", cleaned)

    digest_payload = json.dumps(
        {
            "recipient": recipient,
            "facts": [[f.id, f.source, f.text] for f in facts],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return PersonalizationContext(
        recipient=recipient,
        facts=tuple(facts),
        thin=len(facts) < min_facts,
        context_digest=hashlib.sha256(digest_payload.encode("utf-8")).hexdigest(),
        research_status=research_status,
    )
