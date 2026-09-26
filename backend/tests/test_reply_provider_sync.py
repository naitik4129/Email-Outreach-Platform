from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.core.errors import AppError
from app.modules.mailboxes.providers.base import (
    ProviderCapability,
    UnsupportedCapabilityError,
)
from app.modules.mailboxes.providers.gmail import GmailProvider
from app.modules.mailboxes.providers.microsoft import MicrosoftGraphProvider
from app.modules.mailboxes.providers.smtp import SmtpProvider


class TestGmailReplySyncProvider:
    def test_gmail_capabilities_include_reply_sync(self) -> None:
        provider = GmailProvider()
        assert ProviderCapability.REPLY_SYNC in provider.capabilities

    @patch("httpx.Client.get")
    def test_gmail_initial_sync_without_cursor(self, mock_get: MagicMock) -> None:
        provider = GmailProvider()
        cred = {"access_token": "ya29.test"}

        # Call 1: profile fetch (to capture initial historyId)
        resp_prof = MagicMock()
        resp_prof.status_code = 200
        resp_prof.json.return_value = {"historyId": "99999"}

        # Call 2: messages.list (page 1)
        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {
            "messages": [{"id": "msg_001", "threadId": "th_001"}],
            "nextPageToken": "token_page_2",
            "resultSizeEstimate": 1,
        }

        # Call 3: message detail fetch
        resp_detail = MagicMock()
        resp_detail.status_code = 200
        resp_detail.json.return_value = {
            "id": "msg_001",
            "threadId": "th_001",
            "historyId": "99999",
            "internalDate": "1710000000000",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<inbound1@prospect.com>"},
                    {"name": "In-Reply-To", "value": "<outbound1@platform.com>"},
                    {"name": "References", "value": "<outbound1@platform.com>"},
                    {"name": "From", "value": "Prospect <lead@prospect.com>"},
                    {"name": "To", "value": "rep@outreach.com"},
                    {"name": "Subject", "value": "Re: Partnership"},
                ],
                "mimeType": "text/plain",
                "body": {"data": "SSBhbSBpbnRlcmVzdGVkLCBsZXQncyB0YWxr"},  # "I am interested, let's talk" in base64url
            },
        }

        mock_get.side_effect = [resp_prof, resp1, resp_detail]

        result = provider.sync_inbound_messages(credential=cred, cursor=None, page_size=10)

        assert len(result.messages) == 1
        msg = result.messages[0]
        assert msg.provider_message_id == "msg_001"
        assert msg.provider_thread_id == "th_001"
        assert msg.rfc_message_id == "<inbound1@prospect.com>"
        assert msg.in_reply_to == "<outbound1@platform.com>"
        assert msg.from_address == "lead@prospect.com"
        assert msg.from_name == "Prospect"
        assert msg.subject == "Re: Partnership"
        assert result.has_more is True
        assert result.next_page_token == "token_page_2"

    @patch("httpx.Client.get")
    def test_gmail_incremental_sync_with_history_id(self, mock_get: MagicMock) -> None:
        provider = GmailProvider()
        cred = {"access_token": "ya29.test"}

        # Call: history.list
        resp_hist = MagicMock()
        resp_hist.status_code = 200
        resp_hist.json.return_value = {
            "history": [
                {
                    "id": "100001",
                    "messagesAdded": [
                        {"message": {"id": "msg_002", "threadId": "th_002"}}
                    ],
                }
            ],
            "historyId": "100005",
        }

        # Message detail
        resp_detail = MagicMock()
        resp_detail.status_code = 200
        resp_detail.json.return_value = {
            "id": "msg_002",
            "threadId": "th_002",
            "historyId": "100005",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<inbound2@prospect.com>"},
                    {"name": "From", "value": "lead@prospect.com"},
                    {"name": "Subject", "value": "Re: Follow up"},
                ],
                "mimeType": "text/plain",
                "body": {"data": "SGVsbG8="},  # "Hello"
            },
        }

        mock_get.side_effect = [resp_hist, resp_detail]

        result = provider.sync_inbound_messages(
            credential=cred, cursor="100000", page_size=10
        )

        assert len(result.messages) == 1
        assert result.messages[0].provider_message_id == "msg_002"
        assert result.next_cursor == "100005"
        assert result.has_more is False
        assert result.resync_required is False

    @patch("httpx.Client.get")
    def test_gmail_expired_history_triggers_resync(self, mock_get: MagicMock) -> None:
        provider = GmailProvider()
        cred = {"access_token": "ya29.test"}

        # 404 response on history.list indicates historyId is too old (expired)
        resp_expired = MagicMock()
        resp_expired.status_code = 404
        resp_expired.text = "History ID too old"
        mock_get.return_value = resp_expired

        result = provider.sync_inbound_messages(
            credential=cred, cursor="12345", page_size=10
        )

        assert result.resync_required is True
        assert len(result.messages) == 0


class TestMicrosoftGraphReplySyncProvider:
    def test_microsoft_capabilities_include_reply_sync(self) -> None:
        provider = MicrosoftGraphProvider()
        assert ProviderCapability.REPLY_SYNC in provider.capabilities

    @patch("httpx.Client.get")
    def test_microsoft_delta_sync_pagination(self, mock_get: MagicMock) -> None:
        provider = MicrosoftGraphProvider()
        cred = {"access_token": "ms_test_token"}

        # Page 1: contains message and @odata.nextLink
        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {
            "value": [
                {
                    "id": "graph_msg_001",
                    "conversationId": "graph_th_001",
                    "internetMessageId": "<graph1@company.com>",
                    "subject": "Re: Partnership proposal",
                    "from": {"emailAddress": {"address": "lead@company.com", "name": "Lead"}},
                    "toRecipients": [{"emailAddress": {"address": "rep@outreach.com"}}],
                    "body": {"contentType": "text", "content": "Sounds great!"},
                    "receivedDateTime": "2026-03-24T10:00:00Z",
                    "singleValueExtendedProperties": [
                        {
                            "id": "String 0x1042",
                            "value": "<outbound1@platform.com>",
                        }
                    ],
                }
            ],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages/delta?$skiptoken=skip123",
        }
        mock_get.return_value = resp1

        result = provider.sync_inbound_messages(credential=cred, cursor=None, page_size=10)

        assert len(result.messages) == 1
        msg = result.messages[0]
        assert msg.provider_message_id == "graph_msg_001"
        assert msg.provider_thread_id == "graph_th_001"
        assert msg.rfc_message_id == "<graph1@company.com>"
        assert msg.in_reply_to == "<outbound1@platform.com>"
        assert msg.from_address == "lead@company.com"
        assert result.has_more is True
        assert "skip123" in (result.next_cursor or "")

    @patch("httpx.Client.get")
    def test_microsoft_expired_delta_triggers_resync(self, mock_get: MagicMock) -> None:
        provider = MicrosoftGraphProvider()
        cred = {"access_token": "ms_test_token"}

        # 410 Gone indicates delta token is expired
        resp_410 = MagicMock()
        resp_410.status_code = 410
        resp_410.text = "Delta token expired"
        mock_get.return_value = resp_410

        result = provider.sync_inbound_messages(
            credential=cred,
            cursor="https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages/delta?$deltatoken=old123",
        )

        assert result.resync_required is True
        assert len(result.messages) == 0

    def test_microsoft_ssrf_rejection_on_untrusted_continuation_url(self) -> None:
        provider = MicrosoftGraphProvider()
        cred = {"access_token": "ms_test_token"}

        # Attempt to pass an SSRF continuation URL
        with pytest.raises(AppError) as exc_info:
            provider.sync_inbound_messages(
                credential=cred,
                cursor="https://malicious-attacker.com/steal-token",
            )
        assert exc_info.value.code == "ssrf_rejected"


class TestSmtpProviderRejection:
    def test_smtp_without_imap_settings_cannot_sync_replies(self) -> None:
        """SMTP alone can never receive mail: without IMAP settings the sync
        refuses instead of pretending to work."""
        provider = SmtpProvider()

        with pytest.raises(UnsupportedCapabilityError):
            provider.sync_inbound_messages(credential={}, cursor=None)
