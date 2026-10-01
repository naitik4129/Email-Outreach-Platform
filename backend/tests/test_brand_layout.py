"""Brand tokens, the branded layout, the scoped sanitizer profile and the
backward-compatible config schema (ADR-0016 / ADR-0017)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.modules.personalization.brand_kit import (
    BrandKit,
    brand_warnings,
    contrast_ratio,
    readable_on,
)
from app.modules.personalization.brand_layout import (
    default_cta_label,
    render_branded_email,
    render_layout_preview,
)
from app.modules.personalization.config_schema import (
    MAX_CONFIG_BYTES,
    CompanyProfile,
    PersonalizationConfig,
    compute_approval_digest,
    config_size_bytes,
    parse_config,
)
from app.modules.templates.sanitizer import sanitize_email_html

CONFIG = {
    "objective": "Book demos",
    "offer": "Verified B2B contact data",
    "cta": "Book a 15 minute demo",
    "target": "SaaS founders",
    "problem_solved": "Slow prospecting",
    "tone": "friendly",
    "must_mention": ["free trial"],
    "never_say": ["guarantee"],
}

FULL_BRAND = {
    "logo_url": "https://acme.example/logo.png",
    "logo_alt": "Acme",
    "primary": "#123456",
    "accent": "#ff5500",
    "text": "#222222",
    "background": "#ffffff",
    "font_key": "serif",
    "cta_url": "https://acme.example/demo?ref=email&x=1",
    "cta_label": "Book a demo",
}


class TestBrandKit:
    def test_defaults_are_valid(self) -> None:
        brand = BrandKit()
        assert brand.primary.startswith("#") and brand.font_key == "sans"

    @pytest.mark.parametrize(
        "field,value",
        [
            ("primary", "red"),
            ("primary", "#12345"),
            ("accent", "#12345g"),
            ("text", "url(x)"),
            ("background", "#ffffff;background:url(x)"),
            ("logo_url", "http://acme.example/logo.png"),
            ("logo_url", "javascript:alert(1)"),
            ("logo_url", "https://acme.example/a b.png"),
            ("logo_url", 'https://acme.example/a"onerror="x'),
            ("logo_url", "https://acme.example/<b>"),
            ("logo_url", "https://acme.example/{{first_name}}"),
            ("logo_url", "https://nodot/x"),
            ("cta_url", "https://acme.example/\\evil"),
            ("logo_alt", "hello {{first_name}}"),
            ("cta_label", "a\x00b"),
            ("cta_label", "x" * 41),
            ("font_key", "comic"),
        ],
    )
    def test_rejects_unsafe_tokens(self, field: str, value: str) -> None:
        with pytest.raises(ValidationError):
            BrandKit.model_validate({field: value})

    def test_colours_are_lowercased_and_blank_urls_become_none(self) -> None:
        brand = BrandKit.model_validate(
            {"primary": "#ABCDEF", "logo_url": "  ", "cta_url": ""}
        )
        assert brand.primary == "#abcdef"
        assert brand.logo_url is None and brand.cta_url is None

    def test_unknown_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BrandKit.model_validate({"css": "x"})

    def test_contrast_helpers(self) -> None:
        assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
        assert readable_on("#000000") == "#ffffff"
        assert readable_on("#ffffff") == "#000000"
        assert "text_contrast_low" in brand_warnings(
            BrandKit(text="#eeeeee", background="#ffffff")
        )
        assert brand_warnings(BrandKit()) == []


class TestLayout:
    def test_layout_is_a_fixed_point_of_the_branded_sanitizer(self) -> None:
        brand = BrandKit.model_validate(FULL_BRAND)
        fragment = sanitize_email_html(
            '<p>Hi {{first_name|there}}, see <a href="https://acme.example/x?a=1&amp;b=2">this</a></p>'
        )
        rendered = render_branded_email(
            brand,
            fragment,
            company_name="Acme & Sons",
            site_url="https://acme.example",
            cta_label="Book a demo",
        )
        assert sanitize_email_html(rendered, profile="branded") == rendered

    @pytest.mark.parametrize(
        "brand",
        [
            {},
            {"logo_url": "https://acme.example/l.png"},
            {"cta_url": "https://acme.example/demo"},
            FULL_BRAND,
        ],
    )
    def test_fixed_point_across_token_combinations(self, brand: dict) -> None:
        rendered = render_layout_preview(
            BrandKit.model_validate(brand),
            company_name="O'Brien <Co>",
            site_url="https://acme.example",
        )
        assert sanitize_email_html(rendered, profile="branded") == rendered

    def test_default_profile_would_strip_the_layout(self) -> None:
        rendered = render_layout_preview(
            BrandKit.model_validate(FULL_BRAND), company_name="Acme"
        )
        stripped = sanitize_email_html(rendered)
        assert stripped != rendered
        assert "padding" not in stripped and "border-radius" not in stripped

    def test_renders_expected_structure(self) -> None:
        brand = BrandKit.model_validate(FULL_BRAND)
        html = render_branded_email(
            brand,
            '<p>Body <a href="https://acme.example/x">link</a></p>',
            company_name="Acme",
            site_url="https://acme.example",
        )
        assert 'src="https://acme.example/logo.png"' in html
        assert 'bgcolor="#123456"' in html
        assert "Georgia" in html
        assert 'href="https://acme.example/demo?ref=email&amp;x=1"' in html
        assert ">Book a demo</a>" in html
        # link inside the body picks up the accent colour
        assert '<a style="color: #ff5500" href="https://acme.example/x">' in html
        assert "<style" not in html and "<script" not in html and "class=" not in html

    def test_logo_alt_text_is_readable_when_the_image_is_blocked(self) -> None:
        html = render_branded_email(
            BrandKit.model_validate({**FULL_BRAND, "primary": "#000000"}),
            "<p>Hi</p>",
            company_name="Acme",
        )
        img = html[html.index("<img") : html.index(">", html.index("<img")) + 1]
        assert 'alt="Acme"' in img
        assert "color: #ffffff" in img and "font-weight: bold" in img

    def test_text_header_without_logo_and_no_button_without_cta_url(self) -> None:
        html = render_branded_email(
            BrandKit(), "<p>Hi</p>", company_name="Acme Inc", site_url=None
        )
        assert "<img" not in html
        assert "Acme Inc</span>" in html
        assert "inline-block" not in html

    def test_no_header_row_without_logo_or_name(self) -> None:
        html = render_branded_email(BrandKit(), "<p>Hi</p>")
        assert 'bgcolor="#1f2937"' not in html

    def test_company_name_is_escaped(self) -> None:
        html = render_branded_email(
            BrandKit(), "<p>x</p>", company_name='<img src=x onerror="a">'
        )
        assert "<img" not in html
        assert "&lt;img" in html

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"company_name": "{{first_name}}"},
            {"company_name": "a\x00b"},
            {"site_url": "http://acme.example"},
            {"site_url": 'https://acme.example/"x'},
            {"cta_label": "{{x}}"},
        ],
    )
    def test_render_revalidates_tokens(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            render_branded_email(BrandKit(), "<p>x</p>", **kwargs)

    def test_render_rejects_a_kit_built_without_validation(self) -> None:
        bad = BrandKit.model_construct(
            logo_url='https://acme.example/"onerror="x', primary="#123456"
        )
        with pytest.raises(ValueError):
            render_branded_email(bad, "<p>x</p>", company_name="Acme")

    def test_default_cta_label(self) -> None:
        assert default_cta_label("Book a 15 minute demo.") == "Book a 15 minute demo"
        assert default_cta_label("") == "Learn more"
        long = default_cta_label(
            "Reply to this email with a time that works for a quick chat next week"
        )
        assert 0 < len(long) <= 40


class TestBrandedSanitizerProfile:
    def test_default_profile_output_is_unchanged(self) -> None:
        source = (
            '<table width="600" bgcolor="#fff" cellpadding="4"><tr><td '
            'style="padding: 4px; color: red; border-radius: 3px">x</td></tr></table>'
            '<p style="color: #123456; display: block">y</p>'
        )
        assert sanitize_email_html(source) == sanitize_email_html(
            source, profile="default"
        )
        out = sanitize_email_html(source)
        assert "padding" not in out and "bgcolor" not in out and "width" not in out
        assert "color: red" in out and "color: #123456" in out

    def test_branded_profile_keeps_layout_properties(self) -> None:
        source = (
            '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
            'border="0" bgcolor="#ABCDEF" style="max-width: 600px; margin: 0 auto">'
            '<tr><td valign="top" style="padding: 12px 24px; border-radius: 6px; '
            'border: 1px solid #cccccc">x</td></tr></table>'
        )
        out = sanitize_email_html(source, profile="branded")
        for fragment in (
            'role="presentation"',
            'width="600"',
            'cellpadding="0"',
            'bgcolor="#abcdef"',
            'valign="top"',
            "padding: 12px 24px",
            "border-radius: 6px",
            "border: 1px solid #cccccc",
            "max-width: 600px",
            "margin: 0 auto",
        ):
            assert fragment in out

    @pytest.mark.parametrize(
        "source,forbidden",
        [
            ('<td style="background: url(https://evil.example/x)">a</td>', "url("),
            ('<td style="padding: 1px; behavior: url(x)">a</td>', "behavior"),
            ('<td style="width: expression(alert(1))">a</td>', "expression"),
            ('<td style="width: 10px !important">a</td>', "important"),
            ('<td style="display: none">a</td>', "display"),
            ('<td style="display: flex">a</td>', "flex"),
            ('<td style="padding: 1px; margin: 5px\\41">a</td>', "\\"),
            ('<td style="border: 1px solid url(x)">a</td>', "url"),
            ('<td style="border-radius: 6px}body{color:red">a</td>', "body"),
            ('<table bgcolor="red">a</table>', "bgcolor"),
            ('<table bgcolor="#12345">a</table>', "bgcolor"),
            ('<table cellpadding="100">a</table>', "cellpadding"),
            ('<table role="button">a</table>', "role"),
            ('<td width="9999999">a</td>', "width"),
            ('<td onclick="alert(1)" style="padding: 1px">a</td>', "onclick"),
            ("<style>td{padding:1px}</style><td>a</td>", "<style"),
            ("<script>alert(1)</script><td>a</td>", "script"),
            ('<a href="javascript:alert(1)" style="padding: 1px">a</a>', "javascript"),
            ('<img src="http://evil.example/x.png" width="10">', "<img"),
            ('<img src="data:image/png;base64,AAAA" width="10">', "<img"),
            ("<button>x</button>", "button"),
            ("<svg><circle/></svg>", "svg"),
            ('<div class="x" id="y" data-a="b">a</div>', "class"),
            ('<link rel="stylesheet" href="https://evil.example/a.css">', "<link"),
        ],
    )
    def test_branded_profile_still_blocks_unsafe_input(
        self, source: str, forbidden: str
    ) -> None:
        assert forbidden not in sanitize_email_html(source, profile="branded")

    def test_unknown_profile_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            sanitize_email_html("<p>x</p>", profile="everything")

    def test_empty_input(self) -> None:
        assert sanitize_email_html("", profile="branded") == ""


class TestConfigSchemaCompatibility:
    def test_existing_config_keeps_its_stored_form_and_digest(self) -> None:
        config = PersonalizationConfig.model_validate(CONFIG)
        # Exactly the eight historic keys: nothing new leaks into the stored form.
        assert config.canonical() == CONFIG
        assert set(config.canonical()) == set(CONFIG)
        steps: list[dict] = []
        assert compute_approval_digest(
            config=config.canonical(), steps=steps, model="m"
        ) == compute_approval_digest(config=CONFIG, steps=steps, model="m")

    def test_resaving_an_unchanged_config_does_not_change_the_digest(self) -> None:
        once = PersonalizationConfig.model_validate(CONFIG).canonical()
        twice = PersonalizationConfig.model_validate(once).canonical()
        assert once == twice

    def test_objective_core_excludes_company_brand_and_format(self) -> None:
        config = PersonalizationConfig.model_validate(
            {
                **CONFIG,
                "email_format": "HTML",
                "company": {"source": "MANUAL", "company_name": "Acme"},
                "brand": FULL_BRAND,
            }
        )
        assert config.objective_core() == CONFIG
        assert set(config.canonical()) == set(CONFIG) | {
            "email_format",
            "company",
            "brand",
        }

    def test_new_fields_change_the_digest(self) -> None:
        base = PersonalizationConfig.model_validate(CONFIG).canonical()
        html = PersonalizationConfig.model_validate(
            {**CONFIG, "email_format": "HTML"}
        ).canonical()
        assert compute_approval_digest(
            config=base, steps=[], model="m"
        ) != compute_approval_digest(config=html, steps=[], model="m")

    def test_parse_config_round_trips_the_full_shape(self) -> None:
        raw = PersonalizationConfig.model_validate(
            {
                **CONFIG,
                "email_format": "TEXT",
                "company": {
                    "source": "WEBSITE",
                    "url": "https://acme.example",
                    "company_name": "Acme",
                    "summary": "We sell anvils.",
                    "services": ["Anvils"],
                    "industries": ["Manufacturing"],
                    "audience": "Coyotes",
                    "tone_of_voice": "dry",
                    "key_messages": ["Heavy duty"],
                },
            }
        ).canonical()
        parsed = parse_config(json.loads(json.dumps(raw)))
        assert parsed is not None and parsed.company is not None
        assert parsed.company.company_name == "Acme"
        assert parsed.canonical() == raw

    @pytest.mark.parametrize(
        "extra",
        [
            {"email_format": "PDF"},
            {"company": {"source": "OTHER", "company_name": "A"}},
            {"company": {"source": "MANUAL", "company_name": ""}},
            {"company": {"source": "MANUAL", "company_name": "{{first_name}}"}},
            {
                "company": {
                    "source": "MANUAL",
                    "company_name": "A",
                    "url": "http://a.co",
                }
            },
            {
                "company": {
                    "source": "MANUAL",
                    "company_name": "A",
                    "services": ["x"] * 9,
                }
            },
            {"company": {"source": "MANUAL", "company_name": "A", "x": 1}},
            {"brand": {"primary": "blue"}},
            {"brand": {"unknown": 1}},
        ],
    )
    def test_rejects_invalid_new_fields(self, extra: dict) -> None:
        with pytest.raises(ValidationError):
            PersonalizationConfig.model_validate({**CONFIG, **extra})

    def test_size_budget_is_enforced_below_the_database_check(self) -> None:
        assert MAX_CONFIG_BYTES < 16384
        url = "https://acme.example/" + "a" * 950
        huge = {
            **CONFIG,
            "objective": "o" * 1000,
            "offer": "f" * 1000,
            "cta": "c" * 500,
            "target": "t" * 500,
            "problem_solved": "p" * 1000,
            "tone": "n" * 200,
            "must_mention": [f"m{i}" + "x" * 190 for i in range(10)],
            "never_say": [f"n{i}" + "x" * 190 for i in range(10)],
            "company": {
                "source": "WEBSITE",
                "url": url,
                "company_name": "A" * 120,
                "summary": "s" * 1000,
                "services": ["v" * 80] * 8,
                "industries": ["i" * 60] * 6,
                "audience": "a" * 400,
                "tone_of_voice": "t" * 200,
                "key_messages": ["k" * 150] * 5,
            },
            "brand": {**FULL_BRAND, "logo_url": url, "cta_url": url},
        }
        with pytest.raises(ValidationError, match="too large"):
            PersonalizationConfig.model_validate(huge)

    def test_size_is_measured_in_the_jsonb_text_form(self) -> None:
        small = {"a": "é", "b": [1, 2]}
        assert config_size_bytes(small) == len('{"a": "é", "b": [1, 2]}'.encode())

    def test_company_profile_strips_and_bounds(self) -> None:
        profile = CompanyProfile.model_validate(
            {"source": "MANUAL", "company_name": "  Acme  ", "services": [" A "]}
        )
        assert profile.company_name == "Acme" and profile.services == ["A"]


class TestSequenceServiceProfileSelection:
    """The branded profile is chosen from stored state, never from the request."""

    HTML_CONFIG = {**CONFIG, "email_format": "HTML"}

    def _service(self):
        from unittest.mock import MagicMock

        from app.modules.campaigns.sequence_service import SequenceService

        return SequenceService(MagicMock())

    @pytest.mark.parametrize(
        "campaign_type,config,expected",
        [
            ("HYPER_PERSONALIZED", {**CONFIG, "email_format": "HTML"}, "branded"),
            ("HYPER_PERSONALIZED", {**CONFIG, "email_format": "TEXT"}, "default"),
            ("HYPER_PERSONALIZED", CONFIG, "default"),
            ("HYPER_PERSONALIZED", None, "default"),
            ("STANDARD", {**CONFIG, "email_format": "HTML"}, "default"),
            (None, {**CONFIG, "email_format": "HTML"}, "default"),
        ],
    )
    def test_profile(self, campaign_type, config, expected) -> None:
        service = self._service()
        campaign = {"campaign_type": campaign_type}
        sequence = None if config is None else {"personalization_config": config}
        assert service._sanitizer_profile(campaign, sequence) == expected

    def test_no_sequence_means_default(self) -> None:
        assert (
            self._service()._sanitizer_profile(
                {"campaign_type": "HYPER_PERSONALIZED"}, None
            )
            == "default"
        )

    def test_resolve_email_content_applies_the_profile(self) -> None:
        from uuid import uuid4

        from app.api.deps import WorkspaceContext

        service = self._service()
        context = WorkspaceContext(
            workspace_id=uuid4(), user_id=uuid4(), role_code="MEMBER"
        )
        layout = render_layout_preview(
            BrandKit.model_validate(FULL_BRAND), company_name="Acme"
        )
        kwargs = dict(
            source_template_version_id=None,
            email_subject="Hello {{first_name|there}}",
            email_body_html=layout,
        )
        _, branded, _, _ = service._resolve_email_content(
            context, profile="branded", **kwargs
        )
        _, default, _, _ = service._resolve_email_content(context, **kwargs)
        assert branded == layout
        assert default != layout and "padding" not in default


class TestAttemptBranding:
    """The per-lead seam (shared by JIT and previews) applies the brand layout."""

    def _inputs(self, config: PersonalizationConfig, **kw):
        import uuid

        from app.modules.personalization.service import GenerationInputs
        from tests.support.personalization_fakes import FROZEN, REF_BODY, REF_SUBJECT

        base = {
            "workspace_id": uuid.uuid4(),
            "frozen_variables": FROZEN,
            "objective": config,
            "reference_subject": REF_SUBJECT,
            "reference_body_html": REF_BODY,
            "reference_preheader": None,
            "step_position": 1,
        }
        base.update(kw)
        return GenerationInputs(**base)

    def _service(self, model=None):
        from app.modules.personalization.fake_model import FakeModel
        from app.modules.personalization.service import PersonalizationService
        from tests.support.personalization_fakes import FakeResearch

        return PersonalizationService(
            model=model or FakeModel(), research=FakeResearch(), min_facts=2
        )

    def _config(self, **extra) -> PersonalizationConfig:
        from tests.support.personalization_fakes import CONFIG as FAKE_CONFIG

        return PersonalizationConfig.model_validate({**FAKE_CONFIG, **extra})

    HTML_EXTRA = {
        "email_format": "HTML",
        "company": {
            "source": "WEBSITE",
            "url": "https://acme.example",
            "company_name": "Acme",
            "summary": "Tools for revenue teams",
        },
        "brand": FULL_BRAND,
    }

    def test_html_campaign_wraps_generated_paragraphs_in_the_layout(self) -> None:
        from app.modules.campaigns.message_rendering import compute_content_digest

        outcome = self._service().attempt(self._inputs(self._config(**self.HTML_EXTRA)))
        assert outcome.kind == "GENERATED"
        assert 'bgcolor="#123456"' in outcome.body_html
        assert 'src="https://acme.example/logo.png"' in outcome.body_html
        assert 'href="https://acme.example/demo?ref=email&amp;x=1"' in outcome.body_html
        assert outcome.renderer_version == 2
        assert outcome.content_digest == compute_content_digest(
            outcome.subject, outcome.body_html, 2
        )

    def test_preheader_still_precedes_the_layout(self) -> None:
        outcome = self._service().attempt(
            self._inputs(
                self._config(**self.HTML_EXTRA), reference_preheader="Note for you"
            )
        )
        assert outcome.body_html.startswith('<div style="display:none')
        assert "Note for you" in outcome.body_html and "bgcolor" in outcome.body_html

    @pytest.mark.parametrize("extra", [{}, {"email_format": "TEXT"}])
    def test_text_and_legacy_campaigns_are_not_wrapped(self, extra) -> None:
        legacy = self._service().attempt(self._inputs(self._config()))
        text = self._service().attempt(self._inputs(self._config(**extra)))
        assert text.body_html == legacy.body_html
        assert "bgcolor" not in text.body_html and "<table" not in text.body_html

    def test_html_format_without_a_brand_kit_uses_defaults(self) -> None:
        outcome = self._service().attempt(
            self._inputs(self._config(email_format="HTML"))
        )
        assert outcome.kind == "GENERATED" and "<table" in outcome.body_html

    def test_thin_context_fallback_is_not_double_wrapped(self) -> None:
        from tests.support.personalization_fakes import REF_SUBJECT

        layout = render_layout_preview(
            BrandKit.model_validate(FULL_BRAND), company_name="Acme"
        )
        outcome = self._service().attempt(
            self._inputs(
                self._config(**self.HTML_EXTRA),
                frozen_variables={"first_name": "Sarah"},
                reference_body_html=layout,
                reference_subject=REF_SUBJECT,
            )
        )
        assert outcome.kind == "FALLBACK"
        assert outcome.body_html.count('<table role="presentation" width="100%"') == 1

    def test_the_per_lead_prompt_never_sees_company_brand_or_format(self) -> None:
        from app.modules.personalization.fake_model import FakeModel
        from app.modules.personalization.prompt_builder import build_chat_messages

        plain_model, html_model = FakeModel(), FakeModel()
        self._service(plain_model).attempt(self._inputs(self._config()))
        self._service(html_model).attempt(self._inputs(self._config(**self.HTML_EXTRA)))
        plain_req, html_req = plain_model.requests[0], html_model.requests[0]
        assert set(html_req.objective) == set(plain_req.objective)
        # Byte-identical chat payload => PROMPT_VERSION and approvals are untouched.
        assert build_chat_messages(html_req) == build_chat_messages(plain_req)
