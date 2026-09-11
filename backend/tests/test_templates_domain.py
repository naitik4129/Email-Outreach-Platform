from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.modules.templates.rendering import (
    DEFAULT_SAMPLE_DATA,
    render_template_content,
    resolve_variable_value,
)
from app.modules.templates.sanitizer import sanitize_html_preview
from app.modules.templates.variables import (
    parse_and_validate_variables,
    validate_template_content,
)


class TestVariableParsingAndValidation:
    def test_standard_variables_detected(self) -> None:
        text = "Hello {{first_name}}, welcome to {{company}}! Reach me at {{email}}."
        detected, schema = parse_and_validate_variables(text, "body")
        assert "first_name" in detected
        assert "company" in detected
        assert "email" in detected
        assert schema["standard"] == ["company", "email", "first_name"]

    def test_aliases_normalized_in_schema(self) -> None:
        text = "Hi {{first_name}}, is {{company_name}} hiring for {{job_title}}?"
        detected, schema = parse_and_validate_variables(text, "body")
        assert "company_name" in detected
        assert "job_title" in detected
        # Canonical schema uses 'company' and 'title'
        assert "company" in schema["standard"]
        assert "title" in schema["standard"]

    def test_custom_variables_detected(self) -> None:
        text = "We saw your {{custom.industry}} company located in {{custom_fields.city}}."
        detected, schema = parse_and_validate_variables(text, "body")
        assert "custom.industry" in detected
        assert "custom_fields.city" in detected
        assert "industry" in schema["custom"]
        assert "city" in schema["custom"]

    def test_fallback_syntax_parsed(self) -> None:
        text = "Hi {{first_name|there}}, your role as {{title | Leader}} is impressive."
        detected, schema = parse_and_validate_variables(text, "body")
        assert "first_name" in detected
        assert "title" in detected
        assert "first_name" in schema["standard"]
        assert "title" in schema["standard"]

    def test_empty_placeholder_rejected(self) -> None:
        with pytest.raises(AppError) as exc_info:
            parse_and_validate_variables("Hello {{}} there", "subject")
        assert exc_info.value.status_code == 422
        assert "Empty variable placeholder" in exc_info.value.message

    def test_unclosed_placeholder_rejected(self) -> None:
        with pytest.raises(AppError) as exc_info:
            parse_and_validate_variables("Hello {{first_name there", "subject")
        assert exc_info.value.status_code == 422
        assert "Malformed variable placeholder" in exc_info.value.message

    def test_rogue_closing_braces_rejected(self) -> None:
        with pytest.raises(AppError) as exc_info:
            parse_and_validate_variables("Hello first_name}} there", "subject")
        assert exc_info.value.status_code == 422
        assert "Malformed variable placeholder" in exc_info.value.message

    def test_unknown_variable_rejected(self) -> None:
        with pytest.raises(AppError) as exc_info:
            parse_and_validate_variables("Hello {{unsupported_variable}}", "subject")
        assert exc_info.value.status_code == 422
        assert "Unknown variable 'unsupported_variable'" in exc_info.value.message

    def test_arbitrary_object_traversal_rejected(self) -> None:
        attacks = [
            "{{user.__class__}}",
            "{{__init__}}",
            "{{constructor}}",
            "{{self.session}}",
            "{{custom.123bad}}",
            "{{custom.field-with-dash}}",
        ]
        for attack in attacks:
            with pytest.raises(AppError) as exc_info:
                parse_and_validate_variables(f"Testing {attack}", "body")
            assert exc_info.value.status_code == 422

    def test_validate_template_content_bounds(self) -> None:
        with pytest.raises(AppError) as exc:
            validate_template_content("", "<p>Body</p>")
        assert exc.value.status_code == 422

        with pytest.raises(AppError) as exc:
            validate_template_content("A" * 501, "<p>Body</p>")
        assert exc.value.status_code == 422

        with pytest.raises(AppError) as exc:
            validate_template_content("Subject", "B" * 200_001)
        assert exc.value.status_code == 422


class TestTemplateRendering:
    def test_deterministic_rendering_with_context(self) -> None:
        context = {
            "first_name": "Jordan",
            "last_name": "Lee",
            "company": "NextGen AI",
            "title": "CTO",
            "email": "jordan@nextgen.example.com",
            "custom_fields": {"priority": "High"},
        }
        subj, body = render_template_content(
            subject="Quick note for {{first_name}} at {{company}}",
            body_html="<p>Hi {{first_name}}, noticed your work as {{title}} at {{company}}.</p>",
            context_data=context,
        )
        assert subj == "Quick note for Jordan at NextGen AI"
        assert body == "<p>Hi Jordan, noticed your work as CTO at NextGen AI.</p>"

    def test_fallback_when_variable_missing(self) -> None:
        context = {"first_name": None, "company": ""}
        subj, body = render_template_content(
            subject="Hello {{first_name|there}}",
            body_html="<p>Greetings to team at {{company | your company}}.</p>",
            context_data=context,
        )
        assert subj == "Hello there"
        assert body == "<p>Greetings to team at your company.</p>"

    def test_empty_string_when_variable_missing_and_no_fallback(self) -> None:
        context = {"first_name": None}
        subj, body = render_template_content(
            subject="Hello {{first_name}}",
            body_html="<p>Hello {{first_name}}!</p>",
            context_data=context,
        )
        assert subj == "Hello "
        assert body == "<p>Hello !</p>"

    def test_html_escaping_in_rendered_body(self) -> None:
        context = {
            "first_name": "<script>alert('pwn')</script>",
            "company": "Tom & Jerry <Corp>",
        }
        subj, body = render_template_content(
            subject="Hi {{first_name}}",
            body_html="<p>Hi {{first_name}}, welcome to {{company}}</p>",
            context_data=context,
        )
        # Subject keeps raw characters
        assert subj == "Hi <script>alert('pwn')</script>"
        # Body HTML-escapes injected variables
        assert "&lt;script&gt;alert(&#x27;pwn&#x27;)&lt;/script&gt;" in body
        assert "<script>" not in body
        assert "Tom &amp; Jerry &lt;Corp&gt;" in body

    def test_custom_fields_resolved(self) -> None:
        context = {
            "custom_fields": {"tech_stack": "Python/FastAPI"},
        }
        _, body = render_template_content(
            subject="Tech",
            body_html="We see you use {{custom.tech_stack}}.",
            context_data=context,
        )
        assert "Python/FastAPI" in body


class TestHTMLSanitizer:
    def test_strips_script_tags_and_content(self) -> None:
        dirty = "<p>Hello</p><script>alert('xss');</script><p>World</p>"
        clean = sanitize_html_preview(dirty)
        assert "<script>" not in clean
        assert "alert('xss')" not in clean
        assert "<p>Hello</p>" in clean
        assert "<p>World</p>" in clean

    def test_strips_event_handlers(self) -> None:
        dirty = '<img src="https://example.com/pic.png" onerror="alert(1)" onload="evil()">'
        clean = sanitize_html_preview(dirty)
        assert 'src="https://example.com/pic.png"' in clean
        assert "onerror" not in clean
        assert "onload" not in clean

    def test_strips_javascript_urls(self) -> None:
        dirty = '<a href="javascript:alert(1)">Click here</a>'
        clean = sanitize_html_preview(dirty)
        assert "javascript:" not in clean
        assert "Click here" in clean

    def test_strips_iframes_and_objects(self) -> None:
        dirty = '<iframe src="https://evil.example.com"></iframe><object data="test"></object>'
        clean = sanitize_html_preview(dirty)
        assert "<iframe" not in clean
        assert "<object" not in clean

    def test_preserves_legitimate_formatting_and_unicode(self) -> None:
        legit = (
            "<h1>Welcome 🎉</h1>"
            "<p>Dear <b>Friend</b>, visit our <a href=\"https://example.com\">website</a>.</p>"
            "<ul><li>Item 1</li><li>Item 2</li></ul>"
            "<p>French: café, résumé, naïve. Japanese: こんにちは.</p>"
        )
        clean = sanitize_html_preview(legit)
        assert "<h1>Welcome 🎉</h1>" in clean
        assert "<b>Friend</b>" in clean
        assert '<a href="https://example.com">website</a>' in clean
        assert "café, résumé, naïve" in clean
        assert "こんにちは" in clean
