"""Tests for clerk models."""

from datetime import datetime

import pytest

from clerk.models import (
    Address,
    Attachment,
    Conversation,
    ConversationSummary,
    Draft,
    ExitCode,
    Message,
    MessageFlag,
)


class TestAddress:
    def test_create_with_name(self):
        addr = Address(addr="alice@example.com", name="Alice")
        assert addr.addr == "alice@example.com"
        assert addr.name == "Alice"
        assert str(addr) == "Alice <alice@example.com>"

    def test_create_without_name(self):
        addr = Address(addr="bob@example.com")
        assert addr.name == ""
        assert str(addr) == "bob@example.com"


class TestMessage:
    def test_create_message(self):
        msg = Message(
            message_id="<test123@example.com>",
            conv_id="conv123",
            **{"from": Address(addr="sender@example.com", name="Sender")},
            to=[Address(addr="recipient@example.com")],
            date=datetime(2025, 1, 1, 12, 0, 0),
            subject="Test Subject",
            body_text="Hello World",
        )

        assert msg.message_id == "<test123@example.com>"
        assert msg.conv_id == "conv123"
        assert msg.from_.addr == "sender@example.com"
        assert len(msg.to) == 1
        assert msg.subject == "Test Subject"
        assert msg.body_text == "Hello World"

    def test_message_is_read(self):
        msg = Message(
            message_id="<test@example.com>",
            conv_id="conv1",
            **{"from": Address(addr="a@b.com")},
            date=datetime.utcnow(),
            flags=[MessageFlag.SEEN],
        )
        assert msg.is_read is True

        msg2 = Message(
            message_id="<test2@example.com>",
            conv_id="conv1",
            **{"from": Address(addr="a@b.com")},
            date=datetime.utcnow(),
            flags=[],
        )
        assert msg2.is_read is False

    def test_message_is_flagged(self):
        msg = Message(
            message_id="<test@example.com>",
            conv_id="conv1",
            **{"from": Address(addr="a@b.com")},
            date=datetime.utcnow(),
            flags=[MessageFlag.FLAGGED],
        )
        assert msg.is_flagged is True


class TestConversation:
    def test_create_conversation(self):
        msg1 = Message(
            message_id="<msg1@example.com>",
            conv_id="conv123",
            **{"from": Address(addr="alice@example.com")},
            date=datetime(2025, 1, 1, 10, 0, 0),
            subject="Test",
            flags=[MessageFlag.SEEN],
        )
        msg2 = Message(
            message_id="<msg2@example.com>",
            conv_id="conv123",
            **{"from": Address(addr="bob@example.com")},
            date=datetime(2025, 1, 1, 11, 0, 0),
            subject="Re: Test",
            flags=[],
        )

        conv = Conversation(
            conv_id="conv123",
            subject="Test",
            participants=["alice@example.com", "bob@example.com"],
            message_count=2,
            unread_count=1,
            latest_date=datetime(2025, 1, 1, 11, 0, 0),
            messages=[msg1, msg2],
        )

        assert conv.conv_id == "conv123"
        assert conv.message_count == 2
        assert conv.unread_count == 1
        assert conv.has_unread is True

    def test_conversation_no_unread(self):
        conv = Conversation(
            conv_id="conv1",
            subject="Test",
            participants=[],
            message_count=1,
            unread_count=0,
            latest_date=datetime.utcnow(),
        )
        assert conv.has_unread is False


class TestDraft:
    def test_create_draft(self):
        draft = Draft(
            draft_id="draft_123",
            account="personal",
            to=[Address(addr="bob@example.com")],
            subject="Hello",
            body_text="Test body",
        )

        assert draft.draft_id == "draft_123"
        assert draft.account == "personal"
        assert len(draft.to) == 1
        assert draft.subject == "Hello"
        assert draft.body_text == "Test body"
        assert draft.reply_to_conv_id is None

    def test_create_reply_draft(self):
        draft = Draft(
            draft_id="draft_456",
            account="work",
            to=[Address(addr="alice@example.com")],
            subject="Re: Original",
            body_text="Reply text",
            reply_to_conv_id="conv123",
            in_reply_to="<original@example.com>",
            references=["<original@example.com>"],
        )

        assert draft.reply_to_conv_id == "conv123"
        assert draft.in_reply_to == "<original@example.com>"
        assert len(draft.references) == 1


class TestAttachment:
    def test_create_attachment(self):
        att = Attachment(
            filename="report.pdf",
            size=102400,
            content_type="application/pdf",
        )
        assert att.filename == "report.pdf"
        assert att.size == 102400
        assert att.content_type == "application/pdf"


class TestExitCode:
    def test_exit_codes(self):
        assert ExitCode.SUCCESS.value == 0
        assert ExitCode.NOT_FOUND.value == 1
        assert ExitCode.INVALID_INPUT.value == 2
        assert ExitCode.CONNECTION_ERROR.value == 3
        assert ExitCode.AUTH_ERROR.value == 4
        assert ExitCode.SEND_BLOCKED.value == 5
