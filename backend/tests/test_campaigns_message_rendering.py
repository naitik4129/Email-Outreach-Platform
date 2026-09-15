from __future__ import annotations

import re

from app.modules.campaigns.message_rendering import (
    compute_sequence_content_digest,
    render_step_content,
)

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def test_render_step_content_substitutes_variables() -> None:
    rendered = render_step_content(
        subject="Hi {{first_name}}",
        body_html="<p>Hi {{first_name}} from {{company}}</p>",
        frozen_variables={"first_name": "Ada", "company": "Acme"},
        renderer_version=1,
    )
    assert rendered.subject == "Hi Ada"
    assert rendered.body_html == "<p>Hi Ada from Acme</p>"


def test_render_step_content_html_escapes_body_not_subject() -> None:
    rendered = render_step_content(
        subject="Hi {{first_name}}",
        body_html="<p>Hi {{first_name}}</p>",
        frozen_variables={"first_name": "<script>alert(1)</script>"},
        renderer_version=1,
    )
    assert "<script>" not in rendered.body_html
    assert "&lt;script&gt;" in rendered.body_html
    # Subject substitution is raw (no HTML context to escape into).
    assert rendered.subject == "Hi <script>alert(1)</script>"


def test_content_digest_matches_check_constraint_shape() -> None:
    rendered = render_step_content(
        subject="Hi", body_html="<p>Hi</p>", frozen_variables={}, renderer_version=1
    )
    assert _DIGEST_RE.match(rendered.content_digest)


def test_content_digest_stable_for_identical_inputs() -> None:
    kwargs = {
        "subject": "Hi {{first_name}}",
        "body_html": "<p>Hi</p>",
        "frozen_variables": {"first_name": "Ada"},
        "renderer_version": 1,
    }
    first = render_step_content(**kwargs)
    second = render_step_content(**kwargs)
    assert first.content_digest == second.content_digest


def test_content_digest_changes_with_subject() -> None:
    base = render_step_content(
        subject="A", body_html="<p>Hi</p>", frozen_variables={}, renderer_version=1
    )
    changed = render_step_content(
        subject="B", body_html="<p>Hi</p>", frozen_variables={}, renderer_version=1
    )
    assert base.content_digest != changed.content_digest


def test_content_digest_changes_with_renderer_version() -> None:
    base = render_step_content(
        subject="A", body_html="<p>Hi</p>", frozen_variables={}, renderer_version=1
    )
    changed = render_step_content(
        subject="A", body_html="<p>Hi</p>", frozen_variables={}, renderer_version=2
    )
    assert base.content_digest != changed.content_digest


def _step(position: int, kind: str, **overrides: object) -> dict:
    base = {
        "position": position,
        "kind": kind,
        "email_subject": None,
        "email_body_html": None,
        "wait_duration_minutes": None,
    }
    base.update(overrides)
    return base


def test_sequence_digest_matches_check_constraint_shape() -> None:
    steps = [
        _step(1, "EMAIL", email_subject="Hi", email_body_html="<p>Hi</p>"),
        _step(2, "WAIT", wait_duration_minutes=1440),
        _step(3, "EMAIL", email_subject="Follow up", email_body_html="<p>Bump</p>"),
    ]
    digest = compute_sequence_content_digest(steps)
    assert _DIGEST_RE.match(digest)


def test_sequence_digest_independent_of_input_ordering() -> None:
    steps = [
        _step(1, "EMAIL", email_subject="Hi", email_body_html="<p>Hi</p>"),
        _step(2, "WAIT", wait_duration_minutes=1440),
    ]
    forward = compute_sequence_content_digest(steps)
    backward = compute_sequence_content_digest(list(reversed(steps)))
    assert forward == backward


def test_sequence_digest_sensitive_to_content_change() -> None:
    steps = [_step(1, "EMAIL", email_subject="Hi", email_body_html="<p>Hi</p>")]
    original = compute_sequence_content_digest(steps)
    changed_steps = [
        _step(1, "EMAIL", email_subject="Hello", email_body_html="<p>Hi</p>")
    ]
    changed = compute_sequence_content_digest(changed_steps)
    assert original != changed
