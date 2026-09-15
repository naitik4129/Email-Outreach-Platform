from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from app.modules.templates.rendering import render_template_content


class RenderedMessageContent(NamedTuple):
    subject: str
    body_html: str
    content_digest: str


def render_step_content(
    *,
    subject: str,
    body_html: str,
    frozen_variables: Mapping[str, Any],
    renderer_version: int,
) -> RenderedMessageContent:
    """Render a frozen sequence step's content against an enrollment's frozen
    variables.

    Reuses the same deterministic renderer templates already use
    (app.modules.templates.rendering.render_template_content) -- variable
    substitution/escaping logic is not reimplemented here.
    """
    rendered_subject, rendered_body = render_template_content(
        subject=subject, body_html=body_html, context_data=frozen_variables
    )
    digest_input = f"{rendered_subject}\x00{rendered_body}\x00{renderer_version}"
    content_digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    return RenderedMessageContent(rendered_subject, rendered_body, content_digest)


def compute_sequence_content_digest(steps: Sequence[Mapping[str, Any]]) -> str:
    """A stable sha256 over a sequence's step content, satisfying
    campaign_sequences_frozen_check's ^[0-9a-f]{64}$ shape.

    Computed once at activation freeze time. Independent of the input list's
    ordering (sorted by position here) but sensitive to any content change.
    """
    canonical = [
        {
            "position": step["position"],
            "kind": step["kind"],
            "email_subject": step.get("email_subject"),
            "email_body_html": step.get("email_body_html"),
            "wait_duration_minutes": step.get("wait_duration_minutes"),
        }
        for step in sorted(steps, key=lambda s: s["position"])
    ]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
