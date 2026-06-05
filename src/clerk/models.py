"""Core data models for clerk."""

from collections.abc import Iterable
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class MessageFlag(str, Enum):
    """Standard IMAP message flags."""

    SEEN = "seen"
    ANSWERED = "answered"
    FLAGGED = "flagged"
    DELETED = "deleted"
    DRAFT = "draft"


# Bit values for the cache's INTEGER flags column. Kept adjacent to
# MessageFlag so the mapping is derived from one source of truth.
_FLAG_BITS: dict[MessageFlag, int] = {
    MessageFlag.SEEN: 1,
    MessageFlag.ANSWERED: 2,
    MessageFlag.FLAGGED: 4,
    MessageFlag.DELETED: 8,
    MessageFlag.DRAFT: 16,
}


def flags_to_bitmask(flags: Iterable[MessageFlag]) -> int:
    """Pack model flags into the cache's INTEGER bitmask column."""
    mask = 0
    for flag in flags:
        mask |= _FLAG_BITS.get(flag, 0)
    return mask


def bitmask_to_flags(mask: int) -> list[MessageFlag]:
    """Unpack the cache's INTEGER bitmask column into model flags."""
    return [flag for flag, bit in _FLAG_BITS.items() if mask & bit]


class Address(BaseModel):
    """Email address with optional display name."""

    addr: str
    name: str = ""

    def __str__(self) -> str:
        if self.name:
            return f"{self.name} <{self.addr}>"
        return self.addr


class Attachment(BaseModel):
    """Attachment metadata (content not stored)."""

    filename: str
    size: int
    content_type: str


class Message(BaseModel):
    """A single email message."""

    message_id: str = Field(description="Unique message ID from headers")
    conv_id: str = Field(description="Conversation/thread ID")
    folder: str = Field(default="INBOX", description="IMAP folder name")
    account: str = Field(default="", description="Account name if multi-account")
    uid: int | None = Field(
        default=None, description="Server IMAP UID within (account, folder)"
    )

    from_: Address = Field(alias="from", description="Sender address")
    to: list[Address] = Field(default_factory=list)
    cc: list[Address] = Field(default_factory=list)
    reply_to: list[Address] = Field(default_factory=list)

    date: datetime
    subject: str = ""

    body_text: str | None = Field(default=None, description="Plain text body")
    body_html: str | None = Field(default=None, description="HTML body")
    body_skipped: bool = Field(
        default=False,
        description="True if the body was too large to cache; fetch on demand",
    )

    attachments: list[Attachment] = Field(default_factory=list)
    flags: list[MessageFlag] = Field(default_factory=list)

    # For threading
    in_reply_to: str | None = Field(default=None, description="In-Reply-To header")
    references: list[str] = Field(default_factory=list, description="References header")

    # Cache metadata
    headers_fetched_at: datetime | None = None
    body_fetched_at: datetime | None = None

    model_config = {"populate_by_name": True}

    @property
    def is_read(self) -> bool:
        return MessageFlag.SEEN in self.flags

    @property
    def is_flagged(self) -> bool:
        return MessageFlag.FLAGGED in self.flags


class Conversation(BaseModel):
    """A conversation (thread) of related messages."""

    conv_id: str = Field(description="Unique conversation ID")
    subject: str = Field(description="Subject line (from first message)")
    participants: list[str] = Field(
        default_factory=list, description="All email addresses in thread"
    )
    message_count: int = Field(default=0)
    unread_count: int = Field(default=0)
    latest_date: datetime
    messages: list[Message] = Field(default_factory=list)
    account: str = Field(default="", description="Account name")

    @property
    def has_unread(self) -> bool:
        return self.unread_count > 0


class ConversationSummary(BaseModel):
    """Lightweight conversation summary for listings."""

    conv_id: str
    subject: str
    participants: list[str]
    message_count: int
    unread_count: int
    latest_date: datetime
    snippet: str = Field(default="", description="Preview of latest message")
    account: str = ""


class Draft(BaseModel):
    """A draft message pending send."""

    draft_id: str = Field(description="Local draft ID")
    account: str = Field(description="Account to send from")

    to: list[Address]
    cc: list[Address] = Field(default_factory=list)
    bcc: list[Address] = Field(default_factory=list)

    subject: str
    body_text: str
    body_html: str | None = None

    # For replies
    reply_to_conv_id: str | None = Field(default=None, description="Conversation being replied to")
    in_reply_to: str | None = Field(default=None, description="Message-ID being replied to")
    references: list[str] = Field(default_factory=list, description="Reference chain")

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UnreadCounts(BaseModel):
    """Unread message counts by folder."""

    account: str
    folders: dict[str, int] = Field(default_factory=dict, description="Folder -> count")
    total: int = 0


class FolderInfo(BaseModel):
    """Information about an IMAP folder."""

    name: str
    flags: list[str] = Field(default_factory=list)
    delimiter: str = "/"
    message_count: int | None = None
    unread_count: int | None = None


class CacheStats(BaseModel):
    """Statistics about the local cache."""

    message_count: int
    conversation_count: int
    oldest_message: datetime | None
    newest_message: datetime | None
    cache_size_bytes: int
    last_sync: datetime | None


class SendResult(BaseModel):
    """Result of sending a message."""

    success: bool
    message_id: str | None = None
    error: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# Exit codes as per spec
class ExitCode(int, Enum):
    SUCCESS = 0
    NOT_FOUND = 1
    INVALID_INPUT = 2
    CONNECTION_ERROR = 3
    AUTH_ERROR = 4
    SEND_BLOCKED = 5
