"""Tests for clerk cache."""

from datetime import UTC, datetime, timedelta

import pytest

from clerk.cache import Cache
from clerk.models import Address, Message, MessageFlag


@pytest.fixture
def cache(tmp_path):
    """Create a cache instance with a temporary database."""
    db_path = tmp_path / "test_cache.db"
    return Cache(db_path)


@pytest.fixture
def sample_message():
    """Create a sample message for testing."""
    return Message(
        message_id="<test123@example.com>",
        conv_id="conv_abc123",
        account="test_account",
        folder="INBOX",
        **{"from": Address(addr="sender@example.com", name="Sender Name")},
        to=[Address(addr="recipient@example.com", name="Recipient")],
        cc=[],
        date=datetime.now(UTC),
        subject="Test Subject",
        body_text="This is the body text",
        body_html="<p>This is the body text</p>",
        flags=[MessageFlag.SEEN],
        in_reply_to=None,
        references=[],
        headers_fetched_at=datetime.now(UTC),
        body_fetched_at=datetime.now(UTC),
    )


class TestCacheBasics:
    def test_store_and_retrieve_message(self, cache, sample_message):
        cache.store_message(sample_message)

        retrieved = cache.get_message(sample_message.message_id)
        assert retrieved is not None
        assert retrieved.message_id == sample_message.message_id
        assert retrieved.subject == sample_message.subject
        assert retrieved.from_.addr == sample_message.from_.addr
        assert retrieved.body_text == sample_message.body_text

    def test_get_nonexistent_message(self, cache):
        result = cache.get_message("<nonexistent@example.com>")
        assert result is None

    def test_store_updates_existing(self, cache, sample_message):
        cache.store_message(sample_message)

        # Update the message
        updated = sample_message.model_copy()
        updated.subject = "Updated Subject"
        cache.store_message(updated)

        retrieved = cache.get_message(sample_message.message_id)
        assert retrieved.subject == "Updated Subject"


class TestCacheConversations:
    def test_get_conversation(self, cache):
        # Create messages in a conversation
        conv_id = "conv_thread123"
        msg1 = Message(
            message_id="<msg1@example.com>",
            conv_id=conv_id,
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime(2025, 1, 1, 10, 0, 0),
            subject="Thread subject",
            body_text="First message",
            flags=[MessageFlag.SEEN],
            headers_fetched_at=datetime.now(UTC),
        )
        msg2 = Message(
            message_id="<msg2@example.com>",
            conv_id=conv_id,
            account="test",
            folder="INBOX",
            **{"from": Address(addr="bob@example.com")},
            to=[Address(addr="alice@example.com")],
            date=datetime(2025, 1, 1, 11, 0, 0),
            subject="Re: Thread subject",
            body_text="Second message",
            flags=[],
            headers_fetched_at=datetime.now(UTC),
        )

        cache.store_message(msg1)
        cache.store_message(msg2)

        conv = cache.get_conversation(conv_id)
        assert conv is not None
        assert conv.conv_id == conv_id
        assert conv.message_count == 2
        assert conv.unread_count == 1  # msg2 is unread
        assert len(conv.messages) == 2
        # Messages should be sorted by date
        assert conv.messages[0].message_id == "<msg1@example.com>"
        assert conv.messages[1].message_id == "<msg2@example.com>"

    def test_get_nonexistent_conversation(self, cache):
        result = cache.get_conversation("nonexistent")
        assert result is None

    def test_list_conversations(self, cache):
        # Create multiple conversations
        for i in range(3):
            msg = Message(
                message_id=f"<msg{i}@example.com>",
                conv_id=f"conv_{i}",
                account="test",
                folder="INBOX",
                **{"from": Address(addr=f"user{i}@example.com")},
                date=datetime(2025, 1, i + 1, 10, 0, 0),
                subject=f"Subject {i}",
                body_text=f"Body {i}",
                flags=[MessageFlag.SEEN] if i % 2 == 0 else [],
                headers_fetched_at=datetime.now(UTC),
            )
            cache.store_message(msg)

        convs = cache.list_conversations(account="test", folder="INBOX", limit=10)
        assert len(convs) == 3
        # Should be sorted by latest date descending
        assert convs[0].conv_id == "conv_2"

    def test_list_unread_only(self, cache):
        # Create read and unread messages
        msg_read = Message(
            message_id="<read@example.com>",
            conv_id="conv_read",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="a@example.com")},
            date=datetime.now(UTC),
            flags=[MessageFlag.SEEN],
            headers_fetched_at=datetime.now(UTC),
        )
        msg_unread = Message(
            message_id="<unread@example.com>",
            conv_id="conv_unread",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="b@example.com")},
            date=datetime.now(UTC),
            flags=[],
            headers_fetched_at=datetime.now(UTC),
        )

        cache.store_message(msg_read)
        cache.store_message(msg_unread)

        unread_convs = cache.list_conversations(
            account="test", folder="INBOX", unread_only=True
        )
        assert len(unread_convs) == 1
        assert unread_convs[0].conv_id == "conv_unread"


class TestCacheSearch:
    def test_search_by_subject(self, cache, sample_message):
        cache.store_message(sample_message)

        results = cache.search("Test Subject")
        assert len(results) == 1
        assert results[0].message_id == sample_message.message_id

    def test_search_by_body(self, cache, sample_message):
        cache.store_message(sample_message)

        results = cache.search("body text")
        assert len(results) == 1

    def test_search_by_sender(self, cache, sample_message):
        cache.store_message(sample_message)

        results = cache.search("sender@example.com")
        assert len(results) == 1

    def test_search_no_results(self, cache, sample_message):
        cache.store_message(sample_message)

        results = cache.search("nonexistent query")
        assert len(results) == 0

    def test_search_with_account_filter(self, cache):
        msg1 = Message(
            message_id="<msg1@example.com>",
            conv_id="conv1",
            account="account1",
            folder="INBOX",
            **{"from": Address(addr="a@example.com")},
            date=datetime.now(UTC),
            subject="Common keyword",
            headers_fetched_at=datetime.now(UTC),
        )
        msg2 = Message(
            message_id="<msg2@example.com>",
            conv_id="conv2",
            account="account2",
            folder="INBOX",
            **{"from": Address(addr="b@example.com")},
            date=datetime.now(UTC),
            subject="Common keyword",
            headers_fetched_at=datetime.now(UTC),
        )

        cache.store_message(msg1)
        cache.store_message(msg2)

        results = cache.search("keyword", account="account1")
        assert len(results) == 1
        assert results[0].account == "account1"


class TestCacheFlags:
    def test_update_flags(self, cache, sample_message):
        cache.store_message(sample_message)

        new_flags = [MessageFlag.SEEN, MessageFlag.FLAGGED]
        cache.update_flags(sample_message.message_id, new_flags)

        retrieved = cache.get_message(sample_message.message_id)
        assert MessageFlag.FLAGGED in retrieved.flags
        assert MessageFlag.SEEN in retrieved.flags


class TestCacheBody:
    def test_update_body(self, cache):
        msg = Message(
            message_id="<test@example.com>",
            conv_id="conv1",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="a@example.com")},
            date=datetime.now(UTC),
            body_text=None,  # Body not fetched yet
            headers_fetched_at=datetime.now(UTC),
        )
        cache.store_message(msg)

        cache.update_body(msg.message_id, "New body text", "<p>New body</p>")

        retrieved = cache.get_message(msg.message_id)
        assert retrieved.body_text == "New body text"
        assert retrieved.body_html == "<p>New body</p>"
        assert retrieved.body_fetched_at is not None


class TestCacheFreshness:
    def test_is_fresh_true(self, cache, sample_message):
        cache.store_message(sample_message)

        assert cache.is_fresh(sample_message.message_id, freshness_minutes=5) is True

    def test_is_fresh_false_for_nonexistent(self, cache):
        assert cache.is_fresh("<nonexistent@example.com>", freshness_minutes=5) is False


class TestCachePruning:
    def test_prune_old_messages(self, cache):
        old_msg = Message(
            message_id="<old@example.com>",
            conv_id="conv_old",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="a@example.com")},
            date=datetime.now(UTC) - timedelta(days=10),
            headers_fetched_at=datetime.now(UTC),
        )
        new_msg = Message(
            message_id="<new@example.com>",
            conv_id="conv_new",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="b@example.com")},
            date=datetime.now(UTC),
            headers_fetched_at=datetime.now(UTC),
        )

        cache.store_message(old_msg)
        cache.store_message(new_msg)

        deleted = cache.prune_old_messages(window_days=7)
        assert deleted == 1

        assert cache.get_message("<old@example.com>") is None
        assert cache.get_message("<new@example.com>") is not None


class TestCacheStats:
    def test_get_stats(self, cache, sample_message):
        cache.store_message(sample_message)

        stats = cache.get_stats()
        assert stats.message_count == 1
        assert stats.conversation_count == 1
        assert stats.cache_size_bytes > 0


class TestCacheClear:
    def test_clear(self, cache, sample_message):
        cache.store_message(sample_message)
        cache.clear()

        assert cache.get_message(sample_message.message_id) is None
        stats = cache.get_stats()
        assert stats.message_count == 0


class TestPrefixMatching:
    """Tests for conversation ID prefix matching."""

    def test_find_conversations_by_prefix_single_match(self, cache):
        """Prefix that matches one conversation returns that conversation."""
        msg = Message(
            message_id="<test@example.com>",
            conv_id="abc123def456",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime(2025, 1, 1, 10, 0, 0),
            subject="Test Subject",
            body_text="Body text",
            flags=[MessageFlag.SEEN],
            headers_fetched_at=datetime.now(UTC),
        )
        cache.store_message(msg)

        matches = cache.find_conversations_by_prefix("abc")
        assert len(matches) == 1
        assert matches[0].conv_id == "abc123def456"
        assert matches[0].subject == "Test Subject"

    def test_find_conversations_by_prefix_multiple_matches(self, cache):
        """Prefix that matches multiple conversations returns all of them."""
        # Create two conversations with similar prefixes
        msg1 = Message(
            message_id="<msg1@example.com>",
            conv_id="abc123xxx",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime(2025, 1, 1, 10, 0, 0),
            subject="First Subject",
            flags=[],
            headers_fetched_at=datetime.now(UTC),
        )
        msg2 = Message(
            message_id="<msg2@example.com>",
            conv_id="abc456yyy",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="bob@example.com")},
            date=datetime(2025, 1, 2, 10, 0, 0),
            subject="Second Subject",
            flags=[MessageFlag.SEEN],
            headers_fetched_at=datetime.now(UTC),
        )

        cache.store_message(msg1)
        cache.store_message(msg2)

        # Prefix "abc" matches both
        matches = cache.find_conversations_by_prefix("abc")
        assert len(matches) == 2
        # Should be sorted by latest date descending
        assert matches[0].conv_id == "abc456yyy"  # newer
        assert matches[1].conv_id == "abc123xxx"  # older

    def test_find_conversations_by_prefix_no_matches(self, cache):
        """Prefix that matches no conversations returns empty list."""
        msg = Message(
            message_id="<test@example.com>",
            conv_id="abc123",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime.now(UTC),
            flags=[],
            headers_fetched_at=datetime.now(UTC),
        )
        cache.store_message(msg)

        matches = cache.find_conversations_by_prefix("xyz")
        assert len(matches) == 0

    def test_find_conversations_by_prefix_short_prefix(self, cache):
        """Even single-character prefixes work."""
        msg1 = Message(
            message_id="<msg1@example.com>",
            conv_id="a12345",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime(2025, 1, 1, 10, 0, 0),
            flags=[],
            headers_fetched_at=datetime.now(UTC),
        )
        msg2 = Message(
            message_id="<msg2@example.com>",
            conv_id="b67890",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="bob@example.com")},
            date=datetime(2025, 1, 2, 10, 0, 0),
            flags=[],
            headers_fetched_at=datetime.now(UTC),
        )

        cache.store_message(msg1)
        cache.store_message(msg2)

        # Single char prefix
        matches = cache.find_conversations_by_prefix("a")
        assert len(matches) == 1
        assert matches[0].conv_id == "a12345"

    def test_get_conversation_unique_prefix(self, cache):
        """get_conversation returns conversation for unique prefix."""
        msg = Message(
            message_id="<test@example.com>",
            conv_id="unique123abc",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime(2025, 1, 1, 10, 0, 0),
            subject="Test Subject",
            body_text="Body text",
            flags=[],
            headers_fetched_at=datetime.now(UTC),
        )
        cache.store_message(msg)

        # Unique prefix returns the conversation
        conv = cache.get_conversation("unique")
        assert conv is not None
        assert conv.conv_id == "unique123abc"
        assert conv.subject == "Test Subject"

    def test_get_conversation_ambiguous_prefix_returns_none(self, cache):
        """get_conversation returns None for ambiguous prefix."""
        msg1 = Message(
            message_id="<msg1@example.com>",
            conv_id="ambig123",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime(2025, 1, 1, 10, 0, 0),
            flags=[],
            headers_fetched_at=datetime.now(UTC),
        )
        msg2 = Message(
            message_id="<msg2@example.com>",
            conv_id="ambig456",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="bob@example.com")},
            date=datetime(2025, 1, 2, 10, 0, 0),
            flags=[],
            headers_fetched_at=datetime.now(UTC),
        )

        cache.store_message(msg1)
        cache.store_message(msg2)

        # Ambiguous prefix returns None
        conv = cache.get_conversation("ambig")
        assert conv is None

        # But more specific prefix works
        conv1 = cache.get_conversation("ambig1")
        assert conv1 is not None
        assert conv1.conv_id == "ambig123"

        conv2 = cache.get_conversation("ambig4")
        assert conv2 is not None
        assert conv2.conv_id == "ambig456"

    def test_get_conversation_exact_match(self, cache):
        """get_conversation exact match takes precedence."""
        msg = Message(
            message_id="<test@example.com>",
            conv_id="exact",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime(2025, 1, 1, 10, 0, 0),
            subject="Exact Match",
            flags=[],
            headers_fetched_at=datetime.now(UTC),
        )
        cache.store_message(msg)

        # Exact match works
        conv = cache.get_conversation("exact")
        assert conv is not None
        assert conv.conv_id == "exact"

    def test_get_conversation_no_match(self, cache):
        """get_conversation returns None for no match."""
        msg = Message(
            message_id="<test@example.com>",
            conv_id="abc123",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime.now(UTC),
            flags=[],
            headers_fetched_at=datetime.now(UTC),
        )
        cache.store_message(msg)

        conv = cache.get_conversation("xyz")
        assert conv is None

    def test_find_conversations_includes_summary_fields(self, cache):
        """find_conversations_by_prefix returns complete summary data."""
        msg1 = Message(
            message_id="<msg1@example.com>",
            conv_id="conv_test",
            account="test_account",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime(2025, 1, 1, 10, 0, 0),
            subject="Original Subject",
            body_text="First message body",
            flags=[],
            headers_fetched_at=datetime.now(UTC),
        )
        msg2 = Message(
            message_id="<msg2@example.com>",
            conv_id="conv_test",
            account="test_account",
            folder="INBOX",
            **{"from": Address(addr="bob@example.com")},
            to=[Address(addr="alice@example.com")],
            date=datetime(2025, 1, 2, 10, 0, 0),
            subject="Re: Original Subject",
            body_text="Reply body",
            flags=[MessageFlag.SEEN],
            headers_fetched_at=datetime.now(UTC),
        )

        cache.store_message(msg1)
        cache.store_message(msg2)

        matches = cache.find_conversations_by_prefix("conv")
        assert len(matches) == 1

        summary = matches[0]
        assert summary.conv_id == "conv_test"
        assert summary.message_count == 2
        assert summary.unread_count == 1  # msg1 is unread
        assert summary.account == "test_account"
        assert "alice@example.com" in summary.participants
        assert "bob@example.com" in summary.participants
