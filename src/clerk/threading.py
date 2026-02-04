"""Conversation threading algorithm.

Implements JWZ threading algorithm (simplified) to group messages into conversations.
See: https://www.jwz.org/doc/threading.html
"""

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field

from .models import Conversation, Message, MessageFlag


@dataclass
class ThreadNode:
    """A node in the thread tree."""

    message_id: str
    message: Message | None = None
    parent: "ThreadNode | None" = None
    children: list["ThreadNode"] = field(default_factory=list)

    @property
    def is_dummy(self) -> bool:
        """A dummy node has no message (just a placeholder in the tree)."""
        return self.message is None


def compute_root_id(message_id: str, references: list[str], in_reply_to: str | None) -> str:
    """Compute the root message ID for a thread.

    Returns the earliest message in the reference chain, or the message itself.
    """
    if references:
        return references[0]
    elif in_reply_to:
        return in_reply_to
    return message_id


def compute_conv_id(root_message_id: str) -> str:
    """Compute a stable conversation ID from the root message ID."""
    return hashlib.sha256(root_message_id.encode()).hexdigest()[:12]


def thread_messages(messages: list[Message]) -> list[Conversation]:
    """Thread a list of messages into conversations.

    Uses a simplified JWZ algorithm:
    1. Build a table of message_id -> node
    2. Link parents and children based on References/In-Reply-To
    3. Find root nodes (no parent or parent is dummy)
    4. Group into conversations
    """
    if not messages:
        return []

    # Step 1: Build id_table mapping message_id to ThreadNode
    id_table: dict[str, ThreadNode] = {}

    for msg in messages:
        # Get or create node for this message
        if msg.message_id in id_table:
            node = id_table[msg.message_id]
            node.message = msg
        else:
            node = ThreadNode(message_id=msg.message_id, message=msg)
            id_table[msg.message_id] = node

        # Build reference chain
        refs = msg.references.copy()
        if msg.in_reply_to and msg.in_reply_to not in refs:
            refs.append(msg.in_reply_to)

        parent_node: ThreadNode | None = None
        for ref_id in refs:
            # Get or create node for reference
            if ref_id not in id_table:
                id_table[ref_id] = ThreadNode(message_id=ref_id)

            ref_node = id_table[ref_id]

            # Link to parent if we have one
            if parent_node is not None and ref_node.parent is None:
                ref_node.parent = parent_node
                if ref_node not in parent_node.children:
                    parent_node.children.append(ref_node)

            parent_node = ref_node

        # Link this message to its parent
        if parent_node is not None and parent_node != node and node.parent is None:
            node.parent = parent_node
            if node not in parent_node.children:
                parent_node.children.append(node)

    # Step 2: Find root nodes (nodes with no parent)
    roots: list[ThreadNode] = []
    for node in id_table.values():
        if node.parent is None:
            roots.append(node)

    # Step 3: Promote dummy roots with single child
    promoted_roots: list[ThreadNode] = []
    for root in roots:
        if root.is_dummy and len(root.children) == 1:
            # Promote the child to be the root
            child = root.children[0]
            child.parent = None
            promoted_roots.append(child)
        else:
            promoted_roots.append(root)

    # Step 4: Build conversations from roots
    conversations: list[Conversation] = []

    for root in promoted_roots:
        # Collect all messages in this thread
        thread_messages: list[Message] = []
        _collect_messages(root, thread_messages)

        if not thread_messages:
            continue

        # Sort by date
        thread_messages.sort(key=lambda m: m.date)

        # Compute conversation metadata
        participants: set[str] = set()
        unread_count = 0

        for msg in thread_messages:
            participants.add(msg.from_.addr)
            for addr in msg.to + msg.cc:
                participants.add(addr.addr)
            if MessageFlag.SEEN not in msg.flags:
                unread_count += 1

        # Get subject from first message with a subject
        subject = "(no subject)"
        for msg in thread_messages:
            if msg.subject:
                # Strip Re:/Fwd: prefixes for canonical subject
                subject = _normalize_subject(msg.subject)
                break

        # Compute conv_id from root
        root_id = root.message_id
        conv_id = compute_conv_id(root_id)

        # conv_id is already set during fetch; no per-message update needed

        conversations.append(
            Conversation(
                conv_id=conv_id,
                subject=subject,
                participants=sorted(participants),
                message_count=len(thread_messages),
                unread_count=unread_count,
                latest_date=max(m.date for m in thread_messages),
                messages=thread_messages,
                account=thread_messages[0].account if thread_messages else "",
            )
        )

    # Sort conversations by latest date
    conversations.sort(key=lambda c: c.latest_date, reverse=True)

    return conversations


def _collect_messages(node: ThreadNode, messages: list[Message]) -> None:
    """Recursively collect all messages from a thread tree."""
    if node.message is not None:
        messages.append(node.message)

    for child in node.children:
        _collect_messages(child, messages)


def _normalize_subject(subject: str) -> str:
    """Normalize subject by removing Re:/Fwd: prefixes."""
    import re

    # Remove Re:, Fwd:, etc. prefixes (case insensitive)
    pattern = r"^(?:(?:Re|Fwd|Fw):\s*)+(.*)$"
    match = re.match(pattern, subject, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return subject.strip()


def group_by_subject(messages: list[Message]) -> dict[str, list[Message]]:
    """Group messages by normalized subject.

    This is a fallback for when threading headers are missing.
    """
    groups: dict[str, list[Message]] = defaultdict(list)

    for msg in messages:
        normalized = _normalize_subject(msg.subject) if msg.subject else "(no subject)"
        groups[normalized].append(msg)

    return dict(groups)
