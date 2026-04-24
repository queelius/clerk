"""Tests for clerk CLI."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from clerk.cli import app
from clerk.models import CacheStats

runner = CliRunner()


class TestVersion:
    def test_version_command(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "clerk" in result.stdout


class TestStatus:
    @patch("clerk.cli.get_config")
    def test_status_no_accounts(self, mock_config):
        from clerk.config import ClerkConfig

        mock_config.return_value = ClerkConfig()

        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0


class TestAccounts:
    @patch("clerk.cli.get_config")
    def test_accounts_empty(self, mock_config):
        from clerk.config import ClerkConfig

        mock_config.return_value = ClerkConfig()

        result = runner.invoke(app, ["accounts"])
        assert result.exit_code == 0
        assert "No accounts configured" in result.stdout

    @patch("clerk.cli.get_config")
    def test_accounts_json(self, mock_config):
        from clerk.config import (
            AccountConfig,
            ClerkConfig,
            FromAddress,
            ImapConfig,
            SmtpConfig,
        )

        mock_config.return_value = ClerkConfig(
            default_account="test",
            accounts={
                "test": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.ex.com", username="user"),
                    smtp=SmtpConfig(host="smtp.ex.com", username="user"),
                    **{"from": FromAddress(address="user@example.com")},
                ),
            },
        )

        result = runner.invoke(app, ["accounts", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) == 1
        assert data[0]["name"] == "test"
        assert data[0]["email"] == "user@example.com"


class TestCacheCommands:
    @patch("clerk.cli.get_api")
    def test_cache_status(self, mock_get_api):
        mock_api = MagicMock()
        mock_api.get_cache_stats.return_value = CacheStats(
            message_count=100,
            conversation_count=25,
            oldest_message=datetime(2025, 1, 1),
            newest_message=datetime(2025, 1, 3),
            cache_size_bytes=1024 * 1024,
            last_sync=datetime(2025, 1, 3, 12, 0),
        )
        mock_get_api.return_value = mock_api

        result = runner.invoke(app, ["cache", "status"])
        assert result.exit_code == 0
        assert "100" in result.stdout
        assert "25" in result.stdout

    @patch("clerk.cli.get_api")
    def test_cache_status_json(self, mock_get_api):
        mock_api = MagicMock()
        mock_api.get_cache_stats.return_value = CacheStats(
            message_count=50,
            conversation_count=10,
            oldest_message=None,
            newest_message=None,
            cache_size_bytes=512,
            last_sync=None,
        )
        mock_get_api.return_value = mock_api

        result = runner.invoke(app, ["cache", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["message_count"] == 50

    @patch("clerk.cli.get_api")
    def test_cache_clear_cancelled(self, mock_get_api):
        mock_api = MagicMock()
        mock_get_api.return_value = mock_api

        # Simulate user typing "n" to cancel
        runner.invoke(app, ["cache", "clear"], input="n\n")
        # When user cancels, confirm() aborts before clear is called.
        mock_api.clear_cache.assert_not_called()


class TestAccountsCommands:
    @patch("clerk.cli.get_config")
    def test_accounts_list_empty(self, mock_config):
        from clerk.config import ClerkConfig

        mock_config.return_value = ClerkConfig()

        result = runner.invoke(app, ["accounts"])
        assert result.exit_code == 0
        assert "No accounts configured" in result.stdout

    @patch("clerk.cli.get_config")
    def test_accounts_list_with_accounts(self, mock_config):
        from clerk.config import (
            AccountConfig,
            ClerkConfig,
            FromAddress,
            ImapConfig,
            SmtpConfig,
        )

        mock_config.return_value = ClerkConfig(
            default_account="personal",
            accounts={
                "personal": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.ex.com", username="user"),
                    smtp=SmtpConfig(host="smtp.ex.com", username="user"),
                    **{"from": FromAddress(address="user@example.com")},
                ),
                "work": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.work.com", username="worker"),
                    smtp=SmtpConfig(host="smtp.work.com", username="worker"),
                    **{"from": FromAddress(address="worker@work.com")},
                ),
            },
        )

        result = runner.invoke(app, ["accounts"])
        assert result.exit_code == 0
        assert "personal" in result.stdout
        assert "work" in result.stdout
        assert "default" in result.stdout  # personal is marked as default

    @patch("clerk.cli.get_config")
    def test_accounts_list_json(self, mock_config):
        from clerk.config import (
            AccountConfig,
            ClerkConfig,
            FromAddress,
            ImapConfig,
            SmtpConfig,
        )

        mock_config.return_value = ClerkConfig(
            default_account="test",
            accounts={
                "test": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.ex.com", username="user"),
                    smtp=SmtpConfig(host="smtp.ex.com", username="user"),
                    **{"from": FromAddress(address="user@example.com")},
                ),
            },
        )

        result = runner.invoke(app, ["accounts", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) == 1
        assert data[0]["name"] == "test"
        assert data[0]["default"] is True

    @patch("clerk.cli.save_config")
    @patch("clerk.cli.save_password")
    @patch("clerk.cli.load_config")
    def test_accounts_add_imap(self, mock_load, mock_save_pass, mock_save_config):
        from clerk.config import ClerkConfig

        mock_load.return_value = ClerkConfig()

        result = runner.invoke(
            app,
            ["accounts", "add", "test-account", "--protocol", "imap", "--email", "user@example.com"],
            input="imap.example.com\n993\nuser@example.com\nsmtp.example.com\n587\nuser@example.com\nsecretpass\nTest User\n",
        )

        assert result.exit_code == 0
        assert "added successfully" in result.stdout
        mock_save_pass.assert_called_once()
        mock_save_config.assert_called_once()

    @patch("clerk.cli.load_config")
    def test_accounts_add_duplicate(self, mock_load):
        from clerk.config import (
            AccountConfig,
            ClerkConfig,
            FromAddress,
            ImapConfig,
            SmtpConfig,
        )

        mock_load.return_value = ClerkConfig(
            accounts={
                "existing": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.ex.com", username="user"),
                    smtp=SmtpConfig(host="smtp.ex.com", username="user"),
                    **{"from": FromAddress(address="user@ex.com")},
                ),
            },
        )

        result = runner.invoke(
            app,
            ["accounts", "add", "existing", "--email", "new@example.com"],
        )

        assert result.exit_code != 0
        assert "already exists" in result.output

    @patch("clerk.cli.get_config")
    def test_accounts_test_not_found(self, mock_config):
        from clerk.config import ClerkConfig

        mock_config.return_value = ClerkConfig()

        result = runner.invoke(app, ["accounts", "test", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output

    @patch("clerk.cli.save_config")
    @patch("clerk.cli.delete_password")
    @patch("clerk.cli.load_config")
    def test_accounts_remove(self, mock_load, mock_delete_pass, mock_save):
        from clerk.config import (
            AccountConfig,
            ClerkConfig,
            FromAddress,
            ImapConfig,
            SmtpConfig,
        )

        mock_load.return_value = ClerkConfig(
            default_account="test",
            accounts={
                "test": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.ex.com", username="user"),
                    smtp=SmtpConfig(host="smtp.ex.com", username="user"),
                    **{"from": FromAddress(address="user@ex.com")},
                ),
            },
        )

        result = runner.invoke(app, ["accounts", "remove", "test", "--yes"])

        assert result.exit_code == 0
        assert "removed" in result.stdout
        mock_delete_pass.assert_called_once_with("test")
        mock_save.assert_called_once()

    @patch("clerk.cli.load_config")
    def test_accounts_remove_not_found(self, mock_load):
        from clerk.config import ClerkConfig

        mock_load.return_value = ClerkConfig()

        result = runner.invoke(app, ["accounts", "remove", "nonexistent", "--yes"])
        assert result.exit_code != 0
        assert "not found" in result.output

    @patch("clerk.cli.save_config")
    @patch("clerk.cli.delete_password")
    @patch("clerk.cli.load_config")
    def test_accounts_remove_cancelled(self, mock_load, mock_delete, mock_save):
        from clerk.config import (
            AccountConfig,
            ClerkConfig,
            FromAddress,
            ImapConfig,
            SmtpConfig,
        )

        mock_load.return_value = ClerkConfig(
            accounts={
                "test": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.ex.com", username="user"),
                    smtp=SmtpConfig(host="smtp.ex.com", username="user"),
                    **{"from": FromAddress(address="user@ex.com")},
                ),
            },
        )

        result = runner.invoke(app, ["accounts", "remove", "test"], input="n\n")

        assert "Cancelled" in result.stdout
        mock_delete.assert_not_called()
        mock_save.assert_not_called()


class TestMicrosoft365Accounts:
    """Tests for Microsoft 365 account CLI commands."""

    @patch("clerk.cli.save_config")
    @patch("clerk.cli.load_config")
    def test_accounts_add_microsoft365(self, mock_load, mock_save_config):
        """Test adding a Microsoft 365 account (declining auth)."""
        from clerk.config import ClerkConfig

        mock_load.return_value = ClerkConfig()

        result = runner.invoke(
            app,
            ["accounts", "add", "ms365-test", "--protocol", "microsoft365", "--email", "user@outlook.com"],
            input="Test User\nn\n",  # display name, decline auth
        )

        assert result.exit_code == 0
        assert "added successfully" in result.stdout
        mock_save_config.assert_called_once()

        # Verify the saved config has the right protocol
        saved_config = mock_save_config.call_args[0][0]
        assert "ms365-test" in saved_config.accounts
        assert saved_config.accounts["ms365-test"].protocol == "microsoft365"

    @patch("clerk.cli.save_config")
    @patch("clerk.cli.load_config")
    def test_accounts_add_microsoft365_with_auth(self, mock_load, mock_save_config):
        """Test adding a Microsoft 365 account with successful authentication."""
        from clerk.config import ClerkConfig

        mock_load.return_value = ClerkConfig()

        with patch("clerk.microsoft365.run_m365_device_code_flow") as mock_flow:
            mock_flow.return_value = None

            result = runner.invoke(
                app,
                ["accounts", "add", "ms365-test", "--protocol", "microsoft365", "--email", "user@outlook.com"],
                input="Test User\ny\n",  # display name, accept auth
            )

        assert result.exit_code == 0
        assert "added successfully" in result.stdout

    def test_accounts_add_unknown_protocol(self):
        """Test adding account with unknown protocol."""
        result = runner.invoke(
            app,
            ["accounts", "add", "test", "--protocol", "unknown", "--email", "user@example.com"],
        )

        assert result.exit_code != 0
        assert "Unknown protocol" in result.output
        assert "microsoft365" in result.output  # should mention m365 as an option

    @patch("clerk.cli.save_config")
    @patch("clerk.cli.load_config")
    def test_accounts_remove_microsoft365(self, mock_load, mock_save):
        """Test removing a Microsoft 365 account deletes M365 token cache."""
        from clerk.config import AccountConfig, ClerkConfig, FromAddress

        mock_load.return_value = ClerkConfig(
            default_account="ms365-test",
            accounts={
                "ms365-test": AccountConfig(
                    protocol="microsoft365",
                    **{"from": FromAddress(address="user@outlook.com")},
                ),
            },
        )

        with patch("clerk.config.delete_m365_token_cache") as mock_delete_cache:
            result = runner.invoke(app, ["accounts", "remove", "ms365-test", "--yes"])

            assert result.exit_code == 0
            assert "removed" in result.stdout
            mock_delete_cache.assert_called_once_with("ms365-test")
            mock_save.assert_called_once()

    @patch("clerk.cli.get_config")
    def test_accounts_auth_microsoft365(self, mock_config):
        """Test re-authenticating a Microsoft 365 account."""
        from clerk.config import AccountConfig, ClerkConfig, FromAddress

        mock_config.return_value = ClerkConfig(
            accounts={
                "ms365-test": AccountConfig(
                    protocol="microsoft365",
                    **{"from": FromAddress(address="user@outlook.com")},
                ),
            },
        )

        with patch("clerk.microsoft365.run_m365_device_code_flow") as mock_flow:
            mock_flow.return_value = None
            result = runner.invoke(app, ["accounts", "auth", "ms365-test"])

            assert result.exit_code == 0
            assert "Authentication successful" in result.stdout
            mock_flow.assert_called_once_with("ms365-test")

    @patch("clerk.cli.get_config")
    def test_accounts_auth_not_found(self, mock_config):
        """Test auth command for non-existent account."""
        from clerk.config import ClerkConfig

        mock_config.return_value = ClerkConfig()

        result = runner.invoke(app, ["accounts", "auth", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output

    @patch("clerk.cli.get_config")
    def test_accounts_auth_imap_rejected(self, mock_config):
        """Test auth command for IMAP account (should be rejected)."""
        from clerk.config import (
            AccountConfig,
            ClerkConfig,
            FromAddress,
            ImapConfig,
            SmtpConfig,
        )

        mock_config.return_value = ClerkConfig(
            accounts={
                "imap-test": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.ex.com", username="user"),
                    smtp=SmtpConfig(host="smtp.ex.com", username="user"),
                    **{"from": FromAddress(address="user@ex.com")},
                ),
            },
        )

        result = runner.invoke(app, ["accounts", "auth", "imap-test"])
        assert result.exit_code != 0
        assert "password authentication" in result.output

    @patch("clerk.cli.get_config")
    def test_accounts_auth_gmail(self, mock_config):
        """Test auth command for Gmail account."""
        from pathlib import Path

        from clerk.config import (
            AccountConfig,
            ClerkConfig,
            FromAddress,
            OAuthConfig,
        )

        mock_config.return_value = ClerkConfig(
            accounts={
                "gmail-test": AccountConfig(
                    protocol="gmail",
                    oauth=OAuthConfig(client_id_file=Path("/tmp/credentials.json")),
                    **{"from": FromAddress(address="user@gmail.com")},
                ),
            },
        )

        with patch("clerk.oauth.run_oauth_flow") as mock_flow:
            mock_flow.return_value = None
            result = runner.invoke(app, ["accounts", "auth", "gmail-test"])

            assert result.exit_code == 0
            assert "Authentication successful" in result.stdout
            mock_flow.assert_called_once()

    @patch("clerk.cli.get_config")
    def test_accounts_auth_microsoft365_failure(self, mock_config):
        """Test auth command when Microsoft 365 auth fails."""
        from clerk.config import AccountConfig, ClerkConfig, FromAddress

        mock_config.return_value = ClerkConfig(
            accounts={
                "ms365-test": AccountConfig(
                    protocol="microsoft365",
                    **{"from": FromAddress(address="user@outlook.com")},
                ),
            },
        )

        with patch("clerk.microsoft365.run_m365_device_code_flow") as mock_flow:
            mock_flow.side_effect = Exception("Token expired")
            result = runner.invoke(app, ["accounts", "auth", "ms365-test"])

            assert result.exit_code != 0
            assert "Authentication failed" in result.output


class TestHostGuessing:
    def test_guess_imap_host_gmail(self):
        from clerk.cli import _guess_imap_host

        assert _guess_imap_host("user@gmail.com") == "imap.gmail.com"
        assert _guess_imap_host("user@googlemail.com") == "imap.gmail.com"

    def test_guess_imap_host_outlook(self):
        from clerk.cli import _guess_imap_host

        assert _guess_imap_host("user@outlook.com") == "outlook.office365.com"
        assert _guess_imap_host("user@hotmail.com") == "outlook.office365.com"

    def test_guess_imap_host_unknown(self):
        from clerk.cli import _guess_imap_host

        assert _guess_imap_host("user@custom-domain.org") == "imap.custom-domain.org"

    def test_guess_smtp_host_gmail(self):
        from clerk.cli import _guess_smtp_host

        assert _guess_smtp_host("user@gmail.com") == "smtp.gmail.com"

    def test_guess_smtp_host_unknown(self):
        from clerk.cli import _guess_smtp_host

        assert _guess_smtp_host("user@mydomain.com") == "smtp.mydomain.com"
