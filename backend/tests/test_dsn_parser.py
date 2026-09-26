"""Bounce (delivery-status notification) detection and parsing."""

# ruff: noqa: E501 -- test data (raw MIME, SQL, header values) reads better unwrapped.

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.events.adapters.generic import _classify_bounce
from app.modules.events.schemas import BounceClassification
from app.modules.mailboxes.providers.base import ProviderInboundMessage
from app.modules.mailboxes.providers.message_builder import (
    normalize_message_id,
    sql_normalized_message_id,
)
from app.modules.mailboxes.providers.mime_report import (
    extract_report_text,
    is_delivery_report,
    parse_mime_bytes,
)
from app.modules.replies.dsn import parse_delivery_report
from app.modules.replies.normalizer import normalize_inbound_message
from app.modules.replies.schemas import InboundClassification

GMAIL_DSN_HEADERS = {"content-type": "multipart/report; report-type=delivery-status"}

REPORT_5_1_1 = (
    "Final-Recipient: rfc822; jane@target.test\n"
    "Action: failed\nStatus: 5.1.1\n"
    "Diagnostic-Code: smtp; 550 5.1.1 The email account does not exist\n"
    "Message-ID: <sent-1@acme.test>\nIn-Reply-To: <earlier@acme.test>"
)


def parse(**overrides):
    args = {
        "from_address": "mailer-daemon@googlemail.com",
        "subject": "Delivery Status Notification (Failure)",
        "headers": GMAIL_DSN_HEADERS,
        "content_text": "Your message wasn't delivered",
        "report_text": REPORT_5_1_1,
    }
    args.update(overrides)
    return parse_delivery_report(**args)


class TestParseDeliveryReport:
    def test_hard_bounce_with_original_message_id(self) -> None:
        report = parse()
        assert report is not None
        assert report.bounce_type == "HARD"
        assert report.status_code == "5.1.1"
        assert report.failed_recipient == "jane@target.test"
        assert report.original_message_ids == ["<sent-1@acme.test>"]
        # In-Reply-To of the returned headers is a weaker, separate signal.
        assert report.referenced_message_ids == ["<earlier@acme.test>"]
        assert "does not exist" in (report.diagnostic or "")

    def test_soft_bounce_4xx(self) -> None:
        report = parse(report_text="Final-Recipient: rfc822; a@b.co\nAction: delayed\nStatus: 4.4.7")
        assert report is not None and report.bounce_type == "SOFT"

    def test_mailbox_full_is_soft_even_though_reported_5_2_2(self) -> None:
        report = parse(report_text="Final-Recipient: rfc822; a@b.co\nAction: failed\nStatus: 5.2.2")
        assert report is not None and report.bounce_type == "SOFT"

    @pytest.mark.parametrize("status", ["5.4.1", "5.7.1", "5.1.10"])
    def test_other_5xx_codes_are_hard_not_misread_by_substring(self, status: str) -> None:
        report = parse(report_text=f"Final-Recipient: rfc822; a@b.co\nAction: failed\nStatus: {status}")
        assert report is not None and report.bounce_type == "HARD"

    def test_action_alone_decides_when_there_is_no_status_code(self) -> None:
        assert parse(report_text="Final-Recipient: rfc822; a@b.co\nAction: failed").bounce_type == "HARD"  # type: ignore[union-attr]
        assert parse(report_text="Final-Recipient: rfc822; a@b.co\nAction: delayed").bounce_type == "SOFT"  # type: ignore[union-attr]

    def test_unknown_when_the_notification_says_nothing_useful(self) -> None:
        report = parse(report_text="Final-Recipient: rfc822; a@b.co", content_text="Something happened")
        assert report is not None and report.bounce_type == "UNKNOWN"

    def test_a_delivery_receipt_is_not_a_bounce(self) -> None:
        assert parse(report_text="Final-Recipient: rfc822; a@b.co\nAction: delivered\nStatus: 2.0.0") is None
        assert parse(report_text="Final-Recipient: rfc822; a@b.co\nAction: relayed") is None

    def test_exchange_ndr_with_everything_only_in_the_body(self) -> None:
        body = (
            "Delivery has failed to these recipients or groups:\njane@target.test\n"
            "Remote Server returned '550 5.1.1 RESOLVER.ADR.RecipNotFound; not found'\n"
            "Original message headers:\nMessage-ID: <sent-1@acme.test>\nSubject: Quick idea"
        )
        report = parse(
            from_address="postmaster@target.test",
            subject="Undeliverable: Quick idea",
            headers={"x-ms-exchange-message-is-ndr": ""},
            content_text=body,
            report_text=None,
        )
        assert report is not None
        assert report.bounce_type == "HARD" and report.status_code == "5.1.1"
        assert report.original_message_ids == ["<sent-1@acme.test>"]

    def test_x_failed_recipients_header_names_the_recipient(self) -> None:
        report = parse(
            headers={**GMAIL_DSN_HEADERS, "x-failed-recipients": "Jane <jane@target.test>"},
            report_text="Action: failed\nStatus: 5.1.1",
        )
        assert report is not None and report.failed_recipient == "jane@target.test"

    @pytest.mark.parametrize(
        ("sender", "subject", "headers"),
        [
            ("jane@target.test", "Re: Quick idea", {}),
            ("jane@target.test", "Undeliverable: my own subject line", {}),
            ("jane@target.test", "Out of office", {"auto-submitted": "auto-replied"}),
        ],
    )
    def test_ordinary_mail_is_not_a_report(self, sender: str, subject: str, headers: dict) -> None:
        assert parse(from_address=sender, subject=subject, headers=headers, report_text=None) is None


class TestNormalizerClassification:
    def message(self, **overrides) -> ProviderInboundMessage:
        base = {
            "provider_message_id": "dsn-1",
            "from_address": "mailer-daemon@googlemail.com",
            "to_addresses": ["sender@acme.test"],
            "subject": "Delivery Status Notification (Failure)",
            "body_text": "Address not found",
            "received_at": datetime.now(UTC),
            "headers": GMAIL_DSN_HEADERS,
            "report_text": REPORT_5_1_1,
        }
        base.update(overrides)
        return ProviderInboundMessage(**base)

    def test_a_dsn_is_classified_bounce_never_a_human_reply(self) -> None:
        normalized = normalize_inbound_message(self.message(), provider="GMAIL")
        assert normalized.classification == InboundClassification.BOUNCE.value
        assert normalized.delivery_report is not None and normalized.is_automated

    def test_a_real_reply_is_unaffected(self) -> None:
        normalized = normalize_inbound_message(
            self.message(from_address="jane@target.test", subject="Re: Quick idea", headers={}, report_text=None),
            provider="GMAIL",
        )
        assert normalized.classification == InboundClassification.HUMAN_REPLY.value
        assert normalized.delivery_report is None

    def test_out_of_office_stays_out_of_office(self) -> None:
        normalized = normalize_inbound_message(
            self.message(
                from_address="jane@target.test",
                subject="Automatic reply: Out of office",
                headers={"auto-submitted": "auto-replied"},
                report_text=None,
            ),
            provider="GMAIL",
        )
        assert normalized.classification == InboundClassification.OUT_OF_OFFICE.value


class TestMimeReport:
    RAW = (
        b"From: MAILER-DAEMON@googlemail.com\r\nTo: sender@acme.test\r\nSubject: Delivery Status Notification (Failure)\r\n"
        b"MIME-Version: 1.0\r\nContent-Type: multipart/report; report-type=delivery-status; boundary=B\r\n\r\n"
        b"--B\r\nContent-Type: text/plain\r\n\r\nYour message was not delivered\r\n"
        b"--B\r\nContent-Type: message/delivery-status\r\n\r\n"
        b"Reporting-MTA: dns; googlemail.com\r\n\r\n"
        b"Final-Recipient: rfc822; jane@target.test\r\nAction: failed\r\nStatus: 5.1.1\r\n"
        b"Diagnostic-Code: smtp; 550 5.1.1 no such user\r\n\r\n"
        b"--B\r\nContent-Type: message/rfc822\r\n\r\n"
        b"Message-ID: <sent-1@acme.test>\r\nSubject: Quick idea\r\n\r\nbody\r\n--B--\r\n"
    )

    def test_extracts_status_fields_and_the_returned_message_id(self) -> None:
        msg = parse_mime_bytes(self.RAW)
        assert is_delivery_report(msg)
        report = extract_report_text(msg)
        assert report is not None
        parsed = parse_delivery_report(
            from_address="mailer-daemon@googlemail.com",
            subject="Delivery Status Notification (Failure)",
            headers=GMAIL_DSN_HEADERS,
            content_text="Your message was not delivered",
            report_text=report,
        )
        assert parsed is not None
        assert parsed.bounce_type == "HARD" and parsed.failed_recipient == "jane@target.test"
        assert parsed.original_message_ids == ["<sent-1@acme.test>"]

    def test_a_normal_message_is_not_a_report(self) -> None:
        assert not is_delivery_report(parse_mime_bytes(b"From: a@b.co\r\nSubject: hi\r\n\r\nhello"))

    def test_the_report_text_is_bounded(self) -> None:
        big = self.RAW.replace(b"no such user", b"x" * 100_000)
        assert len(extract_report_text(parse_mime_bytes(big)) or "") <= 20_000


class TestWebhookClassifier:
    @pytest.mark.parametrize(
        ("explicit", "reason", "expected"),
        [
            ("HARD", "", BounceClassification.HARD),
            ("permanent", "", BounceClassification.HARD),
            ("SOFT", "5.1.1 user unknown", BounceClassification.SOFT),
            ("", "550 5.1.1 User unknown", BounceClassification.HARD),
            # 5.4.x contains the substring "4." -- it is a permanent failure.
            ("", "554 5.4.6 routing loop", BounceClassification.HARD),
            ("", "452 4.2.2 over quota", BounceClassification.SOFT),
            ("", "552 5.2.2 mailbox full", BounceClassification.SOFT),
            ("", "user does not exist", BounceClassification.HARD),
            ("", "try again later", BounceClassification.SOFT),
            ("", "", BounceClassification.UNKNOWN),
        ],
    )
    def test_classification(self, explicit: str, reason: str, expected: BounceClassification) -> None:
        assert _classify_bounce(explicit.upper(), reason) == expected


class TestMessageIdNormalization:
    @pytest.mark.parametrize("value", ["<Abc@Example.COM>", "abc@example.com", " <abc@example.com> "])
    def test_forms_compare_equal(self, value: str) -> None:
        assert normalize_message_id(value) == "abc@example.com"

    def test_empty_values(self) -> None:
        assert normalize_message_id(None) is None and normalize_message_id("<>") is None

    def test_sql_expression_per_dialect(self) -> None:
        assert sql_normalized_message_id("m.x", sqlite=True) == "LOWER(TRIM(m.x, '<> '))"
        assert sql_normalized_message_id("m.x", sqlite=False) == "LOWER(BTRIM(m.x, '<> '))"
