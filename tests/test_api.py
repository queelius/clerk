"""Tests for ClerkAPI."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clerk.api import ClerkAPI, ConversationLookupResult, InboxResult, SearchResult, get_api
from clerk.cache import Cache
from clerk.config import AccountConfig, ClerkConfig, FromAddress, ImapConfig, SmtpConfig
from clerk.drafts import DraftManager
from clerk.models import Address, ConversationSummary, Message, MessageFlag


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
        date=datetime.now(timezone.utc),
        subject="Test Subject",
        body_text="This is a test message body.",
        headers_fetched_at=datetime.now(timezone.utc),
        body_fetched_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def api(mock_config, cache, draft_manager, monkeypatch):
    """Create a ClerkAPI instance with mocked dependencies."""
    monkeypatch.setattr("clerk.api.ensure_dirs", lambda: None)
    return ClerkAPI(config=mock_config, cache=cache, draft_manager=draft_manager)


class TestClerkAPIInit:
    """Tests for ClerkAPI initialization."""

    def test_init_with_provided_dependencies(self, mock_config, cache, draft_manager, monkeypatch):
        """Test API initialization with provided dependencies."""
        monkeypatch.setattr("clerk.api.ensure_dirs", lambda: None)
        api = ClerkAPI(config=mock_config, cache=cache, draft_manager=draft_manager)

        assert api.config is mock_config
        assert api.cache is cache
        assert api.drafts is draft_manager

    def test_lazy_loading(self, monkeypatch):
        """Test lazy loading of dependencies."""
        monkeypatch.setattr("clerk.api.ensure_dirs", lambda: None)
        monkeypatch.setattr("clerk.api.get_config", lambda: MagicMock())
        monkeypatch.setattr("clerk.api.get_cache", lambda: MagicMock())
        monkeypatch.setattr("clerk.api.get_draft_manager", lambda: MagicMock())

        api = ClerkAPI()
        # Properties are lazy-loaded
        _ = api.config
        _ = api.cache
        _ = api.drafts


class TestInbox:
    """Tests for inbox operations."""

    def test_list_inbox_from_cache(self, api, cache, sample_message):
        """Test listing inbox from cache."""
        # Store message in cache
        cache.store_message(sample_message)
        cache.mark_inbox_synced("test")

        result = api.list_inbox(account="test")

        assert isinstance(result, InboxResult)
        assert result.account == "test"
        assert result.from_cache is True

    def test_get_conversation(self, api, cache, sample_message):
        """Test getting a conversation."""
        cache.store_message(sample_message)

        conv = api.get_conversation("conv123")

        assert conv is not None
        assert conv.conv_id == "conv123"
        assert len(conv.messages) == 1

    def test_get_conversation_not_found(self, api, cache):
        """Test getting a non-existent conversation."""
        conv = api.get_conversation("nonexistent")
        assert conv is None

    def test_get_message(self, api, cache, sample_message):
        """Test getting a message."""
        cache.store_message(sample_message)

        msg = api.get_message("<msg123@example.com>")

        assert msg is not None
        assert msg.message_id == "<msg123@example.com>"
        assert msg.subject == "Test Subject"

    def test_get_message_not_found(self, api, cache):
        """Test getting a non-existent message."""
        msg = api.get_message("<nonexistent@example.com>")
        assert msg is None


class TestSearch:
    """Tests for search operations."""

    def test_search_basic(self, api, cache, sample_message):
        """Test basic search."""
        cache.store_message(sample_message)

        result = api.search("test")

        assert isinstance(result, SearchResult)
        assert result.query == "test"
        assert result.count >= 0

    def test_search_advanced(self, api, cache, sample_message):
        """Test advanced search with operators."""
        cache.store_message(sample_message)

        result = api.search_advanced("from:sender")

        assert isinstance(result, SearchResult)
        assert "from:sender" in result.query

    def test_search_sql(self, api, cache, sample_message):
        """Test raw SQL search."""
        cache.store_message(sample_message)

        messages = api.search_sql("SELECT * FROM messages LIMIT 10")

        assert isinstance(messages, list)

    def test_search_sql_rejects_non_select(self, api):
        """Test that SQL search rejects non-SELECT queries."""
        with pytest.raises(ValueError, match="Only SELECT"):
            api.search_sql("DELETE FROM messages")

    def test_search_sql_rejects_dangerous(self, api):
        """Test that SQL search rejects dangerous keywords."""
        with pytest.raises(ValueError, match="disallowed keyword"):
            api.search_sql("SELECT * FROM messages; DROP TABLE messages")


class TestDrafts:
    """Tests for draft operations."""

    def test_create_draft(self, api):
        """Test creating a draft."""
        draft = api.create_draft(
            to=["recipient@example.com"],
            subject="Test Subject",
            body="Test body",
        )

        assert draft.draft_id.startswith("draft_")
        assert draft.subject == "Test Subject"
        assert len(draft.to) == 1

    def test_create_draft_with_address_objects(self, api):
        """Test creating a draft with Address objects."""
        draft = api.create_draft(
            to=[Address(addr="recipient@example.com", name="Recipient")],
            subject="Test Subject",
            body="Test body",
        )

        assert draft.to[0].addr == "recipient@example.com"

    def test_get_draft(self, api):
        """Test getting a draft."""
        created = api.create_draft(
            to=["recipient@example.com"],
            subject="Test",
            body="Body",
        )

        retrieved = api.get_draft(created.draft_id)

        assert retrieved is not None
        assert retrieved.draft_id == created.draft_id

    def test_get_draft_not_found(self, api):
        """Test getting a non-existent draft."""
        draft = api.get_draft("nonexistent")
        assert draft is None

    def test_list_drafts(self, api):
        """Test listing drafts."""
        api.create_draft(to=["a@ex.com"], subject="Draft 1", body="Body 1")
        api.create_draft(to=["b@ex.com"], subject="Draft 2", body="Body 2")

        drafts = api.list_drafts()

        assert len(drafts) == 2

    def test_delete_draft(self, api):
        """Test deleting a draft."""
        draft = api.create_draft(
            to=["recipient@example.com"],
            subject="To Delete",
            body="Body",
        )

        result = api.delete_draft(draft.draft_id)

        assert result is True
        assert api.get_draft(draft.draft_id) is None

    def test_delete_draft_not_found(self, api):
        """Test deleting a non-existent draft."""
        result = api.delete_draft("nonexistent")
        assert result is False


class TestMessageActions:
    """Tests for message actions."""

    def test_mark_read(self, api, cache, sample_message, monkeypatch):
        """Test marking a message as read."""
        cache.store_message(sample_message)

        # Mock IMAP client
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)

        api.mark_read("<msg123@example.com>")

        mock_client.add_flags.assert_called_once()

    def test_archive_message(self, api, cache, sample_message, monkeypatch):
        """Test archiving a message."""
        cache.store_message(sample_message)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)

        api.archive_message("<msg123@example.com>")

        mock_client.archive_message.assert_called_once()


class TestAttachments:
    """Tests for attachment operations."""

    def test_list_attachments(self, api, cache):
        """Test listing attachments."""
        msg = Message(
            message_id="<msg_with_att@example.com>",
            conv_id="conv1",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="sender@example.com")},
            date=datetime.now(timezone.utc),
            subject="With Attachment",
            headers_fetched_at=datetime.now(timezone.utc),
            attachments=[
                {"filename": "doc.pdf", "size": 1024, "content_type": "application/pdf"},
            ],
        )
        cache.store_message(msg)

        attachments = api.list_attachments("<msg_with_att@example.com>")

        assert len(attachments) == 1
        assert attachments[0]["filename"] == "doc.pdf"

    def test_list_attachments_message_not_found(self, api, cache):
        """Test listing attachments for non-existent message."""
        attachments = api.list_attachments("<nonexistent@example.com>")
        assert attachments == []


class TestCacheOperations:
    """Tests for cache operations."""

    def test_get_cache_stats(self, api, cache, sample_message):
        """Test getting cache statistics."""
        cache.store_message(sample_message)

        stats = api.get_cache_stats()

        assert stats.message_count == 1
        assert stats.conversation_count == 1

    def test_clear_cache(self, api, cache, sample_message):
        """Test clearing cache."""
        cache.store_message(sample_message)

        api.clear_cache()

        stats = api.get_cache_stats()
        assert stats.message_count == 0


class TestResolveConversationId:
    """Tests for resolve_conversation_id method."""

    def test_resolve_unique_prefix(self, api, cache):
        """Test resolving a unique prefix returns the conversation."""
        msg = Message(
            message_id="<msg1@example.com>",
            conv_id="abc123def456",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime.now(timezone.utc),
            subject="Test Subject",
            body_text="Body text",
            headers_fetched_at=datetime.now(timezone.utc),
            body_fetched_at=datetime.now(timezone.utc),
        )
        cache.store_message(msg)

        result = api.resolve_conversation_id("abc")

        assert result.conversation is not None
        assert result.conversation.conv_id == "abc123def456"
        assert result.matches is None
        assert result.error is None

    def test_resolve_ambiguous_prefix(self, api, cache):
        """Test resolving an ambiguous prefix returns matches."""
        msg1 = Message(
            message_id="<msg1@example.com>",
            conv_id="abc123xxx",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            subject="First Subject",
            headers_fetched_at=datetime.now(timezone.utc),
        )
        msg2 = Message(
            message_id="<msg2@example.com>",
            conv_id="abc456yyy",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="bob@example.com")},
            date=datetime(2025, 1, 2, 10, 0, 0, tzinfo=timezone.utc),
            subject="Second Subject",
            headers_fetched_at=datetime.now(timezone.utc),
        )
        cache.store_message(msg1)
        cache.store_message(msg2)

        result = api.resolve_conversation_id("abc")

        assert result.conversation is None
        assert result.matches is not None
        assert len(result.matches) == 2
        assert result.error is None

    def test_resolve_no_match(self, api, cache):
        """Test resolving a prefix with no matches returns error."""
        msg = Message(
            message_id="<msg1@example.com>",
            conv_id="abc123",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime.now(timezone.utc),
            headers_fetched_at=datetime.now(timezone.utc),
        )
        cache.store_message(msg)

        result = api.resolve_conversation_id("xyz")

        assert result.conversation is None
        assert result.matches is None
        assert result.error is not None
        assert "xyz" in result.error

    def test_resolve_exact_match(self, api, cache):
        """Test resolving exact conv_id works."""
        msg = Message(
            message_id="<msg1@example.com>",
            conv_id="exact_conv_id",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime.now(timezone.utc),
            subject="Test",
            body_text="Body",
            headers_fetched_at=datetime.now(timezone.utc),
            body_fetched_at=datetime.now(timezone.utc),
        )
        cache.store_message(msg)

        result = api.resolve_conversation_id("exact_conv_id")

        assert result.conversation is not None
        assert result.conversation.conv_id == "exact_conv_id"


class TestGetApi:
    """Tests for the get_api singleton function."""

    def test_get_api_returns_instance(self, monkeypatch):
        """Test that get_api returns a ClerkAPI instance."""
        # Reset singleton
        import clerk.api
        clerk.api._api_instance = None

        monkeypatch.setattr("clerk.api.ensure_dirs", lambda: None)
        monkeypatch.setattr("clerk.api.get_config", MagicMock)
        monkeypatch.setattr("clerk.api.get_cache", MagicMock)
        monkeypatch.setattr("clerk.api.get_draft_manager", MagicMock)

        api1 = get_api()
        api2 = get_api()

        assert api1 is api2  # Same instance

        # Reset for other tests
        clerk.api._api_instance = None
