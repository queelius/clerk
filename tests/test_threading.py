"""Tests for conversation threading."""

from datetime import datetime

import pytest

from clerk.models import Address, Message, MessageFlag
from clerk.threading import (
    _normalize_subject,
    compute_conv_id,
    compute_root_id,
    group_by_subject,
    thread_messages,
)


def make_message(
    message_id: str,
    subject: str = "Test",
    from_addr: str = "sender@example.com",
    date: datetime | None = None,
    in_reply_to: str | None = None,
    references: list[str] | None = None,
    flags: list[MessageFlag] | None = None,
) -> Message:
    """Helper to create test messages."""
    return Message(
        message_id=message_id,
        conv_id="",  # Will be computed by threading
        account="test",
        folder="INBOX",
        **{"from": Address(addr=from_addr)},
        date=date or datetime.utcnow(),
        subject=subject,
        in_reply_to=in_reply_to,
        references=references or [],
        flags=flags or [],
        headers_fetched_at=datetime.utcnow(),
    )


class TestComputeRootId:
    def test_with_references(self):
        root = compute_root_id(
            message_id="<msg3@ex.com>",
            references=["<msg1@ex.com>", "<msg2@ex.com>"],
            in_reply_to="<msg2@ex.com>",
        )
        assert root == "<msg1@ex.com>"  # First reference is root

    def test_with_in_reply_to_only(self):
        root = compute_root_id(
            message_id="<msg2@ex.com>",
            references=[],
            in_reply_to="<msg1@ex.com>",
        )
        assert root == "<msg1@ex.com>"

    def test_standalone_message(self):
        root = compute_root_id(
            message_id="<msg1@ex.com>",
            references=[],
            in_reply_to=None,
        )
        assert root == "<msg1@ex.com>"


class TestComputeConvId:
    def test_same_root_same_id(self):
        id1 = compute_conv_id("<root@ex.com>")
        id2 = compute_conv_id("<root@ex.com>")
        assert id1 == id2

    def test_different_root_different_id(self):
        id1 = compute_conv_id("<root1@ex.com>")
        id2 = compute_conv_id("<root2@ex.com>")
        assert id1 != id2


class TestNormalizeSubject:
    def test_removes_re_prefix(self):
        assert _normalize_subject("Re: Hello") == "Hello"
        assert _normalize_subject("RE: Hello") == "Hello"
        assert _normalize_subject("re: Hello") == "Hello"

    def test_removes_fwd_prefix(self):
        assert _normalize_subject("Fwd: Hello") == "Hello"
        assert _normalize_subject("FWD: Hello") == "Hello"
        assert _normalize_subject("Fw: Hello") == "Hello"

    def test_removes_multiple_prefixes(self):
        assert _normalize_subject("Re: Re: Re: Hello") == "Hello"
        assert _normalize_subject("Fwd: Re: Hello") == "Hello"

    def test_no_prefix(self):
        assert _normalize_subject("Hello") == "Hello"

    def test_empty_string(self):
        assert _normalize_subject("") == ""


class TestThreadMessages:
    def test_simple_thread(self):
        """Test a simple two-message thread."""
        msg1 = make_message(
            message_id="<msg1@ex.com>",
            subject="Hello",
            date=datetime(2025, 1, 1, 10, 0),
        )
        msg2 = make_message(
            message_id="<msg2@ex.com>",
            subject="Re: Hello",
            date=datetime(2025, 1, 1, 11, 0),
            in_reply_to="<msg1@ex.com>",
            references=["<msg1@ex.com>"],
        )

        conversations = thread_messages([msg1, msg2])

        assert len(conversations) == 1
        conv = conversations[0]
        assert conv.message_count == 2
        assert conv.subject == "Hello"
        assert len(conv.messages) == 2
        # Messages should be ordered by date
        assert conv.messages[0].message_id == "<msg1@ex.com>"
        assert conv.messages[1].message_id == "<msg2@ex.com>"

    def test_longer_thread(self):
        """Test a three-message thread."""
        msg1 = make_message(
            message_id="<msg1@ex.com>",
            subject="Project Update",
            from_addr="alice@ex.com",
            date=datetime(2025, 1, 1, 9, 0),
        )
        msg2 = make_message(
            message_id="<msg2@ex.com>",
            subject="Re: Project Update",
            from_addr="bob@ex.com",
            date=datetime(2025, 1, 1, 10, 0),
            in_reply_to="<msg1@ex.com>",
            references=["<msg1@ex.com>"],
        )
        msg3 = make_message(
            message_id="<msg3@ex.com>",
            subject="Re: Project Update",
            from_addr="alice@ex.com",
            date=datetime(2025, 1, 1, 11, 0),
            in_reply_to="<msg2@ex.com>",
            references=["<msg1@ex.com>", "<msg2@ex.com>"],
        )

        conversations = thread_messages([msg1, msg2, msg3])

        assert len(conversations) == 1
        conv = conversations[0]
        assert conv.message_count == 3
        assert "alice@ex.com" in conv.participants
        assert "bob@ex.com" in conv.participants

    def test_multiple_threads(self):
        """Test multiple separate threads."""
        thread1_msg = make_message(
            message_id="<t1@ex.com>",
            subject="Thread 1",
            date=datetime(2025, 1, 1, 10, 0),
        )
        thread2_msg = make_message(
            message_id="<t2@ex.com>",
            subject="Thread 2",
            date=datetime(2025, 1, 1, 11, 0),
        )

        conversations = thread_messages([thread1_msg, thread2_msg])

        assert len(conversations) == 2

    def test_unread_count(self):
        """Test unread count calculation."""
        msg1 = make_message(
            message_id="<msg1@ex.com>",
            flags=[MessageFlag.SEEN],
        )
        msg2 = make_message(
            message_id="<msg2@ex.com>",
            in_reply_to="<msg1@ex.com>",
            references=["<msg1@ex.com>"],
            flags=[],  # Unread
        )
        msg3 = make_message(
            message_id="<msg3@ex.com>",
            in_reply_to="<msg2@ex.com>",
            references=["<msg1@ex.com>", "<msg2@ex.com>"],
            flags=[],  # Unread
        )

        conversations = thread_messages([msg1, msg2, msg3])

        assert len(conversations) == 1
        assert conversations[0].unread_count == 2

    def test_empty_input(self):
        """Test with no messages."""
        conversations = thread_messages([])
        assert conversations == []

    def test_missing_parent(self):
        """Test thread where parent message is not in the set."""
        # Only have the reply, not the original
        reply = make_message(
            message_id="<reply@ex.com>",
            subject="Re: Original",
            in_reply_to="<original@ex.com>",
            references=["<original@ex.com>"],
        )

        conversations = thread_messages([reply])

        assert len(conversations) == 1
        assert conversations[0].message_count == 1


class TestGroupBySubject:
    def test_group_same_subject(self):
        msg1 = make_message(message_id="<m1@ex.com>", subject="Hello")
        msg2 = make_message(message_id="<m2@ex.com>", subject="Re: Hello")
        msg3 = make_message(message_id="<m3@ex.com>", subject="RE: Hello")

        groups = group_by_subject([msg1, msg2, msg3])

        # All should be grouped under "Hello"
        assert len(groups) == 1
        assert "Hello" in groups
        assert len(groups["Hello"]) == 3

    def test_group_different_subjects(self):
        msg1 = make_message(message_id="<m1@ex.com>", subject="Topic A")
        msg2 = make_message(message_id="<m2@ex.com>", subject="Topic B")

        groups = group_by_subject([msg1, msg2])

        assert len(groups) == 2
        assert "Topic A" in groups
        assert "Topic B" in groups
