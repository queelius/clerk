"""Tests for Microsoft 365 SMTP sending."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clerk.config import AccountConfig, FromAddress
from clerk.models import Address, Draft
from clerk.smtp_client import SmtpClient


class TestSmtpM365:
    def _make_m365_config(self) -> AccountConfig:
        return AccountConfig(
            protocol="microsoft365",
            **{"from": FromAddress(address="user@siue.edu", name="Test User")},
        )

    def _make_draft(self) -> Draft:
        return Draft(
            draft_id="draft_m365_test",
            account="siue",
            to=[Address(addr="recipient@example.com", name="Recipient")],
            subject="Test Subject",
            body_text="Test body",
        )

    @patch("clerk.smtp_client.aiosmtplib.SMTP")
    @patch("clerk.microsoft365.get_m365_access_token")
    @pytest.mark.asyncio
    async def test_send_microsoft365(self, mock_get_token, mock_smtp_cls):
        """Test sending via M365 SMTP with XOAUTH2."""
        mock_get_token.return_value = "m365_token"

        mock_smtp = AsyncMock()
        mock_smtp_cls.return_value = mock_smtp

        # Simulate successful AUTH response (235 = auth successful)
        mock_response = MagicMock()
        mock_response.code = 235
        mock_smtp.execute_command.return_value = mock_response

        config = self._make_m365_config()
        client = SmtpClient("siue", config)
        draft = self._make_draft()

        result = await client._send_async(draft)

        assert result.success
        mock_smtp_cls.assert_called_once_with(
            hostname="smtp.office365.com", port=587, start_tls=True
        )
        mock_smtp.connect.assert_called_once()
        mock_smtp.ehlo.assert_called_once()
        mock_smtp.starttls.assert_called_once()
        mock_smtp.execute_command.assert_called_once()
        # Verify the AUTH command uses XOAUTH2
        args = mock_smtp.execute_command.call_args[0]
        assert args[0] == b"AUTH"
        assert args[1] == b"XOAUTH2"
        mock_smtp.send_message.assert_called_once()
        mock_smtp.quit.assert_called_once()

    @patch("clerk.smtp_client.aiosmtplib.SMTP")
    @patch("clerk.microsoft365.get_m365_access_token")
    @pytest.mark.asyncio
    async def test_send_microsoft365_auth_error(self, mock_get_token, mock_smtp_cls):
        """Test handling of M365 SMTP auth failure."""
        mock_get_token.return_value = "bad_token"
        mock_smtp = AsyncMock()
        mock_smtp_cls.return_value = mock_smtp

        # Simulate failed AUTH response (535 = auth credentials invalid)
        mock_response = MagicMock()
        mock_response.code = 535
        mock_response.message = b"5.7.3 Authentication unsuccessful"
        mock_smtp.execute_command.return_value = mock_response

        config = self._make_m365_config()
        client = SmtpClient("siue", config)
        draft = self._make_draft()

        result = await client._send_async(draft)

        assert not result.success
        assert "Authentication" in result.error
