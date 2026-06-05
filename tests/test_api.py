"""Tests for ClerkAPI."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from clerk.api import ClerkAPI, get_api, html_to_text
from clerk.cache import Cache
from clerk.config import AccountConfig, ClerkConfig, FromAddress, ImapConfig, SmtpConfig
from clerk.drafts import DraftManager
from clerk.models import Address, Message, MessageFlag


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
    monkeypatch.setattr("clerk.drafts.get_cache", lambda: cache)
    return DraftManager()


@pytest.fixture
def sample_message():
    """Create a sample message."""
    return Message(
        uid=1,
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
        assert result == "Line 1\nLine 2\nLine 3"

    def test_decodes_entities(self):
        result = html_to_text("A &amp; B &lt; C &gt; D")
        assert result == "A & B < C > D"

    def test_strips_style_blocks(self):
        result = html_to_text("<style>body{color:red}</style>Hello")
        assert result == "Hello"

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

    def test_nested_blocks_separate_lines(self):
        html = "<div><p>First para</p><p>Second para</p></div>"
        result = html_to_text(html)
        assert "First para" in result
        assert "Second para" in result
        assert "First paraSecond para" not in result


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


class TestSendPolicyPersistent:
    """Rate limit is computed from send_log rows, so it survives restarts."""

    def test_rate_limit_from_send_log(self, api, monkeypatch):
        """check_send_allowed counts rows in send_log, not an in-memory dict."""
        from clerk.models import Draft

        draft = Draft(
            draft_id="d1",
            account="test",
            to=[Address(addr="a@example.com")],
            subject="s",
            body_text="b",
        )

        # With rate_limit=2, three prior sends in the log should block.
        monkeypatch.setattr(api.config.send, "rate_limit", 2)
        for i in range(3):
            api.cache.log_send(
                account="test",
                to=draft.to,
                cc=[],
                bcc=[],
                subject=f"s{i}",
                message_id=f"<m{i}@x>",
            )

        allowed, error = api.check_send_allowed(draft, "test")
        assert allowed is False
        assert "rate limit" in (error or "").lower()

    def test_rate_limit_ignores_old_sends(self, api, monkeypatch):
        """Sends older than 1 hour should not count toward the limit."""
        import json
        import sqlite3
        from datetime import timedelta

        from clerk.models import Draft

        draft = Draft(
            draft_id="d1",
            account="test",
            to=[Address(addr="a@example.com")],
            subject="s",
            body_text="b",
        )
        monkeypatch.setattr(api.config.send, "rate_limit", 2)

        # Insert three sends with timestamps two hours ago.
        old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        with sqlite3.connect(api.cache.db_path) as conn:
            for i in range(3):
                conn.execute(
                    "INSERT INTO send_log (timestamp, account, to_json, cc_json, bcc_json, subject, message_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        old,
                        "test",
                        json.dumps([{"addr": "a@example.com", "name": ""}]),
                        "[]",
                        "[]",
                        f"s{i}",
                        f"<m{i}@x>",
                    ),
                )

        allowed, error = api.check_send_allowed(draft, "test")
        assert allowed is True, error


class TestSetFlag:
    """set_flag is the single flag-mutation entry point; wrappers delegate here."""

    def test_mark_read_calls_set_flag(self, api, cache, sample_message, monkeypatch):
        from clerk.models import MessageFlag

        cache.store_message(sample_message)
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)

        api.mark_read("<msg123@example.com>")

        args, _ = mock_client.add_flags.call_args
        # add_flags(folder, message_id, [flag]) — flag should be SEEN.
        assert MessageFlag.SEEN in args[2]

    def test_unflag_removes_flagged(self, api, cache, sample_message, monkeypatch):
        from clerk.models import MessageFlag

        sample_message.flags = [MessageFlag.FLAGGED]
        cache.store_message(sample_message)
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)

        api.unflag_message("<msg123@example.com>")

        args, _ = mock_client.remove_flags.call_args
        assert MessageFlag.FLAGGED in args[2]

    def test_cache_write_failure_does_not_raise(self, api, cache, sample_message, monkeypatch):
        """Server-first: IMAP call succeeded, cache failure is logged not raised."""
        from clerk.models import MessageFlag

        cache.store_message(sample_message)
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)
        monkeypatch.setattr(
            cache, "update_flags", MagicMock(side_effect=RuntimeError("disk full"))
        )

        # Must not raise — the IMAP side already succeeded.
        api.set_flag("<msg123@example.com>", MessageFlag.SEEN, True)


class TestEnsureBody:
    """M1: _ensure_body must not trust body_text=None if body_fetched_at is recent."""

    def test_refetches_when_body_text_is_none_even_if_fresh(
        self, api, cache, sample_message, monkeypatch
    ):
        """A prior fetch that stored body_text=None must trigger a re-fetch."""
        # Simulate the bug condition: body_fetched_at is recent, body_text is None.
        sample_message.body_text = None
        sample_message.body_html = None
        sample_message.body_fetched_at = datetime.now(UTC)
        cache.store_message(sample_message)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.fetch_message_body.return_value = ("recovered body", None)
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)

        msg = api.get_message("<msg123@example.com>")

        assert msg is not None
        assert msg.body_text == "recovered body"
        mock_client.fetch_message_body.assert_called_once()


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
    """sync_folder pages new UIDs ascending, advances the watermark per chunk,
    and dead-letters parse failures so they are retried, not lost."""

    def _client(self, monkeypatch, new_uids, fetch_result):
        """fetch_result is a (messages, failed) tuple, or a callable(uids)->tuple."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.search_uids.return_value = new_uids
        if callable(fetch_result):
            mock_client.fetch_uids.side_effect = (
                lambda folder, uids, fetch_bodies=True: fetch_result(list(uids))
            )
        else:
            mock_client.fetch_uids.return_value = fetch_result
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)
        return mock_client

    def _msg(self, uid):
        return Message(
            uid=uid,
            message_id=f"<m{uid}@x>",
            conv_id=f"c{uid}",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="a@x.com")},
            to=[Address(addr="test@example.com")],
            subject="s",
            date=datetime.now(UTC),
            headers_fetched_at=datetime.now(UTC),
        )

    def test_no_new_messages_returns_zero(self, api, monkeypatch):
        self._client(monkeypatch, [], ([], []))
        result = api.sync_folder(account="test", folder="INBOX")
        assert result["synced"] == 0
        assert result["account"] == "test"
        assert result["folder"] == "INBOX"

    def test_sync_stores_and_advances_watermark(self, api, cache, monkeypatch):
        m = self._msg(100)
        self._client(monkeypatch, [100], ([m], []))
        result = api.sync_folder(account="test", folder="INBOX")
        assert result["synced"] == 1
        assert cache.get_sync_state("test", "INBOX")["last_uid"] == 100

    def test_search_uses_existing_watermark(self, api, cache, monkeypatch):
        cache.set_sync_state("test", "INBOX", 50)
        mock_client = self._client(monkeypatch, [], ([], []))
        api.sync_folder(account="test", folder="INBOX")
        mock_client.search_uids.assert_called_once_with("INBOX", 50)

    def test_full_sync_ignores_watermark(self, api, cache, monkeypatch):
        cache.set_sync_state("test", "INBOX", 50)
        mock_client = self._client(monkeypatch, [], ([], []))
        api.sync_folder(account="test", folder="INBOX", full=True)
        mock_client.search_uids.assert_called_once_with("INBOX", 0)

    def test_no_new_messages_keeps_watermark(self, api, cache, monkeypatch):
        cache.set_sync_state("test", "INBOX", 50)
        self._client(monkeypatch, [], ([], []))
        api.sync_folder(account="test", folder="INBOX")
        assert cache.get_sync_state("test", "INBOX")["last_uid"] == 50

    def test_paging_no_200_cap(self, api, cache, monkeypatch):
        uids = list(range(1, 251))  # 250 messages, above the old 200 cap
        msgs = {u: self._msg(u) for u in uids}
        self._client(monkeypatch, uids, lambda chunk: ([msgs[u] for u in chunk], []))
        result = api.sync_folder(account="test", folder="INBOX")
        assert result["synced"] == 250
        assert cache.get_stats().message_count == 250
        assert cache.get_sync_state("test", "INBOX")["last_uid"] == 250

    def test_fetch_is_chunked(self, api, cache, monkeypatch):
        monkeypatch.setattr(api.config.cache, "sync_chunk_size", 2)
        uids = [1, 2, 3, 4, 5]
        msgs = {u: self._msg(u) for u in uids}
        mock_client = self._client(
            monkeypatch, uids, lambda chunk: ([msgs[u] for u in chunk], [])
        )
        api.sync_folder(account="test", folder="INBOX")
        assert mock_client.fetch_uids.call_count == 3  # chunks of 2: (2,2,1)

    def test_parse_failure_recorded_and_watermark_advances(self, api, cache, monkeypatch):
        self._client(monkeypatch, [10], ([], [10]))  # uid 10 fails to parse
        result = api.sync_folder(account="test", folder="INBOX")
        assert result["synced"] == 0
        assert 10 in cache.get_deadletter_uids("test", "INBOX")
        # The bad message does not block sync: the watermark still advances.
        assert cache.get_sync_state("test", "INBOX")["last_uid"] == 10

    def test_deadletter_retried_and_cleared_on_success(self, api, cache, monkeypatch):
        cache.record_deadletter("test", "INBOX", 10)
        m = self._msg(10)
        self._client(monkeypatch, [], ([m], []))  # no new uids; dl retry succeeds
        result = api.sync_folder(account="test", folder="INBOX")
        assert result["synced"] == 1
        assert cache.get_deadletter_uids("test", "INBOX") == []
        assert cache.get_message("<m10@x>") is not None


class TestEagerBodies:
    """Sync fetches full bodies and makes them searchable (FTS completeness)."""

    def _mock_client(self, monkeypatch, messages, highest_uid):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.search_uids.return_value = [m.uid for m in messages]
        mock_client.fetch_uids.return_value = (messages, [])
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)
        return mock_client

    def _msg(self, uid, mid, **kw):
        return Message(
            uid=uid,
            message_id=mid,
            conv_id=f"conv{uid}",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com", name="Alice")},
            to=[Address(addr="test@example.com")],
            date=datetime.now(UTC),
            subject="Subject",
            headers_fetched_at=datetime.now(UTC),
            body_fetched_at=datetime.now(UTC),
            **kw,
        )

    def test_sync_requests_bodies_eagerly(self, api, monkeypatch):
        msg = self._msg(40, "<e0@x>", body_text="hi")
        mock_client = self._mock_client(monkeypatch, [msg], 40)
        api.sync_folder(account="test", folder="INBOX")
        assert mock_client.fetch_uids.called
        _, kwargs = mock_client.fetch_uids.call_args
        assert kwargs.get("fetch_bodies") is True

    def test_synced_body_is_full_text_searchable(self, api, cache, monkeypatch):
        msg = self._msg(40, "<e1@x>", body_text="the quarterly pineapple report")
        self._mock_client(monkeypatch, [msg], 40)
        api.sync_folder(account="test", folder="INBOX")
        rows = cache.execute_readonly_sql(
            "SELECT m.message_id FROM messages_fts f "
            "JOIN messages m ON m.rowid = f.rowid "
            "WHERE messages_fts MATCH 'pineapple'"
        )
        assert any(r["message_id"] == "<e1@x>" for r in rows)

    def test_html_only_body_gets_text_for_search(self, api, cache, monkeypatch):
        msg = self._msg(41, "<e2@x>", body_text=None, body_html="<p>secret mango harvest</p>")
        self._mock_client(monkeypatch, [msg], 41)
        api.sync_folder(account="test", folder="INBOX")
        stored = cache.get_message("<e2@x>")
        assert stored is not None
        assert "mango" in (stored.body_text or "")
        rows = cache.execute_readonly_sql(
            "SELECT m.message_id FROM messages_fts f "
            "JOIN messages m ON m.rowid = f.rowid "
            "WHERE messages_fts MATCH 'mango'"
        )
        assert any(r["message_id"] == "<e2@x>" for r in rows)

    def test_oversized_body_is_skipped(self, api, cache, monkeypatch):
        big = "x" * 2_000_000  # exceeds the 1_000_000 default cap
        msg = self._msg(42, "<e3@x>", body_text=big)
        self._mock_client(monkeypatch, [msg], 42)
        api.sync_folder(account="test", folder="INBOX")
        stored = cache.get_message("<e3@x>")
        assert stored is not None
        assert stored.body_text is None
        with cache._connect() as conn:
            row = conn.execute(
                "SELECT body_skipped FROM messages WHERE message_id='<e3@x>'"
            ).fetchone()
        assert row["body_skipped"] == 1

    def test_cap_measures_bytes_not_chars(self, api, cache, monkeypatch):
        # 4-byte-per-char content: char count below cap, byte count above it.
        monkeypatch.setattr(api.config.cache, "body_max_bytes", 1000)
        body = "\U0001F600" * 300  # 300 chars, 1200 UTF-8 bytes
        msg = self._msg(43, "<e4@x>", body_text=body)
        self._mock_client(monkeypatch, [msg], 43)
        api.sync_folder(account="test", folder="INBOX")
        with cache._connect() as conn:
            row = conn.execute(
                "SELECT body_skipped FROM messages WHERE message_id='<e4@x>'"
            ).fetchone()
        assert row["body_skipped"] == 1


class TestReconcile:
    """sync_folder reconciles flags and expunges for the recent cached window."""

    def _store(self, cache, uid, flags=None):
        cache.store_message(
            Message(
                uid=uid,
                message_id=f"<r{uid}@x>",
                conv_id=f"rc{uid}",
                account="test",
                folder="INBOX",
                **{"from": Address(addr="a@x.com")},
                to=[Address(addr="t@x.com")],
                subject="s",
                date=datetime.now(UTC),
                flags=flags or [],
                headers_fetched_at=datetime.now(UTC),
            )
        )

    def _client(self, monkeypatch, flags_by_uid):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.search_uids.return_value = []  # no new mail this sync
        mock_client.fetch_flags.return_value = flags_by_uid
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)
        return mock_client

    def test_reconcile_updates_changed_flags(self, api, cache, monkeypatch):
        self._store(cache, 5, flags=[])  # unread in cache
        self._client(monkeypatch, {5: [MessageFlag.SEEN]})  # read on the server
        api.sync_folder(account="test", folder="INBOX")
        assert MessageFlag.SEEN in cache.get_message("<r5@x>").flags

    def test_reconcile_deletes_expunged_ghost(self, api, cache, monkeypatch):
        self._store(cache, 6)
        self._client(monkeypatch, {})  # server returns nothing -> uid 6 expunged
        api.sync_folder(account="test", folder="INBOX")
        assert cache.get_message("<r6@x>") is None

    def test_reconcile_bounded_by_window(self, api, cache, monkeypatch):
        for u in (1, 2, 3):
            self._store(cache, u)
        monkeypatch.setattr(api.config.cache, "reconcile_window", 2)
        mock_client = self._client(monkeypatch, {2: [], 3: []})
        api.sync_folder(account="test", folder="INBOX")
        args, _ = mock_client.fetch_flags.call_args
        assert set(args[1]) == {2, 3}  # only the 2 most-recent UIDs

    def test_reconcile_disabled_when_window_zero(self, api, cache, monkeypatch):
        self._store(cache, 8)
        monkeypatch.setattr(api.config.cache, "reconcile_window", 0)
        mock_client = self._client(monkeypatch, {})
        api.sync_folder(account="test", folder="INBOX")
        mock_client.fetch_flags.assert_not_called()

    def test_reconcile_skips_unchanged_flags(self, api, cache, monkeypatch):
        self._store(cache, 9, flags=[MessageFlag.SEEN])
        self._client(monkeypatch, {9: [MessageFlag.SEEN]})  # unchanged
        spy = MagicMock()
        monkeypatch.setattr(cache, "update_flags_by_uid", spy)
        api.sync_folder(account="test", folder="INBOX")
        spy.assert_not_called()


class TestPrune:
    """Pruning is off by default; when enabled, sync drops messages older than window_days."""

    def _client(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.search_uids.return_value = []
        mock_client.fetch_flags.return_value = {}
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)
        return mock_client

    def _store_old(self, cache, uid, days_old):
        from datetime import timedelta

        cache.store_message(
            Message(
                uid=uid, message_id=f"<p{uid}@x>", conv_id=f"pc{uid}", account="test",
                folder="INBOX", **{"from": Address(addr="a@x.com")},
                to=[Address(addr="t@x.com")], subject="s",
                date=datetime.now(UTC) - timedelta(days=days_old),
                headers_fetched_at=datetime.now(UTC),
            )
        )

    def test_prune_disabled_keeps_old_messages(self, api, cache, monkeypatch):
        monkeypatch.setattr(api.config.cache, "reconcile_window", 0)  # isolate prune
        self._store_old(cache, 90, days_old=365)
        self._client(monkeypatch)
        result = api.sync_folder(account="test", folder="INBOX")
        assert result["pruned"] == 0
        assert cache.get_message("<p90@x>") is not None

    def test_prune_enabled_drops_old_messages(self, api, cache, monkeypatch):
        monkeypatch.setattr(api.config.cache, "reconcile_window", 0)  # isolate prune
        monkeypatch.setattr(api.config.cache, "prune_enabled", True)
        monkeypatch.setattr(api.config.cache, "window_days", 30)
        self._store_old(cache, 91, days_old=365)  # older than the 30-day window
        self._client(monkeypatch)
        result = api.sync_folder(account="test", folder="INBOX")
        assert result["pruned"] >= 1
        assert cache.get_message("<p91@x>") is None


class TestSendReservation:
    """Sends are reserved before SMTP so the rate limit cannot be bypassed by a
    finalize/audit-log failure, and failed sends do not count."""

    def _fake_smtp(self, monkeypatch, success):
        from clerk.models import SendResult

        class FakeSmtp:
            def __init__(self, name, config):
                pass

            async def send_async(self, draft):
                return SendResult(
                    success=success,
                    message_id="<sent@x>" if success else None,
                    error=None if success else "smtp boom",
                )

        monkeypatch.setattr("clerk.api.SmtpClient", FakeSmtp)

    def _count(self, cache):
        from datetime import timedelta

        return cache.count_sends_since("test", datetime.now(UTC) - timedelta(hours=1))

    def test_successful_send_counts_as_sent(self, api, cache, monkeypatch):
        self._fake_smtp(monkeypatch, success=True)
        d = api.create_draft(to=["x@example.com"], subject="s", body="b")
        result = api.send_draft(d.draft_id)
        assert result.success
        assert self._count(cache) == 1

    def test_failed_send_not_counted(self, api, cache, monkeypatch):
        self._fake_smtp(monkeypatch, success=False)
        d = api.create_draft(to=["x@example.com"], subject="s", body="b")
        result = api.send_draft(d.draft_id)
        assert not result.success
        assert self._count(cache) == 0

    def test_send_counted_even_if_finalize_fails(self, api, cache, monkeypatch):
        self._fake_smtp(monkeypatch, success=True)
        monkeypatch.setattr(
            cache, "finalize_send", MagicMock(side_effect=RuntimeError("disk full"))
        )
        d = api.create_draft(to=["x@example.com"], subject="s", body="b")
        result = api.send_draft(d.draft_id)
        assert result.success
        assert self._count(cache) == 1  # the pending reservation counts

    def test_audit_failure_does_not_uncount_send(self, api, cache, monkeypatch):
        # If the post-send audit write fails, a SUCCESSFUL send must still count.
        # Old code logged after send (best-effort log_send); a failed log meant
        # the send was not counted (the bug). New code reserves a pending row
        # before SMTP, so the count holds even when finalize/log fails.
        self._fake_smtp(monkeypatch, success=True)
        monkeypatch.setattr(
            cache, "finalize_send", MagicMock(side_effect=RuntimeError("disk full"))
        )
        monkeypatch.setattr(
            cache, "log_send", MagicMock(side_effect=RuntimeError("disk full"))
        )
        d = api.create_draft(to=["x@example.com"], subject="s", body="b")
        result = api.send_draft(d.draft_id)
        assert result.success
        assert self._count(cache) == 1


class TestStatusEnrichment:
    """clerk_status (via get_status) reports staleness and a cache summary."""

    def _mock_imap(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.list_folders.return_value = []
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)

    def test_status_reports_fresh_after_sync(self, api, cache, monkeypatch):
        self._mock_imap(monkeypatch)
        cache.mark_inbox_synced("test")
        status = api.get_status()
        assert status["accounts"]["test"]["last_sync"] is not None
        assert status["accounts"]["test"]["stale"] is False

    def test_status_reports_stale_when_old(self, api, cache, monkeypatch):
        from datetime import timedelta

        self._mock_imap(monkeypatch)
        old = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        cache.set_meta("inbox_sync_test", old)
        status = api.get_status()
        assert status["accounts"]["test"]["stale"] is True

    def test_status_includes_cache_summary(self, api, cache, monkeypatch):
        self._mock_imap(monkeypatch)
        m = Message(
            uid=80, message_id="<c80@x>", conv_id="cc80", account="test", folder="INBOX",
            **{"from": Address(addr="a@x.com")}, to=[Address(addr="t@x.com")],
            subject="s", date=datetime.now(UTC), body_text="hi",
            headers_fetched_at=datetime.now(UTC),
        )
        cache.store_message(m)
        status = api.get_status()
        assert status["cache"]["message_count"] == 1
        assert "body_skipped_count" in status["cache"]
