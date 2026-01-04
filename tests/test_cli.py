"""Tests for clerk CLI."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from clerk.cli import app
from clerk.models import Address, CacheStats, ConversationSummary, Message, MessageFlag

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
        result = runner.invoke(app, ["cache", "clear"], input="n\n")
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
                latest_date=datetime.utcnow(),
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
                date=datetime.utcnow(),
                subject="Found message",
                headers_fetched_at=datetime.utcnow(),
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
