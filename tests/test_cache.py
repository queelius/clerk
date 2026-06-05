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
        uid=1,
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
            uid=1,
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
            uid=2,
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
                uid=i + 1,
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
            uid=1,
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
            uid=2,
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
            uid=1,
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
            uid=1,
            message_id="<old@example.com>",
            conv_id="conv_old",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="a@example.com")},
            date=datetime.now(UTC) - timedelta(days=10),
            headers_fetched_at=datetime.now(UTC),
        )
        new_msg = Message(
            uid=2,
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
            uid=1,
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
            uid=1,
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
            uid=2,
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
            uid=1,
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
            uid=1,
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
            uid=2,
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
        # Conv IDs are always 12 hex chars in production.
        msg = Message(
            uid=1,
            message_id="<test@example.com>",
            conv_id="abcdef123456",
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
        conv = cache.get_conversation("abc")
        assert conv is not None
        assert conv.conv_id == "abcdef123456"
        assert conv.subject == "Test Subject"

    def test_get_conversation_ambiguous_prefix_returns_none(self, cache):
        """get_conversation returns None for ambiguous prefix."""
        msg1 = Message(
            uid=1,
            message_id="<msg1@example.com>",
            conv_id="aaaabbb11111",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime(2025, 1, 1, 10, 0, 0),
            flags=[],
            headers_fetched_at=datetime.now(UTC),
        )
        msg2 = Message(
            uid=2,
            message_id="<msg2@example.com>",
            conv_id="aaaabbb22222",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="bob@example.com")},
            date=datetime(2025, 1, 2, 10, 0, 0),
            flags=[],
            headers_fetched_at=datetime.now(UTC),
        )

        cache.store_message(msg1)
        cache.store_message(msg2)

        # Ambiguous prefix returns None (multiple matches)
        conv = cache.get_conversation("aaaa")
        assert conv is None

        # But more specific prefix works
        conv1 = cache.get_conversation("aaaabbb1")
        assert conv1 is not None
        assert conv1.conv_id == "aaaabbb11111"

        conv2 = cache.get_conversation("aaaabbb2")
        assert conv2 is not None
        assert conv2.conv_id == "aaaabbb22222"

    def test_prefix_rejects_non_hex(self, cache):
        """Caller-supplied prefixes with non-hex chars (e.g. SQL wildcards) return empty."""
        msg = Message(
            uid=1,
            message_id="<test@example.com>",
            conv_id="abcdef123456",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com")},
            date=datetime(2025, 1, 1, 10, 0, 0),
            flags=[],
            headers_fetched_at=datetime.now(UTC),
        )
        cache.store_message(msg)

        # A bare '%' would match everything via LIKE; must be rejected.
        assert cache.find_conversations_by_prefix("%") == []
        assert cache.find_conversations_by_prefix("_") == []
        assert cache.find_conversations_by_prefix("\\") == []
        # Non-hex chars (g-z) cannot be part of a SHA256 prefix.
        assert cache.find_conversations_by_prefix("xyz") == []

    def test_get_conversation_exact_match(self, cache):
        """get_conversation exact match takes precedence."""
        msg = Message(
            uid=1,
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
            uid=1,
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
            uid=1,
            message_id="<msg1@example.com>",
            conv_id="ccc0bbb13579",
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
            uid=2,
            message_id="<msg2@example.com>",
            conv_id="ccc0bbb13579",
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

        matches = cache.find_conversations_by_prefix("ccc0")
        assert len(matches) == 1

        summary = matches[0]
        assert summary.conv_id == "ccc0bbb13579"
        assert summary.message_count == 2
        assert summary.unread_count == 1  # msg1 is unread
        assert summary.account == "test_account"
        assert "alice@example.com" in summary.participants
        assert "bob@example.com" in summary.participants


class TestSyncState:
    def test_get_sync_state_returns_none_for_unknown(self, tmp_path):
        cache = Cache(tmp_path / "test.db")
        state = cache.get_sync_state("test", "INBOX")
        assert state is None

    def test_set_and_get_sync_state(self, tmp_path):
        cache = Cache(tmp_path / "test.db")
        cache.set_sync_state("test", "INBOX", last_uid=42)
        state = cache.get_sync_state("test", "INBOX")
        assert state is not None
        assert state["last_uid"] == 42
        assert state["account"] == "test"
        assert state["folder"] == "INBOX"
        assert "last_sync_utc" in state

    def test_update_sync_state(self, tmp_path):
        cache = Cache(tmp_path / "test.db")
        cache.set_sync_state("test", "INBOX", last_uid=10)
        cache.set_sync_state("test", "INBOX", last_uid=42)
        state = cache.get_sync_state("test", "INBOX")
        assert state["last_uid"] == 42

    def test_sync_state_per_folder(self, tmp_path):
        cache = Cache(tmp_path / "test.db")
        cache.set_sync_state("test", "INBOX", last_uid=10)
        cache.set_sync_state("test", "Sent", last_uid=20)
        assert cache.get_sync_state("test", "INBOX")["last_uid"] == 10
        assert cache.get_sync_state("test", "Sent")["last_uid"] == 20


# ---------------------------------------------------------------------------
# Task 4: schema v2 tests - UID identity, bitmask flags, body-preserving upsert
# ---------------------------------------------------------------------------


def _msg(uid, message_id, *, body_text=None, body_html=None, subject="Re: Budget",
         flags=None, conv_id="aa11bb22cc33", references=None, in_reply_to=None):
    return Message(
        uid=uid,
        message_id=message_id,
        conv_id=conv_id,
        account="acct",
        folder="INBOX",
        **{"from": Address(addr="boss@work.com", name="Boss")},
        date=datetime(2026, 1, 1, tzinfo=UTC),
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        flags=flags or [],
        references=references or [],
        in_reply_to=in_reply_to,
        headers_fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_store_and_get_roundtrip_with_uid_and_flags(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(10, "<m10@x>", body_text="hi", flags=[MessageFlag.SEEN]))
    got = cache.get_message("<m10@x>")
    assert got is not None
    assert got.uid == 10
    assert MessageFlag.SEEN in got.flags
    assert got.body_text == "hi"


def test_flags_stored_as_integer_bitmask(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(11, "<m11@x>", flags=[MessageFlag.SEEN, MessageFlag.FLAGGED]))
    with cache._connect() as conn:
        row = conn.execute("SELECT flags FROM messages WHERE uid = 11").fetchone()
    assert row["flags"] == 5  # SEEN(1) | FLAGGED(4)


def test_thread_subject_persisted_normalized(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(12, "<m12@x>", subject="Re: Fwd: Budget"))
    with cache._connect() as conn:
        row = conn.execute("SELECT thread_subject FROM messages WHERE uid = 12").fetchone()
    assert row["thread_subject"] == "Budget"


def test_header_resync_preserves_existing_body(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(13, "<m13@x>", body_text="full body"))
    cache.store_message(_msg(13, "<m13@x>", body_text=None, flags=[MessageFlag.SEEN]))
    got = cache.get_message("<m13@x>")
    assert got.body_text == "full body"
    assert MessageFlag.SEEN in got.flags


def test_wal_mode_enabled(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    with cache._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_fresh_db_has_user_version_2(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    with cache._connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# Task 6: bitmask unread predicate and canonical thread_subject in listings
# ---------------------------------------------------------------------------


def test_list_conversations_unread_uses_bitmask(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(20, "<u1@x>", conv_id="ff00ff00ff00", flags=[]))
    cache.store_message(_msg(21, "<u2@x>", conv_id="ee11ee11ee11",
                             flags=[MessageFlag.SEEN]))
    summaries = cache.list_conversations(account="acct", unread_only=True)
    conv_ids = {s.conv_id for s in summaries}
    assert "ff00ff00ff00" in conv_ids
    assert "ee11ee11ee11" not in conv_ids


def test_listing_subject_is_canonical(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(22, "<c1@x>", conv_id="abcabcabcabc", subject="Re: Budget"))
    summaries = cache.list_conversations(account="acct")
    match = next(s for s in summaries if s.conv_id == "abcabcabcabc")
    assert match.subject == "Budget"


def test_update_flags_writes_bitmask(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(23, "<f1@x>", flags=[]))
    cache.update_flags("<f1@x>", [MessageFlag.SEEN, MessageFlag.ANSWERED])
    with cache._connect() as conn:
        assert conn.execute("SELECT flags FROM messages WHERE uid = 23").fetchone()["flags"] == 3


def test_store_message_persists_body_skipped_true(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    msg = _msg(30, "<bs1@x>")
    msg.body_skipped = True
    cache.store_message(msg)
    with cache._connect() as conn:
        row = conn.execute(
            "SELECT body_skipped FROM messages WHERE account='acct' AND folder='INBOX' AND uid=30"
        ).fetchone()
    assert row["body_skipped"] == 1


def test_store_message_persists_body_skipped_false(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(31, "<bs2@x>"))
    with cache._connect() as conn:
        row = conn.execute(
            "SELECT body_skipped FROM messages WHERE account='acct' AND folder='INBOX' AND uid=31"
        ).fetchone()
    assert row["body_skipped"] == 0


def test_get_message_round_trips_body_skipped(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    msg = _msg(33, "<bsr@x>")
    msg.body_skipped = True
    cache.store_message(msg)
    got = cache.get_message("<bsr@x>")
    assert got is not None
    assert got.body_skipped is True


# ---------------------------------------------------------------------------
# Task 4: dead-letter set for parse failures
# ---------------------------------------------------------------------------


def test_record_and_get_deadletter(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    assert cache.get_deadletter_uids("acct", "INBOX") == []
    cache.record_deadletter("acct", "INBOX", 7)
    assert cache.get_deadletter_uids("acct", "INBOX") == [7]


def test_deadletter_ages_out_after_max_attempts(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    for _ in range(3):  # _DEADLETTER_MAX_ATTEMPTS
        cache.record_deadletter("acct", "INBOX", 9)
    assert 9 not in cache.get_deadletter_uids("acct", "INBOX")


def test_clear_deadletter(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.record_deadletter("acct", "INBOX", 5)
    cache.clear_deadletter("acct", "INBOX", 5)
    assert cache.get_deadletter_uids("acct", "INBOX") == []


# ---------------------------------------------------------------------------
# Task 2: UID-keyed cache helpers for reconciliation
# ---------------------------------------------------------------------------


def test_get_recent_uid_flags_returns_highest_uids(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(1, "<a1@x>", flags=[MessageFlag.SEEN]))
    cache.store_message(_msg(2, "<a2@x>", flags=[]))
    cache.store_message(_msg(3, "<a3@x>", flags=[MessageFlag.FLAGGED]))
    # window of 2 returns the two highest UIDs with their flag bitmasks
    recent = cache.get_recent_uid_flags("acct", "INBOX", 2)
    assert set(recent.keys()) == {2, 3}
    assert recent[3] == 4  # FLAGGED bit


def test_update_flags_by_uid(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(5, "<u5@x>", flags=[]))
    cache.update_flags_by_uid("acct", "INBOX", 5, [MessageFlag.SEEN])
    assert MessageFlag.SEEN in cache.get_message("<u5@x>").flags


def test_delete_by_uid(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(6, "<d6@x>"))
    cache.delete_by_uid("acct", "INBOX", 6)
    assert cache.get_message("<d6@x>") is None


def test_get_stats_counts_body_skipped(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    normal = _msg(70, "<n70@x>", body_text="hi")
    skipped = _msg(71, "<s71@x>")
    skipped.body_skipped = True
    cache.store_message(normal)
    cache.store_message(skipped)
    assert cache.get_stats().body_skipped_count == 1


def test_update_body_clears_body_skipped(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    skipped = _msg(72, "<s72@x>")
    skipped.body_skipped = True
    cache.store_message(skipped)
    cache.update_body("<s72@x>", "recovered text", None)
    got = cache.get_message("<s72@x>")
    assert got.body_text == "recovered text"
    assert got.body_skipped is False


# ---------------------------------------------------------------------------
# Task 3: get_last_sync
# ---------------------------------------------------------------------------


def test_get_last_sync_none_then_set(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    assert cache.get_last_sync("acct") is None
    cache.mark_inbox_synced("acct")
    assert cache.get_last_sync("acct") is not None


# ---------------------------------------------------------------------------
# Task 1: Pre-send reservation (pending/sent/failed) for the rate limiter
# ---------------------------------------------------------------------------


def test_reserve_send_counts_toward_limit(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    from datetime import timedelta

    hour_ago = datetime.now(UTC) - timedelta(hours=1)
    assert cache.count_sends_since("acct", hour_ago) == 0
    cache.reserve_send("acct", [Address(addr="x@y.com")], [], [], "subj")
    assert cache.count_sends_since("acct", hour_ago) == 1


def test_finalize_failed_not_counted(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    from datetime import timedelta

    hour_ago = datetime.now(UTC) - timedelta(hours=1)
    send_id = cache.reserve_send("acct", [Address(addr="x@y.com")], [], [], "subj")
    cache.finalize_send(send_id, "failed", None)
    assert cache.count_sends_since("acct", hour_ago) == 0


def test_finalize_sent_counts_and_records_message_id(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    from datetime import timedelta

    hour_ago = datetime.now(UTC) - timedelta(hours=1)
    send_id = cache.reserve_send("acct", [Address(addr="x@y.com")], [], [], "subj")
    cache.finalize_send(send_id, "sent", "<m@x>")
    assert cache.count_sends_since("acct", hour_ago) == 1
    with cache._connect() as conn:
        row = conn.execute(
            "SELECT status, message_id FROM send_log WHERE id = ?", (send_id,)
        ).fetchone()
    assert row["status"] == "sent"
    assert row["message_id"] == "<m@x>"
