"""The campaign objective a user defines once for a hyper-personalized campaign,
and the digest that binds a sample approval to it (ADR-0011)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.modules.personalization.version import PROMPT_VERSION

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

Phrase = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


def _clean(value: str) -> str:
    if _CONTROL_RE.search(value):
        raise ValueError("must not contain control characters")
    return value.strip()


class PersonalizationConfig(BaseModel):
    """What the user is trying to achieve. Deliberately small (ADR-0011)."""

    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=1000)
    offer: str = Field(min_length=1, max_length=1000)
    cta: str = Field(min_length=1, max_length=500)
    target: str = Field(default="", max_length=500)
    problem_solved: str = Field(default="", max_length=1000)
    tone: str = Field(default="", max_length=200)
    must_mention: list[Phrase] = Field(default_factory=list, max_length=10)
    never_say: list[Phrase] = Field(default_factory=list, max_length=10)

    @field_validator("objective", "offer", "cta", "target", "problem_solved", "tone")
    @classmethod
    def _no_control_chars(cls, value: str) -> str:
        return _clean(value)

    @field_validator("must_mention", "never_say")
    @classmethod
    def _phrases(cls, value: list[str]) -> list[str]:
        cleaned = [_clean(item) for item in value]
        if len({item.lower() for item in cleaned}) != len(cleaned):
            raise ValueError("must not contain duplicates")
        return cleaned

    def canonical(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def parse_config(raw: Mapping[str, Any] | None) -> PersonalizationConfig | None:
    """None when there is no objective yet; raises ValidationError when the
    stored value is not a valid objective."""
    if not raw:
        return None
    return PersonalizationConfig.model_validate(dict(raw))


def compute_approval_digest(
    *,
    config: Mapping[str, Any] | None,
    steps: Sequence[Mapping[Any, Any]],
    model: str,
) -> str:
    """Server-computed identity of everything a sample approval vouches for:
    the objective, every email step's reference content, the prompt version and
    the model. Any edit changes it, so an approval is stale without any write."""
    email_steps = [
        {
            "position": step["position"],
            "subject": step.get("email_subject"),
            "body": step.get("email_body_html"),
            "preheader": step.get("email_preheader") or None,
        }
        for step in sorted(steps, key=lambda s: s["position"])
        if step["kind"] == "EMAIL"
    ]
    payload = json.dumps(
        {
            "config": dict(config) if config else None,
            "steps": email_steps,
            "prompt_version": PROMPT_VERSION,
            "model": model,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
