"""Tests for draft management."""

from datetime import UTC, datetime

import pytest

from clerk.drafts import DraftManager, generate_draft_id
from clerk.models import Address


@pytest.fixture
def draft_manager(tmp_path, monkeypatch):
    """Create a draft manager with a temporary database."""
    # Patch get_data_dir to use temp directory
    monkeypatch.setattr("clerk.drafts.get_data_dir", lambda: tmp_path)

    # Create cache database schema and patch get_cache to return it
    from clerk.cache import Cache

    cache = Cache(tmp_path / "cache.db")
    monkeypatch.setattr("clerk.drafts.get_cache", lambda: cache)

    return DraftManager()


class TestGenerateDraftId:
    def test_generates_unique_ids(self):
        ids = {generate_draft_id() for _ in range(100)}
        assert len(ids) == 100  # All unique

    def test_format(self):
        draft_id = generate_draft_id()
        assert draft_id.startswith("draft_")
        assert len(draft_id) == 22  # "draft_" + 16 hex chars


class TestDraftManager:
    def test_create_draft(self, draft_manager):
        draft = draft_manager.create(
            account="test",
            to=[Address(addr="recipient@example.com")],
            subject="Test Subject",
            body_text="Hello World",
        )

        assert draft.draft_id.startswith("draft_")
        assert draft.account == "test"
        assert len(draft.to) == 1
        assert draft.subject == "Test Subject"
        assert draft.body_text == "Hello World"
        assert draft.created_at is not None

    def test_create_with_cc(self, draft_manager):
        draft = draft_manager.create(
            account="test",
            to=[Address(addr="to@example.com")],
            cc=[Address(addr="cc@example.com")],
            subject="Test",
            body_text="Body",
        )

        assert len(draft.cc) == 1
        assert draft.cc[0].addr == "cc@example.com"

    def test_create_reply(self, draft_manager, monkeypatch, tmp_path):
        """Test creating a reply draft."""
        # Use the draft_manager's cache (already set up by fixture)
        from clerk.models import Message

        # Store a message in the draft manager's cache
        msg = Message(
            message_id="<original@example.com>",
            conv_id="conv123",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="sender@example.com", name="Sender")},
            to=[Address(addr="me@example.com")],
            date=datetime.now(UTC),
            subject="Original Subject",
            body_text="Original body",
            headers_fetched_at=datetime.now(UTC),
        )
        draft_manager.cache.store_message(msg)

        # Mock get_config to return a config with our account
        from clerk.config import AccountConfig, ClerkConfig, FromAddress, ImapConfig, SmtpConfig

        mock_config = ClerkConfig(
            accounts={
                "test": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.ex.com", username="me@example.com"),
                    smtp=SmtpConfig(host="smtp.ex.com", username="me@example.com"),
                    **{"from": FromAddress(address="me@example.com")},
                ),
            },
        )
        monkeypatch.setattr("clerk.drafts.get_config", lambda: mock_config)

        draft = draft_manager.create_reply(
            account="test",
            conv_id="conv123",
            body_text="This is my reply",
        )

        assert draft.reply_to_conv_id == "conv123"
        assert draft.in_reply_to == "<original@example.com>"
        assert draft.subject == "Re: Original Subject"
        assert draft.to[0].addr == "sender@example.com"

    def test_get_draft(self, draft_manager):
        created = draft_manager.create(
            account="test",
            to=[Address(addr="test@example.com")],
            subject="Test",
            body_text="Body",
        )

        retrieved = draft_manager.get(created.draft_id)
        assert retrieved is not None
        assert retrieved.draft_id == created.draft_id
        assert retrieved.subject == created.subject

    def test_get_nonexistent_draft(self, draft_manager):
        result = draft_manager.get("nonexistent_id")
        assert result is None

    def test_list_drafts(self, draft_manager):
        draft_manager.create(
            account="account1",
            to=[Address(addr="a@ex.com")],
            subject="Draft 1",
            body_text="Body 1",
        )
        draft_manager.create(
            account="account2",
            to=[Address(addr="b@ex.com")],
            subject="Draft 2",
            body_text="Body 2",
        )

        # List all
        all_drafts = draft_manager.list()
        assert len(all_drafts) == 2

        # List by account
        acc1_drafts = draft_manager.list(account="account1")
        assert len(acc1_drafts) == 1
        assert acc1_drafts[0].subject == "Draft 1"

    def test_update_draft(self, draft_manager):
        draft = draft_manager.create(
            account="test",
            to=[Address(addr="test@example.com")],
            subject="Original",
            body_text="Original body",
        )

        original_updated = draft.updated_at

        # Modify and update
        draft.subject = "Updated Subject"
        draft.body_text = "Updated body"
        draft_manager.update(draft)

        retrieved = draft_manager.get(draft.draft_id)
        assert retrieved.subject == "Updated Subject"
        assert retrieved.body_text == "Updated body"
        assert retrieved.updated_at > original_updated

    def test_delete_draft(self, draft_manager):
        draft = draft_manager.create(
            account="test",
            to=[Address(addr="test@example.com")],
            subject="To Delete",
            body_text="Body",
        )

        result = draft_manager.delete(draft.draft_id)
        assert result is True

        # Should be gone
        assert draft_manager.get(draft.draft_id) is None

    def test_delete_nonexistent_draft(self, draft_manager):
        result = draft_manager.delete("nonexistent")
        assert result is False
