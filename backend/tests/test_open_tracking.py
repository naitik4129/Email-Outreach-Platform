"""Open tracking: signed tokens, pixel injection and the public endpoint."""

# ruff: noqa: E501 -- test data (raw MIME, SQL, header values) reads better unwrapped.

from __future__ import annotations

import base64
import re
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.config import Settings
from app.main import app
from app.modules.mailboxes.providers.message_builder import generate_message_id
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
        assert parse_open_token(token, KEY) == (WS, MSG)

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
        assert parse_open_token(token, KEY) == (WS, MSG)


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
