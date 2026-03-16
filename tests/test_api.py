"""Tests for ClerkAPI."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from clerk.api import ClerkAPI, get_api, html_to_text
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
def api(mock_config, cache, draft_manager, monkeypatch):
    """Create a ClerkAPI instance with mocked dependencies."""
    monkeypatch.setattr("clerk.api.ensure_dirs", lambda: None)
    return ClerkAPI(config=mock_config, cache=cache, draft_manager=draft_manager)


class TestHtmlToText:
    def test_strips_tags(self):
        assert html_to_text("<p>Hello <b>world</b></p>") == "Hello world"

    def test_preserves_line_breaks(self):
        result = html_to_text("Line 1<br>Line 2<br/>Line 3")
        assert "Line 1\nLine 2\nLine 3" == result

    def test_decodes_entities(self):
        result = html_to_text("A &amp; B &lt; C &gt; D")
        assert "A & B < C > D" == result

    def test_strips_style_blocks(self):
        result = html_to_text("<style>body{color:red}</style>Hello")
        assert "Hello" == result

    def test_real_outlook_fragment(self):
        html = (
            '<html><body><div class="WordSection1">'
            "<p>Hi Alex,</p>"
            "<p>I can&#8217;t believe you wrote another paper.</p>"
            "</div></body></html>"
        )
        result = html_to_text(html)
        assert "Hi Alex," in result
        assert "can\u2019t believe" in result
        assert "<" not in result


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


class TestCreateReply:
    def test_create_reply_success(self, api, cache, sample_message):
        cache.store_message(sample_message)

        with patch.object(api.drafts, "create_reply") as mock_create:
            mock_create.return_value = MagicMock(draft_id="d1")
            draft = api.create_reply(
                message_id=sample_message.message_id,
                body="Thanks!",
            )
            mock_create.assert_called_once_with(
                account=sample_message.account,
                conv_id=sample_message.conv_id,
                body_text="Thanks!",
                reply_all=False,
            )
            assert draft.draft_id == "d1"

    def test_create_reply_message_not_found(self, api, cache):
        with pytest.raises(ValueError, match="not found"):
            api.create_reply(message_id="<nonexistent>", body="Hello")

    def test_create_reply_passes_reply_all(self, api, cache, sample_message):
        cache.store_message(sample_message)

        with patch.object(api.drafts, "create_reply") as mock_create:
            mock_create.return_value = MagicMock(draft_id="d1")
            api.create_reply(
                message_id=sample_message.message_id,
                body="Thanks!",
                reply_all=True,
            )
            mock_create.assert_called_once_with(
                account=sample_message.account,
                conv_id=sample_message.conv_id,
                body_text="Thanks!",
                reply_all=True,
            )


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


class TestSyncFolder:
    """Tests for sync_folder operation."""

    def test_incremental_sync_returns_count(self, api, monkeypatch):
        """Sync should return number of new messages fetched."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.fetch_messages_since_uid.return_value = ([], 0)
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)

        result = api.sync_folder(account="test", folder="INBOX")

        assert result["synced"] == 0
        assert result["account"] == "test"
        assert result["folder"] == "INBOX"

    def test_incremental_sync_updates_sync_state(self, api, cache, monkeypatch):
        """Sync should update the last_uid in sync_state."""
        msg = Message(
            message_id="<msg1@example.com>",
            conv_id="abc123",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com", name="Alice")},
            to=[Address(addr="test@example.com", name="Test")],
            subject="Test",
            date=datetime.now(UTC),
            headers_fetched_at=datetime.now(UTC),
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.fetch_messages_since_uid.return_value = ([msg], 100)
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)

        result = api.sync_folder(account="test", folder="INBOX")
        assert result["synced"] == 1

        state = cache.get_sync_state("test", "INBOX")
        assert state is not None
        assert state["last_uid"] == 100

    def test_incremental_sync_uses_existing_uid(self, api, cache, monkeypatch):
        """Sync should pass the last known UID to fetch_messages_since_uid."""
        cache.set_sync_state("test", "INBOX", 50)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.fetch_messages_since_uid.return_value = ([], 50)
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)

        api.sync_folder(account="test", folder="INBOX")

        mock_client.fetch_messages_since_uid.assert_called_once_with(
            folder="INBOX",
            since_uid=50,
            fetch_bodies=False,
        )

    def test_full_sync_ignores_sync_state(self, api, cache, monkeypatch):
        """Full sync should ignore existing sync state and pass since_uid=0."""
        cache.set_sync_state("test", "INBOX", 50)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.fetch_messages_since_uid.return_value = ([], 0)
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)

        api.sync_folder(account="test", folder="INBOX", full=True)

        mock_client.fetch_messages_since_uid.assert_called_once_with(
            folder="INBOX",
            since_uid=0,
            fetch_bodies=False,
        )

    def test_sync_does_not_update_state_if_no_new_messages(self, api, cache, monkeypatch):
        """Sync should not update sync state when highest_uid hasn't changed."""
        cache.set_sync_state("test", "INBOX", 50)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        # highest_uid == since_uid means no new messages
        mock_client.fetch_messages_since_uid.return_value = ([], 50)
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)

        api.sync_folder(account="test", folder="INBOX")

        state = cache.get_sync_state("test", "INBOX")
        assert state is not None
        assert state["last_uid"] == 50
