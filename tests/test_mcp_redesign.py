"""Tests for the redesigned MCP server (8 tools + 3 resources)."""

import json
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from clerk.cache import Cache
from clerk.config import (
    AccountConfig,
    ClerkConfig,
    FromAddress,
    ImapConfig,
    PrioritiesConfig,
    SmtpConfig,
)
from clerk.models import Address, Message, MessageFlag


@pytest.fixture
def populated_cache(tmp_path):
    """Create a cache with test messages."""
    cache = Cache(tmp_path / "test.db")
    msg1 = Message(
        message_id="<msg1@example.com>",
        conv_id="conv001",
        account="test",
        folder="INBOX",
        **{"from": Address(addr="alice@example.com", name="Alice")},
        to=[Address(addr="test@example.com", name="Test")],
        subject="Hello",
        date=datetime(2026, 3, 1, tzinfo=UTC),
        body_text="Hello body",
        flags=[],
    )
    msg2 = Message(
        message_id="<msg2@example.com>",
        conv_id="conv001",
        account="test",
        folder="INBOX",
        **{"from": Address(addr="test@example.com", name="Test")},
        to=[Address(addr="alice@example.com", name="Alice")],
        subject="Re: Hello",
        date=datetime(2026, 3, 2, tzinfo=UTC),
        body_text="Reply body",
        flags=[MessageFlag.SEEN],
        in_reply_to="<msg1@example.com>",
        references=["<msg1@example.com>"],
    )
    cache.store_message(msg1)
    cache.store_message(msg2)
    return cache


@pytest.fixture
def mock_config():
    return ClerkConfig(
        accounts={
            "test": AccountConfig(
                protocol="imap",
                imap=ImapConfig(host="localhost", username="test"),
                smtp=SmtpConfig(host="localhost", username="test"),
                **{"from": FromAddress(address="test@example.com", name="Test User")},
            ),
        },
        default_account="test",
        priorities=PrioritiesConfig(
            senders=["alice@example.com", "@siue.edu"],
            topics=["IDOT", "scanner"],
        ),
    )


# --- Confirmation tokens ---

class TestConfirmationTokens:
    """Tests for the two-step send confirmation flow internals."""

    def test_generate_and_validate_token(self):
        from clerk.mcp_server import (
            _confirmation_tokens,
            _generate_confirmation_token,
            _validate_confirmation_token,
        )

        _confirmation_tokens.clear()

        draft_id = "draft_test123"
        token = _generate_confirmation_token(draft_id)

        assert token is not None
        assert len(token) == 32  # 16 bytes hex = 32 chars
        assert draft_id in _confirmation_tokens

        # Validate the token
        valid, error = _validate_confirmation_token(draft_id, token)
        assert valid is True
        assert error is None

        # Token should be consumed (one-time use)
        assert draft_id not in _confirmation_tokens

    def test_invalid_token(self):
        from clerk.mcp_server import (
            _confirmation_tokens,
            _generate_confirmation_token,
            _validate_confirmation_token,
        )

        _confirmation_tokens.clear()

        draft_id = "draft_test456"
        _generate_confirmation_token(draft_id)

        valid, error = _validate_confirmation_token(draft_id, "wrong_token")
        assert valid is False
        assert "Invalid confirmation token" in error

    def test_no_token_exists(self):
        from clerk.mcp_server import _confirmation_tokens, _validate_confirmation_token

        _confirmation_tokens.clear()

        valid, error = _validate_confirmation_token("nonexistent", "any_token")
        assert valid is False
        assert "No confirmation token found" in error

    def test_expired_token(self):
        from clerk.mcp_server import (
            _confirmation_tokens,
            _validate_confirmation_token,
        )

        _confirmation_tokens.clear()

        draft_id = "draft_expired"
        token = "test_token"
        # Set expiry in the past
        _confirmation_tokens[draft_id] = (token, time.time() - 1)

        valid, error = _validate_confirmation_token(draft_id, token)
        assert valid is False
        assert "expired" in error.lower()

    def test_cleanup_expired_tokens(self):
        from clerk.mcp_server import _cleanup_expired_tokens, _confirmation_tokens

        _confirmation_tokens.clear()

        # Add some tokens - one expired, one valid
        _confirmation_tokens["expired1"] = ("token1", time.time() - 100)
        _confirmation_tokens["expired2"] = ("token2", time.time() - 10)
        _confirmation_tokens["valid1"] = ("token3", time.time() + 300)

        _cleanup_expired_tokens()

        assert "expired1" not in _confirmation_tokens
        assert "expired2" not in _confirmation_tokens
        assert "valid1" in _confirmation_tokens


# --- clerk_sql ---

class TestClerkSql:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_select_returns_rows(self, _dirs, mock_get_api, populated_cache):
        from clerk.mcp_server import clerk_sql

        mock_api = MagicMock()
        mock_api.cache = populated_cache
        mock_get_api.return_value = mock_api

        result = clerk_sql(query="SELECT message_id, subject FROM messages ORDER BY date_utc")
        assert result["count"] == 2
        assert result["rows"][0]["subject"] == "Hello"

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_rejects_non_select(self, _dirs, mock_get_api, populated_cache):
        from clerk.mcp_server import clerk_sql

        mock_api = MagicMock()
        mock_api.cache = populated_cache
        mock_get_api.return_value = mock_api

        result = clerk_sql(query="DELETE FROM messages")
        assert "error" in result


# --- clerk_sync ---

class TestClerkSync:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_sync_calls_api(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_sync

        mock_api = MagicMock()
        mock_api.sync_folder.return_value = {"synced": 5, "account": "test", "folder": "INBOX"}
        mock_get_api.return_value = mock_api

        result = clerk_sync(account="test")
        assert result["synced"] == 5
        mock_api.sync_folder.assert_called_once_with(account="test", folder="INBOX", full=False)

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_sync_full(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_sync

        mock_api = MagicMock()
        mock_api.sync_folder.return_value = {"synced": 100, "account": "test", "folder": "INBOX"}
        mock_get_api.return_value = mock_api

        clerk_sync(account="test", full=True)
        mock_api.sync_folder.assert_called_once_with(account="test", folder="INBOX", full=True)


# --- clerk_reply ---

class TestClerkReplyRouting:
    """Test that clerk_reply routes through api.create_reply()."""

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_reply_calls_api_create_reply(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_reply
        from clerk.models import Address, Draft

        mock_api = MagicMock()
        mock_api.create_reply.return_value = Draft(
            draft_id="d1",
            account="test",
            to=[Address(addr="alice@example.com", name="Alice")],
            cc=[],
            subject="Re: Test",
            body_text="reply body",
        )
        mock_get_api.return_value = mock_api

        result = clerk_reply(message_id="<msg1>", body="reply body")

        mock_api.create_reply.assert_called_once_with(
            message_id="<msg1>",
            body="reply body",
            reply_all=False,
            account=None,
        )
        assert result["draft_id"] == "d1"
        assert "preview" not in result  # no redundant preview

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_reply_with_reply_all(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_reply
        from clerk.models import Address, Draft

        mock_api = MagicMock()
        mock_api.create_reply.return_value = Draft(
            draft_id="d1",
            account="test",
            to=[Address(addr="alice@example.com", name="Alice")],
            cc=[Address(addr="bob@example.com", name="Bob")],
            subject="Re: Test",
            body_text="reply body",
        )
        mock_get_api.return_value = mock_api

        result = clerk_reply(message_id="<msg1>", body="reply body", reply_all=True)

        mock_api.create_reply.assert_called_once_with(
            message_id="<msg1>",
            body="reply body",
            reply_all=True,
            account=None,
        )

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_reply_message_not_found(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_reply

        mock_api = MagicMock()
        mock_api.create_reply.side_effect = ValueError("Message not found: <msg1>")
        mock_get_api.return_value = mock_api

        result = clerk_reply(message_id="<msg1>", body="reply body")
        assert "error" in result
        assert "not found" in result["error"].lower()


# --- clerk_draft ---

class TestClerkDraftListParams:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_draft_with_list_params(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_draft
        from clerk.models import Address, Draft

        mock_api = MagicMock()
        mock_api.create_draft.return_value = Draft(
            draft_id="d1",
            account="test",
            to=[Address(addr="alice@example.com", name="")],
            cc=[Address(addr="bob@example.com", name="")],
            subject="Test",
            body_text="body",
        )
        mock_get_api.return_value = mock_api

        result = clerk_draft(
            to=["alice@example.com"],
            subject="Test",
            body="body",
            cc=["bob@example.com"],
        )

        assert result["draft_id"] == "d1"
        mock_api.create_draft.assert_called_once_with(
            to=["alice@example.com"],
            subject="Test",
            body="body",
            cc=["bob@example.com"],
            account=None,
        )


# --- clerk_send ---

class TestClerkSend:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_send_step1_returns_token(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_send

        mock_draft = MagicMock()
        mock_draft.account = "test"
        mock_draft.to = [Address(addr="bob@example.com", name="Bob")]
        mock_draft.cc = []
        mock_draft.subject = "Test"
        mock_draft.body_text = "Test body"

        mock_api = MagicMock()
        mock_api.get_draft.return_value = mock_draft
        mock_get_api.return_value = mock_api

        with (
            patch("clerk.mcp_server.check_send_allowed", return_value=(True, None)),
            patch("clerk.mcp_server.format_draft_preview", return_value="Preview text"),
        ):
            result = clerk_send(draft_id="draft_1")

        assert result["status"] == "pending_confirmation"
        assert "token" in result


# --- clerk_move ---

class TestClerkMoveRedesign:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_move_success(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_move

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api

        result = clerk_move(message_id="<msg1>", to_folder="Archive")
        assert result["status"] == "success"
        mock_api.move_message.assert_called_once()


# --- clerk_flag ---

class TestClerkFlagRedesign:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_flag_action(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_flag

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api

        result = clerk_flag(message_id="<msg1>", action="flag")
        assert result["status"] == "success"
        mock_api.flag_message.assert_called_once()

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_read_action(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_flag

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api

        result = clerk_flag(message_id="<msg1>", action="read")
        assert result["status"] == "success"
        mock_api.mark_read.assert_called_once()

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_invalid_action(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_flag

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api

        result = clerk_flag(message_id="<msg1>", action="invalid")
        assert "error" in result


# --- clerk_status ---

class TestClerkStatusRedesign:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_status(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_status

        mock_api = MagicMock()
        mock_api.get_status.return_value = {"version": "0.6.0", "accounts": {}}
        mock_get_api.return_value = mock_api

        result = clerk_status()
        assert "version" in result


# --- Resources ---

class TestResources:
    @patch("clerk.mcp_server.get_config")
    def test_schema_resource(self, mock_get_config):
        from clerk.mcp_server import resource_schema

        result = resource_schema()
        assert "messages" in result
        assert "messages_fts" in result
        assert "SELECT" in result  # Example queries

    @patch("clerk.mcp_server.get_config")
    def test_config_resource(self, mock_get_config, mock_config):
        from clerk.mcp_server import resource_config

        mock_get_config.return_value = mock_config
        result = resource_config()
        data = json.loads(result)
        assert "accounts" in data
        assert "priorities" in data
        assert data["default_account"] == "test"

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.get_config")
    def test_folders_resource(self, mock_get_config, mock_get_api, mock_config):
        from clerk.mcp_server import resource_folders
        from clerk.models import FolderInfo

        mock_get_config.return_value = mock_config
        mock_api = MagicMock()
        mock_api.list_folders.return_value = [
            FolderInfo(name="INBOX"), FolderInfo(name="Sent"),
        ]
        mock_get_api.return_value = mock_api

        result = resource_folders()
        data = json.loads(result)
        assert "test" in data
        assert "INBOX" in data["test"]
        assert "Sent" in data["test"]
