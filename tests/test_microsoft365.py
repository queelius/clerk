"""Tests for Microsoft 365 OAuth module."""

from unittest.mock import MagicMock, patch

import pytest


class TestM365Constants:
    def test_scopes_include_imap(self):
        from clerk.microsoft365 import M365_SCOPES
        assert "https://outlook.office.com/IMAP.AccessAsUser.All" in M365_SCOPES

    def test_scopes_include_smtp(self):
        from clerk.microsoft365 import M365_SCOPES
        assert "https://outlook.office.com/SMTP.Send" in M365_SCOPES

    def test_scopes_exclude_reserved(self):
        """MSAL adds offline_access automatically; we must not include reserved scopes."""
        from clerk.microsoft365 import M365_SCOPES
        assert "offline_access" not in M365_SCOPES
        assert "openid" not in M365_SCOPES
        assert "profile" not in M365_SCOPES

    def test_authority_is_common(self):
        from clerk.microsoft365 import M365_AUTHORITY
        assert "common" in M365_AUTHORITY

    def test_client_id_is_set(self):
        from clerk.microsoft365 import M365_CLIENT_ID
        assert M365_CLIENT_ID  # non-empty


class TestGetM365AccessToken:
    @patch("clerk.microsoft365.get_m365_token_cache")
    @patch("clerk.microsoft365.save_m365_token_cache")
    @patch("clerk.microsoft365.msal.PublicClientApplication")
    def test_acquire_token_silent_success(self, mock_app_cls, mock_save, mock_get_cache):
        from clerk.microsoft365 import get_m365_access_token
        mock_get_cache.return_value = '{"cached": true}'
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@siue.edu"}]
        mock_app.acquire_token_silent.return_value = {"access_token": "fresh_token_123"}
        mock_cache = MagicMock()
        mock_cache.has_state_changed = True
        mock_cache.serialize.return_value = '{"updated": true}'
        mock_app.token_cache = mock_cache

        result = get_m365_access_token("test-account")
        assert result == "fresh_token_123"
        mock_app.acquire_token_silent.assert_called_once()
        mock_save.assert_called_once()

    @patch("clerk.microsoft365.get_m365_token_cache")
    @patch("clerk.microsoft365.msal.PublicClientApplication")
    def test_no_cache_no_accounts_raises(self, mock_app_cls, mock_get_cache):
        from clerk.microsoft365 import get_m365_access_token
        mock_get_cache.return_value = None
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = []
        with pytest.raises(ValueError, match="No valid credentials"):
            get_m365_access_token("test-account")

    @patch("clerk.microsoft365.get_m365_token_cache")
    @patch("clerk.microsoft365.msal.PublicClientApplication")
    def test_silent_acquire_fails_raises(self, mock_app_cls, mock_get_cache):
        from clerk.microsoft365 import get_m365_access_token
        mock_get_cache.return_value = '{"cached": true}'
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@siue.edu"}]
        mock_app.acquire_token_silent.return_value = None
        with pytest.raises(ValueError, match="No valid credentials"):
            get_m365_access_token("test-account")


class TestRunM365DeviceCodeFlow:
    @patch("clerk.microsoft365.save_m365_token_cache")
    @patch("clerk.microsoft365.msal.PublicClientApplication")
    def test_successful_flow(self, mock_app_cls, mock_save):
        from clerk.microsoft365 import run_m365_device_code_flow
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.initiate_device_flow.return_value = {
            "user_code": "ABCD-EFGH",
            "message": "Go to https://microsoft.com/devicelogin and enter code ABCD-EFGH",
        }
        mock_app.acquire_token_by_device_flow.return_value = {"access_token": "new_token"}
        mock_cache = MagicMock()
        mock_app.token_cache = mock_cache
        mock_cache.serialize.return_value = '{"serialized": true}'

        run_m365_device_code_flow("test-account")
        mock_app.initiate_device_flow.assert_called_once()
        mock_app.acquire_token_by_device_flow.assert_called_once()
        mock_save.assert_called_once_with("test-account", '{"serialized": true}')

    @patch("clerk.microsoft365.msal.PublicClientApplication")
    def test_flow_initiation_failure(self, mock_app_cls):
        from clerk.microsoft365 import run_m365_device_code_flow
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.initiate_device_flow.return_value = {
            "error": "authorization_pending",
            "error_description": "Something went wrong",
        }
        with pytest.raises(ValueError, match="device code flow"):
            run_m365_device_code_flow("test-account")

    @patch("clerk.microsoft365.msal.PublicClientApplication")
    def test_token_acquisition_failure(self, mock_app_cls):
        from clerk.microsoft365 import run_m365_device_code_flow
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.initiate_device_flow.return_value = {
            "user_code": "ABCD-EFGH",
            "message": "Go to device login",
        }
        mock_app.acquire_token_by_device_flow.return_value = {
            "error": "authorization_declined",
            "error_description": "User declined",
        }
        with pytest.raises(ValueError, match="User declined"):
            run_m365_device_code_flow("test-account")


class TestRevokeM365Credentials:
    @patch("clerk.microsoft365.delete_m365_token_cache")
    def test_revoke(self, mock_delete):
        from clerk.microsoft365 import revoke_m365_credentials
        revoke_m365_credentials("test-account")
        mock_delete.assert_called_once_with("test-account")
