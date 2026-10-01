"""AI authoring endpoints of the personalization API service (ADR-0016): company
analysis and the layout preview. Repositories are mocked; the model is the
deterministic fake and the fetcher is scripted, so nothing touches the network."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.api.deps import WorkspaceContext
from app.core.config import Settings
from app.core.errors import AppError
from app.modules.personalization.api_service import PersonalizationApiService
from app.modules.personalization.brand_kit import BrandKit
from app.modules.personalization.fake_model import FakeModel
from app.modules.personalization.ports import ModelTimeout
from app.modules.personalization.schemas import (
    BusinessInfoIn,
    CompanyAnalysisIn,
    LayoutPreviewIn,
)
from tests.support.personalization_fakes import WS
from tests.test_company_analysis import ScriptedFetcher, _routes

CAMPAIGN_ID = uuid.uuid4()
CTX = WorkspaceContext(workspace_id=WS, user_id=uuid.uuid4(), role_code="MEMBER")
DESCRIPTION = "We make heavy duty anvils for professional blacksmiths and film studios."


def _settings(**kw) -> Settings:
    base = {
        "database_url": "sqlite+pysqlite:///:memory:",
        "redis_url": "redis://x",
        "supabase_url": "https://x.supabase.co",
        "personalization_enabled": True,
        "personalization_model": "test-model",
        "personalization_openai_api_key": "sk-test-not-real",
    }
    base.update(kw)
    return Settings(**base)


def _campaign(**kw) -> dict:
    row = {
        "id": CAMPAIGN_ID,
        "workspace_id": WS,
        "campaign_type": "HYPER_PERSONALIZED",
        "status": "DRAFT",
    }
    row.update(kw)
    return row


def _service(campaign=None, model=None, fetcher=None, **settings_kw):
    model = model or FakeModel()
    service = PersonalizationApiService(
        MagicMock(),
        _settings(**settings_kw),
        drafting_model_factory=lambda: model,
        fetcher=fetcher or ScriptedFetcher(_routes()),  # type: ignore[arg-type]
    )
    service.campaigns = MagicMock()
    service.campaigns.get_campaign.return_value = campaign
    return service, model


class TestAnalyzeCompany:
    def test_website_analysis_returns_profile_brand_and_suggestions(self) -> None:
        service, model = _service(_campaign())
        out = service.analyze_company(
            CTX, CAMPAIGN_ID, CompanyAnalysisIn(url="acme.example")
        )
        assert out.source == "WEBSITE" and out.pages_read == 2
        assert out.profile.company_name == "Acme"
        assert out.brand is not None and out.brand.primary == "#123456"
        assert out.suggestions.cta
        assert model.analysis_requests[0].workspace_ref == str(WS)

    def test_business_info_analysis(self) -> None:
        service, _ = _service(_campaign())
        out = service.analyze_company(
            CTX,
            CAMPAIGN_ID,
            CompanyAnalysisIn(
                business=BusinessInfoIn(
                    company_name="Acme Anvils", description=DESCRIPTION
                )
            ),
        )
        assert out.source == "MANUAL" and out.brand is None and out.final_url is None
        assert out.profile.company_name == "Acme Anvils"

    def test_lookups_are_workspace_scoped(self) -> None:
        service, _ = _service(_campaign())
        service.analyze_company(CTX, CAMPAIGN_ID, CompanyAnalysisIn(url="acme.example"))
        service.campaigns.get_campaign.assert_called_once_with(
            workspace_id=WS, campaign_id=CAMPAIGN_ID
        )

    def test_writes_nothing(self) -> None:
        service, _ = _service(_campaign())
        service.analyze_company(CTX, CAMPAIGN_ID, CompanyAnalysisIn(url="acme.example"))
        calls = [c[0] for c in service.campaigns.method_calls]
        assert calls == ["get_campaign"]  # no set_/insert_/create_ calls

    def test_releases_the_connection_before_any_network_or_model_work(self) -> None:
        order: list[str] = []
        service, model = _service(_campaign())
        service.session.commit.side_effect = lambda: order.append("commit")
        original = model.analyze_company

        def spy(request):
            order.append("model")
            return original(request)

        model.analyze_company = spy  # type: ignore[method-assign]
        fetcher = service._fetcher
        original_fetch = fetcher.fetch  # type: ignore[union-attr]

        def fetch_spy(url, **kw):
            order.append("fetch")
            return original_fetch(url, **kw)

        fetcher.fetch = fetch_spy  # type: ignore[union-attr,method-assign]
        service.analyze_company(CTX, CAMPAIGN_ID, CompanyAnalysisIn(url="acme.example"))
        assert order[0] == "commit" and "fetch" in order and order[-1] == "model"
        assert order.index("commit") < order.index("fetch") < order.index("model")

    def test_unknown_or_foreign_campaign_is_404(self) -> None:
        service, model = _service(None)
        with pytest.raises(AppError) as info:
            service.analyze_company(
                CTX, CAMPAIGN_ID, CompanyAnalysisIn(url="acme.example")
            )
        assert info.value.status_code == 404 and model.analysis_requests == []

    def test_standard_campaign_is_refused(self) -> None:
        service, _ = _service(_campaign(campaign_type="STANDARD"))
        with pytest.raises(AppError) as info:
            service.analyze_company(
                CTX, CAMPAIGN_ID, CompanyAnalysisIn(url="acme.example")
            )
        assert info.value.code == "not_personalized_campaign"

    def test_non_draft_campaign_is_refused(self) -> None:
        service, _ = _service(_campaign(status="RUNNING"))
        with pytest.raises(AppError) as info:
            service.analyze_company(
                CTX, CAMPAIGN_ID, CompanyAnalysisIn(url="acme.example")
            )
        assert info.value.code == "state_conflict" and info.value.status_code == 409

    def test_disabled_deployment_is_refused(self) -> None:
        service, model = _service(_campaign(), personalization_enabled=False)
        with pytest.raises(AppError) as info:
            service.analyze_company(
                CTX, CAMPAIGN_ID, CompanyAnalysisIn(url="acme.example")
            )
        assert (
            info.value.code == "personalization_disabled"
            and model.analysis_requests == []
        )

    def test_missing_key_is_a_clear_503_and_no_model_is_built(self) -> None:
        built: list[int] = []
        service, _ = _service(_campaign(), personalization_openai_api_key="")
        service._drafting_model_factory = lambda: built.append(1)  # type: ignore[assignment,return-value]
        with pytest.raises(AppError) as info:
            service.analyze_company(
                CTX, CAMPAIGN_ID, CompanyAnalysisIn(url="acme.example")
            )
        assert info.value.code == "personalization_not_configured"
        assert info.value.status_code == 503 and built == []

    @pytest.mark.parametrize(
        "payload",
        [
            CompanyAnalysisIn(),
            CompanyAnalysisIn(url="   "),
            CompanyAnalysisIn(
                url="acme.example",
                business=BusinessInfoIn(company_name="Acme", description=DESCRIPTION),
            ),
        ],
    )
    def test_exactly_one_source_is_required(self, payload) -> None:
        service, model = _service(_campaign())
        with pytest.raises(AppError) as info:
            service.analyze_company(CTX, CAMPAIGN_ID, payload)
        assert (
            info.value.code == "company_source_required"
            and info.value.status_code == 422
        )
        assert model.analysis_requests == []

    def test_site_errors_propagate_with_their_codes(self) -> None:
        service, _ = _service(_campaign(), fetcher=ScriptedFetcher({}))
        with pytest.raises(AppError) as info:
            service.analyze_company(
                CTX, CAMPAIGN_ID, CompanyAnalysisIn(url="acme.example")
            )
        assert info.value.code == "website_unreachable"

    def test_model_failure_still_returns_an_editable_result(self) -> None:
        service, _ = _service(
            _campaign(), model=FakeModel(analysis_script=[ModelTimeout()])
        )
        out = service.analyze_company(
            CTX, CAMPAIGN_ID, CompanyAnalysisIn(url="acme.example")
        )
        assert "analysis_model_failed" in out.warnings and out.model is None
        assert out.profile.company_name == "Acme" and out.brand is not None

    def test_the_adapter_is_closed_even_when_analysis_fails(self) -> None:
        model = FakeModel()
        model.close = MagicMock()  # type: ignore[attr-defined]
        service, _ = _service(_campaign(), model=model, fetcher=ScriptedFetcher({}))
        with pytest.raises(AppError):
            service.analyze_company(
                CTX, CAMPAIGN_ID, CompanyAnalysisIn(url="acme.example")
            )
        model.close.assert_called_once()  # type: ignore[attr-defined]

    def test_response_never_contains_the_api_key(self) -> None:
        service, _ = _service(
            _campaign(), personalization_openai_api_key="sk-live-very-secret"
        )
        out = service.analyze_company(
            CTX, CAMPAIGN_ID, CompanyAnalysisIn(url="acme.example")
        )
        assert "sk-live-very-secret" not in out.model_dump_json()


class TestLayoutPreview:
    BRAND = BrandKit(
        logo_url="https://acme.example/logo.png",
        primary="#123456",
        accent="#ff5500",
        cta_url="https://acme.example/demo",
        cta_label="Book a demo",
    )

    def test_renders_with_the_real_layout_and_needs_no_model_or_key(self) -> None:
        built: list[int] = []
        service, _ = _service(_campaign(), personalization_openai_api_key="")
        service._drafting_model_factory = lambda: built.append(1)  # type: ignore[assignment,return-value]
        out = service.layout_preview(
            CTX, CAMPAIGN_ID, LayoutPreviewIn(brand=self.BRAND, company_name="Acme")
        )
        assert 'bgcolor="#123456"' in out.html and "Book a demo" in out.html
        assert built == []  # AI unavailable must not disable brand editing

    def test_reports_contrast_warnings(self) -> None:
        service, _ = _service(_campaign())
        out = service.layout_preview(
            CTX,
            CAMPAIGN_ID,
            LayoutPreviewIn(brand=BrandKit(text="#eeeeee", background="#ffffff")),
        )
        assert "text_contrast_low" in out.warnings

    def test_guards_apply(self) -> None:
        for campaign, code in (
            (None, "not_found"),
            (_campaign(campaign_type="STANDARD"), "not_personalized_campaign"),
            (_campaign(status="RUNNING"), "state_conflict"),
        ):
            service, _ = _service(campaign)
            with pytest.raises(AppError) as info:
                service.layout_preview(
                    CTX, CAMPAIGN_ID, LayoutPreviewIn(brand=self.BRAND)
                )
            assert info.value.code == code

    def test_invalid_site_url_is_a_422(self) -> None:
        service, _ = _service(_campaign())
        with pytest.raises(AppError) as info:
            service.layout_preview(
                CTX,
                CAMPAIGN_ID,
                LayoutPreviewIn(brand=self.BRAND, site_url="http://not-https.example"),
            )
        assert info.value.code == "invalid_brand" and info.value.status_code == 422

    def test_invalid_brand_tokens_are_rejected_by_the_schema(self) -> None:
        with pytest.raises(ValueError):
            LayoutPreviewIn.model_validate({"brand": {"primary": "url(javascript:x)"}})
