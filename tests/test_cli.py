"""Tests for clerk CLI."""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from clerk.cli import app
from clerk.models import Address, CacheStats, ConversationSummary, Message

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
    @patch("clerk.cli.get_cache")
    def test_cache_status(self, mock_get_cache):
        mock_cache = MagicMock()
        mock_cache.get_stats.return_value = CacheStats(
            message_count=100,
            conversation_count=25,
            oldest_message=datetime(2025, 1, 1),
            newest_message=datetime(2025, 1, 3),
            cache_size_bytes=1024 * 1024,
            last_sync=datetime(2025, 1, 3, 12, 0),
        )
        mock_get_cache.return_value = mock_cache

        result = runner.invoke(app, ["cache", "status"])
        assert result.exit_code == 0
        assert "100" in result.stdout
        assert "25" in result.stdout

    @patch("clerk.cli.get_cache")
    def test_cache_status_json(self, mock_get_cache):
        mock_cache = MagicMock()
        mock_cache.get_stats.return_value = CacheStats(
            message_count=50,
            conversation_count=10,
            oldest_message=None,
            newest_message=None,
            cache_size_bytes=512,
            last_sync=None,
        )
        mock_get_cache.return_value = mock_cache

        result = runner.invoke(app, ["cache", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["message_count"] == 50

    @patch("clerk.cli.get_cache")
    def test_cache_clear_cancelled(self, mock_get_cache):
        mock_cache = MagicMock()
        mock_get_cache.return_value = mock_cache

        # Simulate user typing "n" to cancel
        runner.invoke(app, ["cache", "clear"], input="n\n")
        # When user cancels, confirm() aborts with exit code 1
        # But the command might exit cleanly depending on typer version
        mock_cache.clear.assert_not_called()


class TestDraftCommands:
    @patch("clerk.cli.get_draft_manager")
    @patch("clerk.cli.get_config")
    def test_draft_list_empty(self, mock_config, mock_manager):
        from clerk.config import (
            AccountConfig,
            ClerkConfig,
            FromAddress,
            ImapConfig,
            SmtpConfig,
        )

        mock_config.return_value = ClerkConfig(
            accounts={
                "test": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.ex.com", username="user"),
                    smtp=SmtpConfig(host="smtp.ex.com", username="user"),
                    **{"from": FromAddress(address="user@ex.com")},
                ),
            },
        )
        mock_manager.return_value.list.return_value = []

        result = runner.invoke(app, ["draft", "list"])
        assert result.exit_code == 0
        assert "No drafts" in result.stdout

    @patch("clerk.cli.get_draft_manager")
    @patch("clerk.cli.get_config")
    def test_draft_create(self, mock_config, mock_manager):
        from clerk.config import (
            AccountConfig,
            ClerkConfig,
            FromAddress,
            ImapConfig,
            SmtpConfig,
        )
        from clerk.models import Draft

        mock_config.return_value = ClerkConfig(
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

        mock_draft = Draft(
            draft_id="draft_test123",
            account="test",
            to=[Address(addr="recipient@example.com")],
            subject="Test Subject",
            body_text="Test body",
        )
        mock_manager.return_value.create.return_value = mock_draft

        result = runner.invoke(
            app,
            [
                "draft",
                "create",
                "--to",
                "recipient@example.com",
                "--subject",
                "Test Subject",
                "--body",
                "Test body",
            ],
        )

        assert result.exit_code == 0
        assert "draft_test123" in result.stdout

    @patch("clerk.cli.get_draft_manager")
    def test_draft_show_not_found(self, mock_manager):
        mock_manager.return_value.get.return_value = None

        result = runner.invoke(app, ["draft", "show", "nonexistent"])
        assert result.exit_code == 1

    @patch("clerk.cli.get_draft_manager")
    def test_draft_delete(self, mock_manager):
        mock_manager.return_value.delete.return_value = True

        result = runner.invoke(app, ["draft", "delete", "draft_123"])
        assert result.exit_code == 0
        assert "Deleted" in result.stdout

    @patch("clerk.cli.get_draft_manager")
    def test_draft_delete_not_found(self, mock_manager):
        mock_manager.return_value.delete.return_value = False

        result = runner.invoke(app, ["draft", "delete", "nonexistent"])
        assert result.exit_code == 1


class TestInboxCommand:
    @patch("clerk.cli.get_cache")
    @patch("clerk.cli.get_config")
    def test_inbox_from_cache(self, mock_config, mock_cache):
        from clerk.config import (
            AccountConfig,
            CacheConfig,
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
                    **{"from": FromAddress(address="user@ex.com")},
                ),
            },
            cache=CacheConfig(),
        )

        cache_instance = MagicMock()
        cache_instance.is_inbox_fresh.return_value = True
        cache_instance.list_conversations.return_value = [
            ConversationSummary(
                conv_id="conv123",
                subject="Test Thread",
                participants=["alice@ex.com"],
                message_count=3,
                unread_count=1,
                latest_date=datetime.now(UTC),
                snippet="Hello...",
            ),
        ]
        mock_cache.return_value = cache_instance

        result = runner.invoke(app, ["inbox"])
        assert result.exit_code == 0
        # Conv ID may be truncated in display, check for subject instead
        assert "Test Thread" in result.stdout

    @patch("clerk.cli.get_cache")
    @patch("clerk.cli.get_config")
    def test_inbox_empty(self, mock_config, mock_cache):
        from clerk.config import (
            AccountConfig,
            CacheConfig,
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
                    **{"from": FromAddress(address="user@ex.com")},
                ),
            },
            cache=CacheConfig(),
        )

        cache_instance = MagicMock()
        cache_instance.is_inbox_fresh.return_value = True
        cache_instance.list_conversations.return_value = []
        mock_cache.return_value = cache_instance

        result = runner.invoke(app, ["inbox"])
        assert result.exit_code == 0
        assert "No conversations" in result.stdout


class TestSearchCommand:
    @patch("clerk.cli.get_cache")
    def test_search_no_results(self, mock_cache):
        cache_instance = MagicMock()
        cache_instance.search.return_value = []
        mock_cache.return_value = cache_instance

        result = runner.invoke(app, ["search", "nonexistent query"])
        assert result.exit_code == 0
        assert "No results" in result.stdout

    @patch("clerk.cli.get_cache")
    def test_search_with_results(self, mock_cache):
        cache_instance = MagicMock()
        cache_instance.search.return_value = [
            Message(
                message_id="<msg1@ex.com>",
                conv_id="conv1",
                account="test",
                folder="INBOX",
                **{"from": Address(addr="sender@ex.com")},
                date=datetime.now(UTC),
                subject="Found message",
                headers_fetched_at=datetime.now(UTC),
            ),
        ]
        mock_cache.return_value = cache_instance

        result = runner.invoke(app, ["search", "found"])
        assert result.exit_code == 0
        assert "conv1" in result.stdout


class TestShowCommand:
    @patch("clerk.cli.get_cache")
    def test_show_not_found(self, mock_cache):
        cache_instance = MagicMock()
        cache_instance.get_conversation.return_value = None
        cache_instance.get_message.return_value = None
        mock_cache.return_value = cache_instance

        result = runner.invoke(app, ["show", "nonexistent"])
        assert result.exit_code == 1
        # Error message goes to stderr, use output which combines both
        assert "Not found" in result.output


class TestSendCommand:
    @patch("clerk.cli.send_draft")
    @patch("clerk.cli.check_send_allowed")
    @patch("clerk.cli.get_config")
    @patch("clerk.cli.get_draft_manager")
    def test_send_not_found(
        self, mock_manager, mock_config, mock_check, mock_send
    ):
        mock_manager.return_value.get.return_value = None

        result = runner.invoke(app, ["send", "nonexistent"])
        assert result.exit_code == 1
        # Error message goes to stderr, use output which combines both
        assert "not found" in result.output.lower()

    @patch("clerk.cli.send_draft")
    @patch("clerk.cli.check_send_allowed")
    @patch("clerk.cli.get_config")
    @patch("clerk.cli.get_draft_manager")
    def test_send_blocked(
        self, mock_manager, mock_config, mock_check, mock_send
    ):
        from clerk.config import ClerkConfig, SendConfig
        from clerk.models import Draft

        mock_draft = Draft(
            draft_id="draft_123",
            account="test",
            to=[Address(addr="recipient@ex.com")],
            subject="Test",
            body_text="Body",
        )
        mock_manager.return_value.get.return_value = mock_draft
        mock_config.return_value = ClerkConfig(send=SendConfig())
        mock_check.return_value = (False, "Rate limit exceeded")

        result = runner.invoke(app, ["send", "draft_123"])
        assert result.exit_code == 5  # SEND_BLOCKED


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


class TestSkillCommands:
    def test_skill_install_global(self, tmp_path, monkeypatch):
        """Test installing skill globally."""
        # Mock home directory to avoid modifying real ~/.claude
        monkeypatch.setattr("clerk.skill.Path.home", lambda: tmp_path)

        result = runner.invoke(app, ["skill", "install"])
        assert result.exit_code == 0
        assert "installed" in result.stdout.lower()
        assert "global" in result.stdout.lower()

        # Verify file was created
        skill_file = tmp_path / ".claude" / "skills" / "clerk" / "SKILL.md"
        assert skill_file.exists()
        content = skill_file.read_text()
        assert "clerk" in content
        assert "Email CLI for LLM Agents" in content

    def test_skill_install_local(self, tmp_path, monkeypatch):
        """Test installing skill locally."""
        # Mock cwd to use temp directory
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["skill", "install", "--local"])
        assert result.exit_code == 0
        assert "installed" in result.stdout.lower()
        assert "local" in result.stdout.lower()

        # Verify file was created
        skill_file = tmp_path / ".claude" / "skills" / "clerk" / "SKILL.md"
        assert skill_file.exists()

    def test_skill_uninstall_global(self, tmp_path, monkeypatch):
        """Test uninstalling skill globally."""
        monkeypatch.setattr("clerk.skill.Path.home", lambda: tmp_path)

        # First install
        runner.invoke(app, ["skill", "install"])
        skill_file = tmp_path / ".claude" / "skills" / "clerk" / "SKILL.md"
        assert skill_file.exists()

        # Then uninstall
        result = runner.invoke(app, ["skill", "uninstall"])
        assert result.exit_code == 0
        assert "uninstalled" in result.stdout.lower()
        assert not skill_file.exists()

    def test_skill_uninstall_not_installed(self, tmp_path, monkeypatch):
        """Test uninstalling when skill is not installed."""
        monkeypatch.setattr("clerk.skill.Path.home", lambda: tmp_path)

        result = runner.invoke(app, ["skill", "uninstall"])
        assert result.exit_code == 0
        assert "not installed" in result.stdout.lower()

    def test_skill_status_not_installed(self, tmp_path, monkeypatch):
        """Test status when skill is not installed."""
        monkeypatch.setattr("clerk.skill.Path.home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["skill", "status"])
        assert result.exit_code == 0
        assert "Not installed" in result.stdout

    def test_skill_status_global_installed(self, tmp_path, monkeypatch):
        """Test status when skill is installed globally."""
        monkeypatch.setattr("clerk.skill.Path.home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)

        # Install globally
        runner.invoke(app, ["skill", "install"])

        result = runner.invoke(app, ["skill", "status"])
        assert result.exit_code == 0
        assert "Global" in result.stdout
        assert "Installed" in result.stdout

    def test_skill_status_local_installed(self, tmp_path, monkeypatch):
        """Test status when skill is installed locally."""
        monkeypatch.setattr("clerk.skill.Path.home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)

        # Install locally
        runner.invoke(app, ["skill", "install", "--local"])

        result = runner.invoke(app, ["skill", "status"])
        assert result.exit_code == 0
        assert "Local" in result.stdout
        assert "Installed" in result.stdout

    def test_skill_status_json(self, tmp_path, monkeypatch):
        """Test status with JSON output."""
        # Use different paths for global and local
        global_home = tmp_path / "home"
        local_dir = tmp_path / "project"
        global_home.mkdir()
        local_dir.mkdir()

        monkeypatch.setattr("clerk.skill.Path.home", lambda: global_home)
        monkeypatch.chdir(local_dir)

        # Install globally
        runner.invoke(app, ["skill", "install"])

        result = runner.invoke(app, ["skill", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["global"]["installed"] is True
        assert data["local"]["installed"] is False
        assert ".claude/skills/clerk" in data["global"]["path"]
