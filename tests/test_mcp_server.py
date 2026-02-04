"""Tests for MCP server implementation."""

import time
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from clerk.api import ClerkAPI
from clerk.cache import Cache
from clerk.config import AccountConfig, ClerkConfig, FromAddress, ImapConfig, SmtpConfig
from clerk.drafts import DraftManager
from clerk.models import Address, Message


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    return ClerkConfig(
        accounts={
            "test": AccountConfig(
                protocol="imap",
                imap=ImapConfig(host="imap.example.com", username="test@example.com"),
                smtp=SmtpConfig(host="smtp.example.com", username="test@example.com"),
                **{"from": FromAddress(address="test@example.com", name="Test User")},
            ),
        },
        default_account="test",
    )


@pytest.fixture
def cache(tmp_path):
    """Create a temporary cache database."""
    return Cache(tmp_path / "cache.db")


@pytest.fixture
def draft_manager(tmp_path, cache, monkeypatch):
    """Create a draft manager with temporary storage."""
    monkeypatch.setattr("clerk.drafts.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("clerk.drafts.get_cache", lambda: cache)
    return DraftManager()


@pytest.fixture
def sample_message():
    """Create a sample message."""
    return Message(
        message_id="<msg123@example.com>",
        conv_id="conv123",
        account="test",
        folder="INBOX",
        **{"from": Address(addr="sender@example.com", name="Sender")},
        to=[Address(addr="test@example.com")],
        date=datetime.now(UTC),
        subject="Test Subject",
        body_text="This is a test message body.",
        headers_fetched_at=datetime.now(UTC),
        body_fetched_at=datetime.now(UTC),
    )


@pytest.fixture
def mock_api(mock_config, cache, draft_manager, monkeypatch):
    """Create a mock API and inject it into the mcp_server module."""
    import clerk.api as api_module

    api = ClerkAPI(config=mock_config, cache=cache, draft_manager=draft_manager)

    # Override the get_api function to return our mock
    monkeypatch.setattr(api_module, "_api_instance", api)
    monkeypatch.setattr("clerk.mcp_server.get_api", lambda: api)
    monkeypatch.setattr("clerk.mcp_server.ensure_dirs", lambda: None)

    return api


class TestConfirmationTokens:
    """Tests for the two-step send confirmation flow."""

    def test_generate_and_validate_token(self):
        """Test token generation and validation."""
        from clerk.mcp_server import (
            _confirmation_tokens,
            _generate_confirmation_token,
            _validate_confirmation_token,
        )

        # Clear any existing tokens
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
        """Test validation fails with wrong token."""
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
        """Test validation fails when no token exists."""
        from clerk.mcp_server import _confirmation_tokens, _validate_confirmation_token

        _confirmation_tokens.clear()

        valid, error = _validate_confirmation_token("nonexistent", "any_token")
        assert valid is False
        assert "No confirmation token found" in error

    def test_expired_token(self):
        """Test that expired tokens are rejected."""
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
        """Test that cleanup removes expired tokens."""
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


class TestClerkInbox:
    """Tests for clerk_inbox tool."""

    def test_inbox_from_cache(self, mock_api, cache, sample_message):
        """Test inbox returns cached conversations."""
        from clerk.mcp_server import clerk_inbox

        # Store message in cache
        cache.store_message(sample_message)
        cache.mark_inbox_synced("test")

        result = clerk_inbox(limit=10)

        assert "conversations" in result
        assert "account" in result
        assert result["count"] >= 0


class TestClerkShow:
    """Tests for clerk_show tool."""

    def test_show_conversation(self, mock_api, cache, sample_message):
        """Test showing a conversation."""
        from clerk.mcp_server import clerk_show

        cache.store_message(sample_message)

        result = clerk_show("conv123")

        assert result["type"] == "conversation"
        assert "conversation" in result

    def test_show_not_found(self, mock_api, cache):
        """Test showing non-existent conversation."""
        from clerk.mcp_server import clerk_show

        result = clerk_show("nonexistent")

        assert "error" in result
        assert "Not found" in result["error"]


class TestClerkSearch:
    """Tests for clerk_search tool."""

    def test_search_messages(self, mock_api, cache, sample_message):
        """Test searching messages."""
        from clerk.mcp_server import clerk_search

        cache.store_message(sample_message)

        result = clerk_search("test")

        assert "results" in result
        assert "query" in result
        assert result["query"] == "test"

    def test_search_advanced(self, mock_api, cache, sample_message):
        """Test advanced search with operators."""
        from clerk.mcp_server import clerk_search

        cache.store_message(sample_message)

        result = clerk_search("from:sender", advanced=True)

        assert "results" in result
        assert "query" in result


class TestClerkSearchSql:
    """Tests for clerk_search_sql tool."""

    def test_search_sql_basic(self, mock_api, cache, sample_message):
        """Test basic SQL search."""
        from clerk.mcp_server import clerk_search_sql

        cache.store_message(sample_message)

        result = clerk_search_sql("SELECT * FROM messages LIMIT 10")

        assert "results" in result
        assert "count" in result

    def test_search_sql_invalid(self, mock_api):
        """Test SQL search rejects non-SELECT."""
        from clerk.mcp_server import clerk_search_sql

        result = clerk_search_sql("DELETE FROM messages")

        assert "error" in result
        assert "Only SELECT" in result["error"]


class TestClerkDraft:
    """Tests for clerk_draft tool."""

    def test_create_draft(self, mock_api, mock_config):
        """Test creating a draft."""
        from clerk.mcp_server import clerk_draft

        result = clerk_draft(
            to="recipient@example.com",
            subject="Test Subject",
            body="Test body content",
        )

        assert "draft_id" in result
        assert result["draft_id"].startswith("draft_")
        assert result["subject"] == "Test Subject"

    def test_create_draft_with_cc(self, mock_api, mock_config):
        """Test creating a draft with CC."""
        from clerk.mcp_server import clerk_draft

        result = clerk_draft(
            to="recipient@example.com",
            subject="Test Subject",
            body="Test body content",
            cc="cc1@example.com, cc2@example.com",
        )

        assert "draft_id" in result


class TestClerkDrafts:
    """Tests for clerk_drafts tool."""

    def test_list_drafts(self, mock_api, mock_config):
        """Test listing drafts."""
        from clerk.mcp_server import clerk_draft, clerk_drafts

        # Create a draft first
        clerk_draft(
            to="recipient@example.com",
            subject="Test Draft",
            body="Test body",
        )

        result = clerk_drafts()

        assert "drafts" in result
        assert result["count"] >= 1

    def test_list_empty_drafts(self, mock_api):
        """Test listing when no drafts exist."""
        from clerk.mcp_server import clerk_drafts

        result = clerk_drafts()

        assert result["count"] == 0
        assert result["drafts"] == []


class TestClerkSend:
    """Tests for clerk_send tool with two-step confirmation."""

    def test_send_step1_preview(self, mock_api, mock_config, monkeypatch):
        """Test first step returns preview and token."""
        from clerk.mcp_server import _confirmation_tokens, clerk_draft, clerk_send

        _confirmation_tokens.clear()

        # Mock check_send_allowed to return True
        monkeypatch.setattr(
            "clerk.smtp_client.check_send_allowed", lambda d, a: (True, None)
        )

        # Create a draft
        draft_result = clerk_draft(
            to="recipient@example.com",
            subject="Test",
            body="Test body",
        )
        draft_id = draft_result["draft_id"]

        # Step 1: Get preview
        result = clerk_send(draft_id, confirm=False)

        assert result["status"] == "pending_confirmation"
        assert "preview" in result
        assert "token" in result
        assert result["expires_in_seconds"] == 300

    def test_send_step2_without_token(self, mock_api, mock_config):
        """Test step 2 fails without token."""
        from clerk.mcp_server import clerk_draft, clerk_send

        # Create a draft
        draft_result = clerk_draft(
            to="recipient@example.com",
            subject="Test",
            body="Test body",
        )
        draft_id = draft_result["draft_id"]

        # Step 2 without token
        result = clerk_send(draft_id, confirm=True)

        assert "error" in result
        assert "Token required" in result["error"]

    def test_send_draft_not_found(self, mock_api):
        """Test sending non-existent draft."""
        from clerk.mcp_server import clerk_send

        result = clerk_send("nonexistent_draft", confirm=False)

        assert "error" in result
        assert "not found" in result["error"].lower()


class TestClerkDeleteDraft:
    """Tests for clerk_delete_draft tool."""

    def test_delete_draft(self, mock_api, mock_config):
        """Test deleting a draft."""
        from clerk.mcp_server import clerk_delete_draft, clerk_draft

        # Create a draft
        draft_result = clerk_draft(
            to="recipient@example.com",
            subject="To Delete",
            body="Test body",
        )
        draft_id = draft_result["draft_id"]

        # Delete it
        result = clerk_delete_draft(draft_id)

        assert result["status"] == "deleted"
        assert result["draft_id"] == draft_id

    def test_delete_nonexistent_draft(self, mock_api):
        """Test deleting non-existent draft."""
        from clerk.mcp_server import clerk_delete_draft

        result = clerk_delete_draft("nonexistent")

        assert "error" in result
        assert "not found" in result["error"].lower()


class TestClerkMarkRead:
    """Tests for clerk_mark_read tool."""

    def test_mark_read_error_handling(self, mock_api, cache, mock_config):
        """Test error handling when marking as read fails."""
        from clerk.mcp_server import clerk_mark_read

        # Mock the API's mark_read to raise an error
        mock_api.mark_read = MagicMock(side_effect=Exception("Connection failed"))

        result = clerk_mark_read("msg123")

        assert "error" in result


class TestClerkArchive:
    """Tests for clerk_archive tool."""

    def test_archive_error_handling(self, mock_api, cache, mock_config):
        """Test error handling when archive fails."""
        from clerk.mcp_server import clerk_archive

        # Mock the API's archive_message to raise an error
        mock_api.archive_message = MagicMock(side_effect=Exception("Connection failed"))

        result = clerk_archive("msg123")

        assert "error" in result


class TestClerkAttachments:
    """Tests for clerk_attachments tool."""

    def test_list_attachments(self, mock_api, cache):
        """Test listing attachments."""
        from clerk.mcp_server import clerk_attachments

        # Create a message with attachments
        msg = Message(
            message_id="<msg_with_att@example.com>",
            conv_id="conv1",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="sender@example.com")},
            date=datetime.now(UTC),
            subject="With Attachment",
            headers_fetched_at=datetime.now(UTC),
            attachments=[
                {"filename": "doc.pdf", "size": 1024, "content_type": "application/pdf"},
            ],
        )
        cache.store_message(msg)

        result = clerk_attachments("<msg_with_att@example.com>")

        assert result["count"] == 1
        assert result["attachments"][0]["filename"] == "doc.pdf"

    def test_list_attachments_message_not_found(self, mock_api):
        """Test listing attachments for non-existent message."""
        from clerk.mcp_server import clerk_attachments

        result = clerk_attachments("<nonexistent@example.com>")

        assert "error" in result


class TestResources:
    """Tests for MCP resources."""

    def test_resource_inbox(self, mock_api, cache, sample_message):
        """Test inbox resource returns JSON."""
        from clerk.mcp_server import resource_inbox

        cache.store_message(sample_message)
        cache.mark_inbox_synced("test")

        result = resource_inbox()

        import json

        parsed = json.loads(result)
        assert "conversations" in parsed

    def test_resource_conversation(self, mock_api, cache, sample_message):
        """Test conversation resource returns JSON."""
        from clerk.mcp_server import resource_conversation

        cache.store_message(sample_message)

        result = resource_conversation("conv123")

        import json

        parsed = json.loads(result)
        assert "conversation" in parsed or "error" in parsed

    def test_resource_draft(self, mock_api, mock_config):
        """Test draft resource returns JSON."""
        from clerk.mcp_server import clerk_draft, resource_draft

        # Create a draft
        draft_result = clerk_draft(
            to="recipient@example.com",
            subject="Test",
            body="Test body",
        )
        draft_id = draft_result["draft_id"]

        result = resource_draft(draft_id)

        import json

        parsed = json.loads(result)
        assert parsed["draft_id"] == draft_id

    def test_resource_draft_not_found(self, mock_api):
        """Test draft resource returns error for non-existent draft."""
        from clerk.mcp_server import resource_draft

        result = resource_draft("nonexistent")

        import json

        parsed = json.loads(result)
        assert "error" in parsed


class TestMcpServerModule:
    """Tests for MCP server module initialization."""

    def test_mcp_server_exists(self):
        """Test that the MCP server object is created."""
        from clerk.mcp_server import mcp

        assert mcp is not None

    def test_run_server_function_exists(self):
        """Test that run_server function exists."""
        from clerk.mcp_server import run_server

        assert callable(run_server)
