"""Tests for OAuth module."""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from clerk.oauth import (
    GMAIL_SCOPES,
    _load_credentials,
    _save_credentials,
    get_oauth2_string,
    revoke_credentials,
)


class TestGetOAuth2String:
    def test_format(self):
        """Test XOAUTH2 string format."""
        email = "user@gmail.com"
        token = "ya29.test-access-token"

        result = get_oauth2_string(email, token)

        # Decode and verify format
        decoded = base64.b64decode(result).decode()
        assert decoded == f"user={email}\x01auth=Bearer {token}\x01\x01"

    def test_base64_encoding(self):
        """Test that result is valid base64."""
        result = get_oauth2_string("test@example.com", "token123")

        # Should not raise
        decoded = base64.b64decode(result)
        assert b"test@example.com" in decoded
        assert b"token123" in decoded


class TestCredentialsSerialization:
    @patch("clerk.oauth.save_oauth_token")
    def test_save_credentials(self, mock_save):
        """Test saving credentials to keyring."""
        mock_creds = MagicMock()
        mock_creds.token = "access_token_123"
        mock_creds.refresh_token = "refresh_token_456"
        mock_creds.token_uri = "https://oauth2.googleapis.com/token"
        mock_creds.client_id = "client_id.apps.googleusercontent.com"
        mock_creds.client_secret = "secret123"
        mock_creds.scopes = ["https://mail.google.com/"]

        _save_credentials("test-account", mock_creds)

        mock_save.assert_called_once()
        account_name, token_json = mock_save.call_args[0]
        assert account_name == "test-account"

        # Verify JSON structure
        data = json.loads(token_json)
        assert data["token"] == "access_token_123"
        assert data["refresh_token"] == "refresh_token_456"
        assert data["client_id"] == "client_id.apps.googleusercontent.com"

    def test_load_credentials(self):
        """Test loading credentials from JSON."""
        token_data = {
            "token": "access_token",
            "refresh_token": "refresh_token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client_id",
            "client_secret": "secret",
            "scopes": ["https://mail.google.com/"],
        }
        token_json = json.dumps(token_data)

        creds = _load_credentials(token_json)

        assert creds.token == "access_token"
        assert creds.refresh_token == "refresh_token"
        assert creds.client_id == "client_id"
        assert creds.client_secret == "secret"

    def test_load_credentials_defaults(self):
        """Test loading credentials with missing optional fields."""
        token_data = {
            "token": "access_token",
            "refresh_token": "refresh_token",
        }
        token_json = json.dumps(token_data)

        creds = _load_credentials(token_json)

        assert creds.token == "access_token"
        assert creds.token_uri == "https://oauth2.googleapis.com/token"
        assert list(creds.scopes) == GMAIL_SCOPES


class TestRevokeCredentials:
    @patch("clerk.oauth.delete_oauth_token")
    def test_revoke(self, mock_delete):
        """Test revoking credentials."""
        revoke_credentials("test-account")
        mock_delete.assert_called_once_with("test-account")


class TestGetGmailCredentials:
    @patch("clerk.oauth.get_oauth_token")
    def test_valid_cached_credentials(self, mock_get_token):
        """Test returning valid cached credentials."""
        from clerk.oauth import get_gmail_credentials

        token_data = {
            "token": "valid_token",
            "refresh_token": "refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client_id",
            "client_secret": "secret",
            "scopes": ["https://mail.google.com/"],
        }
        mock_get_token.return_value = json.dumps(token_data)

        # Mock the credentials to be valid
        with patch("clerk.oauth._load_credentials") as mock_load:
            mock_creds = MagicMock()
            mock_creds.valid = True
            mock_load.return_value = mock_creds

            result = get_gmail_credentials("test-account")

            assert result == mock_creds

    @patch("clerk.oauth.get_oauth_token")
    def test_no_credentials_no_client_file(self, mock_get_token):
        """Test error when no credentials and no client file."""
        from clerk.oauth import get_gmail_credentials

        mock_get_token.return_value = None

        with pytest.raises(ValueError, match="No valid credentials"):
            get_gmail_credentials("test-account")

    @patch("clerk.oauth.get_oauth_token")
    @patch("clerk.oauth.run_oauth_flow")
    def test_no_credentials_with_client_file(self, mock_flow, mock_get_token, tmp_path):
        """Test running OAuth flow when no credentials exist."""
        from clerk.oauth import get_gmail_credentials

        mock_get_token.return_value = None
        mock_creds = MagicMock()
        mock_flow.return_value = mock_creds

        client_file = tmp_path / "client_id.json"
        client_file.write_text("{}")

        result = get_gmail_credentials("test-account", client_id_file=client_file)

        mock_flow.assert_called_once_with(client_file, "test-account")
        assert result == mock_creds


class TestRunOAuthFlow:
    def test_missing_client_file(self, tmp_path):
        """Test error when client ID file doesn't exist."""
        from clerk.oauth import run_oauth_flow

        missing_file = tmp_path / "missing.json"

        with pytest.raises(FileNotFoundError, match="OAuth client ID file not found"):
            run_oauth_flow(missing_file, "test-account")
