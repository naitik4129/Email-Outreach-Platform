"""Open tracking: signed tokens, pixel injection and the public endpoint."""

# ruff: noqa: E501 -- test data (raw MIME, SQL, header values) reads better unwrapped.

from __future__ import annotations

import base64
import re
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.config import Settings
from app.main import app
from app.modules.mailboxes.providers.message_builder import generate_message_id
from app.modules.tracking.classify import classify_open
from app.modules.tracking.pixel import (
    TRANSPARENT_GIF,
    inject_open_pixel,
    open_pixel_url,
    open_tracking_ready,
)
from app.modules.tracking.tokens import make_open_token, parse_open_token

KEY = "unit-test-signing-key"
WS = uuid.uuid4()
MSG = uuid.uuid4()


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "open_tracking_enabled": True,
        "tracking_base_url": "https://outly.example.com",
        "tracking_signing_key": KEY,
    }
    base.update(overrides)
    return Settings.current().model_copy(update=base)


class TestTokens:
    def test_round_trip(self) -> None:
        token = make_open_token(WS, MSG, KEY)
        assert parse_open_token(token, KEY) == (WS, MSG, None)

    def test_v2_round_trip_carries_the_send_time(self) -> None:
        sent = datetime(2026, 9, 26, 10, 0, 0, tzinfo=UTC)
        token = make_open_token(WS, MSG, KEY, sent_at=sent)
        assert parse_open_token(token, KEY) == (WS, MSG, sent)

    def test_send_time_is_signed_and_cannot_be_rewritten(self) -> None:
        sent = datetime(2026, 9, 26, 10, 0, 0, tzinfo=UTC)
        token = make_open_token(WS, MSG, KEY, sent_at=sent)
        raw = bytearray(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        raw[32:36] = (int(sent.timestamp()) - 3600).to_bytes(4, "big")
        forged = base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode()
        assert parse_open_token(forged, KEY) is None
        assert parse_open_token(token, "another-key") is None

    def test_token_is_urlsafe_and_carries_no_readable_ids(self) -> None:
        token = make_open_token(WS, MSG, KEY)
        assert re.fullmatch(r"[A-Za-z0-9_-]+", token)
        assert str(MSG) not in token and str(WS) not in token

    def test_wrong_key_tampering_and_garbage_are_rejected(self) -> None:
        token = make_open_token(WS, MSG, KEY)
        assert parse_open_token(token, "another-key") is None
        flipped = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
        assert parse_open_token(flipped, KEY) is None
        for junk in ("", "x", "not base64 !!", "A" * 500, token + "extra"):
            assert parse_open_token(junk, KEY) is None
        assert parse_open_token(token, "") is None

    def test_a_token_for_one_message_cannot_name_another(self) -> None:
        token = make_open_token(WS, MSG, KEY)
        raw = bytearray(base64.urlsafe_b64decode(token + "=="))
        raw[16:32] = uuid.uuid4().bytes  # swap in another message id
        forged = base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode()
        assert parse_open_token(forged, KEY) is None


class TestPixel:
    def test_gif_is_a_valid_1x1_image(self) -> None:
        assert TRANSPARENT_GIF.startswith(b"GIF89a") and TRANSPARENT_GIF.endswith(b";")
        width = int.from_bytes(TRANSPARENT_GIF[6:8], "little")
        height = int.from_bytes(TRANSPARENT_GIF[8:10], "little")
        assert (width, height) == (1, 1)

    def test_injected_before_the_closing_body_tag(self) -> None:
        html = "<html><body><p>Hi</p></body></html>"
        out = inject_open_pixel(html, "https://x.test/p.gif")
        assert out.index("<img") < out.index("</body>")
        assert out.count("<img") == 1 and 'src="https://x.test/p.gif"' in out

    def test_appended_when_there_is_no_body_tag_and_url_is_escaped(self) -> None:
        out = inject_open_pixel("<p>Hi</p>", 'https://x.test/p.gif?a=1&b="2"')
        assert out.startswith("<p>Hi</p><img")
        assert "&amp;b=&quot;2&quot;" in out

    def test_uses_the_last_body_tag(self) -> None:
        out = inject_open_pixel("<body>a</body><div>&lt;/body&gt;</div><body>b</body>", "https://x.test/p.gif")
        assert out.endswith("</body>") and out.rindex("<img") < out.rindex("</body>")

    @pytest.mark.parametrize(
        "overrides",
        [
            {"open_tracking_enabled": False},
            {"tracking_base_url": ""},
            {"tracking_signing_key": ""},
            {"tracking_base_url": "javascript:alert(1)"},
            {"tracking_base_url": "not a url"},
        ],
    )
    def test_never_injected_unless_fully_configured(self, overrides: dict[str, object]) -> None:
        assert open_tracking_ready(settings(**overrides)) is False

    def test_configured_url_points_at_the_public_endpoint(self) -> None:
        cfg = settings(tracking_base_url="https://outly.example.com/")
        assert open_tracking_ready(cfg) is True
        url = open_pixel_url(cfg, WS, MSG)
        assert url.startswith("https://outly.example.com/api/v1/t/o/") and url.endswith(".gif")
        token = url.rsplit("/", 1)[1].removesuffix(".gif")
        parsed = parse_open_token(token, KEY)
        assert parsed is not None and (parsed.workspace_id, parsed.message_id) == (WS, MSG)
        assert parsed.sent_at is not None  # send time is minted into the URL


SENT = datetime(2026, 9, 26, 10, 0, 0, tzinfo=UTC)
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
GMAIL_PROXY_UA = "Mozilla/5.0 (Windows NT 5.1; rv:11.0) Gecko Firefox/11.0 (via ggpht.com GoogleImageProxy)"


def classify(
    ua: str | None = BROWSER_UA,
    *,
    after: int = 600,
    sent_at: datetime | None = SENT,
    headers: dict[str, str] | None = None,
) -> tuple[bool, str]:
    result = classify_open(
        user_agent=ua,
        headers=headers or ({"user-agent": ua} if ua else {}),
        sent_at=sent_at,
        now=SENT + timedelta(seconds=after),
        min_delay_seconds=60,
    )
    return result.qualified, result.reason


class TestClassifier:
    """Default-deny: only a hit that looks like a person is an open."""

    def test_a_normal_browser_or_mail_client_well_after_send_counts(self) -> None:
        assert classify() == (True, "human_like")
        assert classify("Thunderbird/115.0")[0] is True
        assert classify("YahooMailProxy")[0] is True

    def test_gmail_image_proxy_counts_because_real_gmail_opens_arrive_through_it(self) -> None:
        assert classify(GMAIL_PROXY_UA) == (True, "human_like")

    @pytest.mark.parametrize("after", [0, 2, 59])
    def test_delivery_time_fetches_never_count_even_with_a_browser_ua(self, after: int) -> None:
        # The bulk-send case: every recipient's scanner fetches seconds after send.
        assert classify(GMAIL_PROXY_UA, after=after) == (False, "too_soon_after_send")

    def test_the_minimum_delay_boundary(self) -> None:
        assert classify(after=60)[0] is True

    def test_tokens_without_a_send_time_cannot_prove_it_and_never_count(self) -> None:
        assert classify(sent_at=None) == (False, "no_send_time")

    @pytest.mark.parametrize("name", ["purpose", "sec-purpose", "x-purpose", "x-moz"])
    @pytest.mark.parametrize("value", ["prefetch", "Preview"])
    def test_prefetch_headers_do_not_count(self, name: str, value: str) -> None:
        assert classify(headers={name: value}) == (False, "prefetch_header")

    @pytest.mark.parametrize("ua", [None, "", "   "])
    def test_missing_user_agent_does_not_count(self, ua: str | None) -> None:
        assert classify(ua) == (False, "missing_user_agent")

    @pytest.mark.parametrize(
        "ua",
        [
            "curl/8.4.0",
            "python-requests/2.31",
            "Go-http-client/2.0",
            "Mozilla/5.0 HeadlessChrome/120.0 Safari/537.36",
            "Mozilla/5.0 (compatible; Googlebot/2.1)",
            "Mozilla/5.0 (compatible; Barracuda Sentinel)",
            "Mimecast Link Scanner",
            "Mozilla/5.0 SafeLinks Protection",
            "Slackbot-LinkExpanding 1.0",
            "facebookexternalhit/1.1",
        ],
    )
    def test_scanners_and_bots_do_not_count_even_when_they_look_like_browsers(self, ua: str) -> None:
        assert classify(ua) == (False, "automated_user_agent")

    @pytest.mark.parametrize("ua", ["SomethingUnknown/1.0", "MyCustomFetcherApp", "Java"])
    def test_unrecognised_agents_are_not_counted(self, ua: str) -> None:
        assert classify(ua)[0] is False


class TestMessageId:
    def test_unique_and_on_the_senders_domain(self) -> None:
        first, second = generate_message_id("Sales@Acme.COM"), generate_message_id("sales@acme.com")
        assert first != second
        assert re.fullmatch(r"<[0-9a-f]{32}@acme\.com>", first)

    @pytest.mark.parametrize("bad", ["no-at-sign", "x@", "x@bad domain\r\nBcc: e@x.co"])
    def test_hostile_or_missing_domains_fall_back_safely(self, bad: str) -> None:
        value = generate_message_id(bad)
        assert "\r" not in value and "\n" not in value and " " not in value
        assert value.startswith("<") and value.endswith(">")


class TestEndpointWithoutDatabase:
    """The invalid-token paths must answer without ever writing."""

    @pytest.fixture()
    def client(self, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
        db = MagicMock()
        app.dependency_overrides[get_db] = lambda: db
        configured = settings()  # built BEFORE patching Settings.current
        monkeypatch.setattr(
            "app.api.v1.tracking.Settings.current", classmethod(lambda cls: configured)
        )
        yield TestClient(app), db
        app.dependency_overrides.clear()

    @pytest.mark.parametrize("name", ["garbage.gif", "x", "%00.gif", "A" * 200 + ".gif"])
    def test_invalid_tokens_get_the_gif_and_never_touch_the_database(
        self, client: tuple[TestClient, MagicMock], name: str
    ) -> None:
        test_client, db = client
        response = test_client.get(f"/api/v1/t/o/{name}")
        assert response.status_code == 200
        assert response.content == TRANSPARENT_GIF
        assert response.headers["content-type"] == "image/gif"
        assert "no-store" in response.headers["cache-control"]
        db.execute.assert_not_called()

    def test_head_is_answered_without_recording(self, client: tuple[TestClient, MagicMock]) -> None:
        test_client, db = client
        token = make_open_token(WS, MSG, KEY)
        response = test_client.head(f"/api/v1/t/o/{token}.gif")
        assert response.status_code == 200
        db.execute.assert_not_called()

    def test_a_database_failure_still_serves_the_image(
        self, client: tuple[TestClient, MagicMock]
    ) -> None:
        from sqlalchemy.exc import OperationalError

        test_client, db = client
        db.execute.side_effect = OperationalError("stmt", {}, Exception("db down"))
        token = make_open_token(WS, MSG, KEY)
        response = test_client.get(f"/api/v1/t/o/{token}.gif")
        assert response.status_code == 200 and response.content == TRANSPARENT_GIF
        db.rollback.assert_called()

    def test_the_response_reveals_nothing_about_the_token(
        self, client: tuple[TestClient, MagicMock]
    ) -> None:
        test_client, _ = client
        good = test_client.get(f"/api/v1/t/o/{make_open_token(WS, MSG, KEY)}.gif")
        bad = test_client.get("/api/v1/t/o/nope.gif")
        assert good.content == bad.content and dict(good.headers).keys() == dict(bad.headers).keys()
