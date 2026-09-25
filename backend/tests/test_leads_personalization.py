from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.core.errors import AppError
from app.modules.campaigns import worker_repository
from app.modules.campaigns.worker_repository import CampaignWorkerRepository
from app.modules.leads.fields import PROFILE_FIELD_NAMES
from app.modules.templates.rendering import (
    DEFAULT_SAMPLE_DATA,
    build_lead_render_context,
    render_template_content,
)
from app.modules.templates.variables import (
    STANDARD_VARIABLES,
    parse_and_validate_variables,
    validate_template_content,
)


def test_every_profile_field_is_a_valid_template_variable() -> None:
    body = " ".join(f"{{{{{name}}}}}" for name in PROFILE_FIELD_NAMES)

    detected, schema = parse_and_validate_variables(body, "body")

    assert detected == set(PROFILE_FIELD_NAMES)
    assert set(PROFILE_FIELD_NAMES) <= set(schema["standard"])
    assert set(PROFILE_FIELD_NAMES) <= STANDARD_VARIABLES


def test_existing_variables_and_aliases_are_unchanged() -> None:
    schema = validate_template_content(
        "Hi {{first_name|there}}", "{{company_name}} {{job_title}} {{custom.x}}"
    )

    assert schema == {
        "standard": ["company", "first_name", "title"],
        "custom": ["x"],
    }


def test_unknown_lookalike_variables_are_still_rejected() -> None:
    # "industry" was deliberately not added; only company_industry exists.
    for name in ("industry", "company_linkedin", "linkedin", "job_department"):
        with pytest.raises(AppError, match="Unknown variable"):
            parse_and_validate_variables(f"{{{{{name}}}}}", "body")


def test_render_context_carries_profile_values_and_nulls() -> None:
    context = build_lead_render_context(
        {
            "first_name": "Ada",
            "original_address": "ada@example.com",
            "city": "Berlin",
            "experience_years": 0,
            "company_founded_year": 1999,
        }
    )

    assert context["city"] == "Berlin"
    assert context["experience_years"] == 0
    assert context["company_founded_year"] == 1999
    assert context["phone"] is None
    assert set(PROFILE_FIELD_NAMES) <= set(context)


def test_render_stringifies_numbers_and_keeps_zero() -> None:
    context = build_lead_render_context(
        {"experience_years": 0, "company_founded_year": 1999}
    )

    subject, body = render_template_content(
        subject="{{company_founded_year}}",
        body_html="<p>{{experience_years}} yrs</p>",
        context_data=context,
    )

    assert subject == "1999"
    assert body == "<p>0 yrs</p>"


def test_missing_profile_value_uses_fallback_or_empty() -> None:
    context = build_lead_render_context({"city": None})

    subject, body = render_template_content(
        subject="{{city|your city}}",
        body_html="<p>[{{department}}]</p>",
        context_data=context,
    )

    assert subject == "your city"
    assert body == "<p>[]</p>"


def test_profile_values_are_html_escaped_in_body_but_not_subject() -> None:
    context = build_lead_render_context(
        {"company_website": 'https://example.com/?a=1&b="2"<x>'}
    )

    subject, body = render_template_content(
        subject="{{company_website}}",
        body_html="<a>{{company_website}}</a>",
        context_data=context,
    )

    assert subject == 'https://example.com/?a=1&b="2"<x>'
    assert "<x>" not in body
    assert "&amp;" in body and "&quot;" in body and "&lt;x&gt;" in body


def test_preview_sample_data_covers_every_profile_field() -> None:
    missing = [
        name
        for name in PROFILE_FIELD_NAMES
        if DEFAULT_SAMPLE_DATA.get(name) in (None, "")
    ]
    assert not missing


class _CapturingSession:
    def __init__(self) -> None:
        self.sql = ""

    def execute(self, statement: Any, params: Any = None) -> _CapturingSession:
        self.sql = str(statement)
        return self

    def mappings(self) -> _CapturingSession:
        return self

    def all(self) -> list[Any]:
        return []


def test_audience_capture_select_reads_every_profile_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A column missing here would silently freeze as empty in every campaign."""
    monkeypatch.setattr(worker_repository, "_safe_set_role", lambda *_: None)
    session = _CapturingSession()

    CampaignWorkerRepository(session).fetch_lead_capture_rows(  # type: ignore[arg-type]
        workspace_id=uuid.uuid4(), lead_ids=[]
    )

    for name in PROFILE_FIELD_NAMES:
        assert f"l.{name}" in session.sql, name
    for legacy in ("l.first_name", "l.last_name", "l.company", "l.title"):
        assert legacy in session.sql


# --- template preview "missing variables" --------------------------------


def _preview(subject: str, body: str, lead: dict[str, Any] | None = None) -> Any:
    from app.api.deps import WorkspaceContext
    from app.modules.templates.service import TemplateService
    from app.schemas.templates import TemplatePreviewIn

    class _Leads:
        def get_lead(self, **_: Any) -> dict[str, Any] | None:
            return lead

    service = TemplateService.__new__(TemplateService)
    service.leads_repo = _Leads()  # type: ignore[assignment]
    context = WorkspaceContext(
        workspace_id=uuid.uuid4(), user_id=uuid.uuid4(), role_code="MEMBER"
    )
    payload = TemplatePreviewIn(
        subject=subject, body_html=body, lead_id=uuid.uuid4() if lead else None
    )
    return service.preview_template(context, payload)


def _lead(**values: Any) -> dict[str, Any]:
    return {
        "first_name": "Ada",
        "original_address": "ada@example.com",
        "archived_at": None,
        "custom_fields": {},
        **values,
    }


def test_preview_does_not_report_resolvable_custom_and_alias_variables() -> None:
    result = _preview(
        "{{custom.segment}} for {{company_name}}",
        "<p>{{custom_fields.tier}} {{job_title}} {{city}}</p>",
        _lead(
            company="Acme",
            title="CTO",
            city="Berlin",
            custom_fields={"segment": "founder", "tier": "gold"},
        ),
    )

    assert result.missing_variables == []
    assert result.subject == "founder for Acme"
    assert "gold CTO Berlin" in result.body_html


def test_preview_still_reports_genuinely_missing_variables() -> None:
    result = _preview(
        "{{custom.segment}} {{department|your team}}",
        "<p>{{phone}} {{company_name}} {{custom_fields.tier}}</p>",
        _lead(custom_fields={"tier": "gold"}),
    )

    # department has a fallback, tier resolves; the rest are truly empty.
    assert result.missing_variables == ["company_name", "custom.segment", "phone"]
