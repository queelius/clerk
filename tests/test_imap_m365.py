"""Tests for Microsoft 365 IMAP connection."""

from unittest.mock import MagicMock, patch

import pytest

from clerk.config import AccountConfig, FromAddress
from clerk.imap_client import ImapClient


class TestImapM365Connection:
    def _make_m365_config(self) -> AccountConfig:
        return AccountConfig(
            protocol="microsoft365",
            **{"from": FromAddress(address="user@siue.edu", name="Test User")},
        )

    @patch("clerk.imap_client.IMAPClient")
    @patch("clerk.microsoft365.get_m365_access_token")
    def test_connect_microsoft365(self, mock_get_token, mock_imap_cls):
        """Test connecting to M365 via XOAUTH2."""
        mock_get_token.return_value = "m365_access_token"
        mock_client = MagicMock()
        mock_imap_cls.return_value = mock_client

        config = self._make_m365_config()
        client = ImapClient("siue", config)
        client.connect()

        mock_imap_cls.assert_called_once_with(
            "outlook.office365.com", port=993, ssl=True
        )
        mock_client.oauth2_login.assert_called_once_with(
            "user@siue.edu", "m365_access_token"
        )

    @patch("clerk.imap_client.IMAPClient")
    @patch("clerk.microsoft365.get_m365_access_token")
    def test_connect_microsoft365_token_error(self, mock_get_token, mock_imap_cls):
        """Test error handling when token acquisition fails."""
        mock_get_token.side_effect = ValueError("No valid credentials")

        config = self._make_m365_config()
        client = ImapClient("siue", config)

        with pytest.raises(ValueError, match="No valid credentials"):
            client.connect()
