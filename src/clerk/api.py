"""ClerkAPI - Shared business logic layer for clerk.

This module provides a unified API that both the CLI and MCP server use.
All email operations go through this layer to ensure consistent behavior.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .cache import Cache, get_cache
from .config import ClerkConfig, ensure_dirs, get_config
from .drafts import DraftManager, get_draft_manager
from .imap_client import get_imap_client
from .models import (
    Address,
    CacheStats,
    Conversation,
    ConversationSummary,
    Draft,
    FolderInfo,
    Message,
    MessageFlag,
    UnreadCounts,
)
from .search import SearchQuery, parse_search_query
from .smtp_client import SendResult, check_send_allowed, send_draft


@dataclass
class InboxResult:
    """Result from list_inbox operation."""

    account: str
    conversations: list[ConversationSummary]
    count: int
    from_cache: bool = False


@dataclass
class SearchResult:
    """Result from search operation."""

    query: str
    messages: list[Message]
    count: int


@dataclass
class SendPreview:
    """Preview before sending a draft."""

    draft_id: str
    preview: str
    confirmation_token: str
    expires_in_seconds: int


@dataclass
class ConversationLookupResult:
    """Result of conversation ID lookup.

    One of these will be set:
    - conversation: If a unique match was found
    - matches: If multiple conversations match (ambiguous prefix)
    - error: If no matches were found
    """

    conversation: Conversation | None = None
    matches: list[ConversationSummary] | None = None
    error: str | None = None


class ClerkAPI:
    """Unified API for clerk email operations.

    This class centralizes all business logic and can be used by:
    - CLI commands
    - MCP server tools
    - Interactive shell
    - Programmatic usage

    Example:
        api = ClerkAPI()
        result = api.list_inbox("personal", limit=20)
        for conv in result.conversations:
            print(conv.subject)
    """

    def __init__(
        self,
        config: ClerkConfig | None = None,
        cache: Cache | None = None,
        draft_manager: DraftManager | None = None,
    ) -> None:
        """Initialize the API.

        Args:
            config: Configuration (uses default if not provided)
            cache: Cache instance (uses default if not provided)
            draft_manager: Draft manager (uses default if not provided)
        """
        ensure_dirs()
        self._config = config
        self._cache = cache
        self._draft_manager = draft_manager

    @property
    def config(self) -> ClerkConfig:
        """Get configuration, loading default if needed."""
        if self._config is None:
            self._config = get_config()
        return self._config

    @property
    def cache(self) -> Cache:
        """Get cache, loading default if needed."""
        if self._cache is None:
            self._cache = get_cache()
        return self._cache

    @property
    def drafts(self) -> DraftManager:
        """Get draft manager, loading default if needed."""
        if self._draft_manager is None:
            self._draft_manager = get_draft_manager()
        return self._draft_manager

    # =========================================================================
    # Inbox & Message Operations
    # =========================================================================

    def list_inbox(
        self,
        account: str | None = None,
        folder: str = "INBOX",
        limit: int = 20,
        unread_only: bool = False,
        fresh: bool = False,
    ) -> InboxResult:
        """List recent conversations in inbox.

        Args:
            account: Account name (uses default if not provided)
            folder: Folder to list (default: INBOX)
            limit: Maximum conversations to return
            unread_only: Only show unread conversations
            fresh: Bypass cache, fetch from server

        Returns:
            InboxResult with list of conversation summaries
        """
        account_name, account_config = self.config.get_account(account)

        # Check cache freshness
        if not fresh and self.cache.is_inbox_fresh(
            account_name, self.config.cache.inbox_freshness_min
        ):
            # Serve from cache
            conversations = self.cache.list_conversations(
                account=account_name,
                folder=folder,
                unread_only=unread_only,
                limit=limit,
            )
            return InboxResult(
                account=account_name,
                conversations=conversations,
                count=len(conversations),
                from_cache=True,
            )

        # Fetch from server
        with get_imap_client(account_name) as client:
            messages = client.fetch_messages(
                folder=folder,
                limit=limit * 3,  # Fetch more to account for threading
                unread_only=unread_only,
                fetch_bodies=False,
            )

            for msg in messages:
                self.cache.store_message(msg)

            self.cache.mark_inbox_synced(account_name)

        # Prune old messages
        self.cache.prune_old_messages(self.config.cache.window_days)

        # Get conversations from cache
        conversations = self.cache.list_conversations(
            account=account_name,
            folder=folder,
            unread_only=unread_only,
            limit=limit,
        )

        return InboxResult(
            account=account_name,
            conversations=conversations,
            count=len(conversations),
            from_cache=False,
        )

    def get_conversation(
        self, conv_id: str, fresh: bool = False
    ) -> Conversation | None:
        """Get a conversation by ID.

        Args:
            conv_id: Conversation ID
            fresh: Bypass cache for body fetching

        Returns:
            Conversation with all messages, or None if not found
        """
        conv = self.cache.get_conversation(conv_id)

        if conv:
            # Fetch bodies if needed
            for msg in conv.messages:
                if msg.body_text is None:
                    if fresh or not self.cache.is_fresh(
                        msg.message_id,
                        self.config.cache.body_freshness_min,
                        check_body=True,
                    ):
                        with get_imap_client(msg.account) as client:
                            body_text, body_html = client.fetch_message_body(
                                msg.folder, msg.message_id
                            )
                            self.cache.update_body(msg.message_id, body_text, body_html)
                            msg.body_text = body_text
                            msg.body_html = body_html

        return conv

    def get_message(self, message_id: str, fresh: bool = False) -> Message | None:
        """Get a single message by ID.

        Args:
            message_id: Message ID
            fresh: Bypass cache for body fetching

        Returns:
            Message or None if not found
        """
        msg = self.cache.get_message(message_id)

        if msg and msg.body_text is None:
            if fresh or not self.cache.is_fresh(
                msg.message_id, self.config.cache.body_freshness_min, check_body=True
            ):
                with get_imap_client(msg.account) as client:
                    body_text, body_html = client.fetch_message_body(
                        msg.folder, msg.message_id
                    )
                    self.cache.update_body(msg.message_id, body_text, body_html)
                    msg.body_text = body_text
                    msg.body_html = body_html

        return msg

    def resolve_conversation_id(
        self, conv_id: str, fresh: bool = False
    ) -> ConversationLookupResult:
        """Resolve a conversation ID or prefix.

        Use this method when you need to handle ambiguous prefixes gracefully.
        It supports any prefix length and provides disambiguation when multiple
        conversations match.

        Args:
            conv_id: Conversation ID or prefix
            fresh: Bypass cache for body fetching

        Returns:
            ConversationLookupResult with one of:
            - conversation: If unique match found (bodies will be fetched)
            - matches: If ambiguous (multiple matches) - summaries for disambiguation
            - error: If no matches found
        """
        # Try to get conversation (handles unique prefix internally)
        conv = self.get_conversation(conv_id, fresh=fresh)
        if conv:
            return ConversationLookupResult(conversation=conv)

        # Check for ambiguous matches
        matches = self.cache.find_conversations_by_prefix(conv_id)
        if matches:
            return ConversationLookupResult(matches=matches)

        return ConversationLookupResult(error=f"No conversation matching '{conv_id}'")

    # =========================================================================
    # Search Operations
    # =========================================================================

    def search(
        self,
        query: str,
        account: str | None = None,
        limit: int = 20,
    ) -> SearchResult:
        """Search messages using basic FTS.

        Args:
            query: Search query string
            account: Filter by account (optional)
            limit: Maximum results

        Returns:
            SearchResult with matching messages
        """
        messages = self.cache.search(query, account=account, limit=limit)
        return SearchResult(query=query, messages=messages, count=len(messages))

    def search_advanced(
        self,
        query: str | SearchQuery,
        account: str | None = None,
        folder: str | None = None,
        limit: int = 20,
    ) -> SearchResult:
        """Advanced search with operator support.

        Supports operators like:
        - from:alice, to:bob
        - subject:meeting, body:quarterly
        - has:attachment
        - is:unread, is:read, is:flagged
        - after:2025-01-01, before:2025-12-31

        Args:
            query: Search query string or pre-parsed SearchQuery
            account: Filter by account (optional)
            folder: Filter by folder (optional)
            limit: Maximum results

        Returns:
            SearchResult with matching messages
        """
        original_query = query if isinstance(query, str) else query.original_query
        messages = self.cache.search_advanced(
            query, account=account, folder=folder, limit=limit
        )
        return SearchResult(query=original_query, messages=messages, count=len(messages))

    def search_sql(
        self,
        sql: str,
        params: tuple | list | None = None,
        limit: int = 100,
    ) -> list[Message]:
        """Execute a raw SQL query (power users).

        Args:
            sql: SQL SELECT query
            params: Query parameters (optional)
            limit: Maximum results (enforced)

        Returns:
            List of matching messages

        Raises:
            ValueError: If query is not a SELECT statement
        """
        return self.cache.execute_raw_query(sql, params, limit)

    # =========================================================================
    # Draft Operations
    # =========================================================================

    def create_draft(
        self,
        to: list[str] | list[Address],
        subject: str,
        body: str,
        cc: list[str] | list[Address] | None = None,
        reply_to_conv_id: str | None = None,
        account: str | None = None,
    ) -> Draft:
        """Create a new draft message.

        Args:
            to: Recipient addresses
            subject: Subject line
            body: Message body
            cc: CC recipients (optional)
            reply_to_conv_id: Conversation ID to reply to (optional)
            account: Account name (uses default if not provided)

        Returns:
            Created Draft
        """
        account_name, _ = self.config.get_account(account)

        # Convert strings to Address objects
        def to_address(a: str | Address) -> Address:
            if isinstance(a, Address):
                return a
            return Address(addr=a.strip(), name="")

        to_addrs = [to_address(a) for a in to]
        cc_addrs = [to_address(a) for a in (cc or [])]

        if reply_to_conv_id:
            return self.drafts.create_reply(
                account=account_name,
                conv_id=reply_to_conv_id,
                body_text=body,
            )
        else:
            return self.drafts.create(
                account=account_name,
                to=to_addrs,
                cc=cc_addrs,
                subject=subject,
                body_text=body,
            )

    def get_draft(self, draft_id: str) -> Draft | None:
        """Get a draft by ID."""
        return self.drafts.get(draft_id)

    def list_drafts(self, account: str | None = None) -> list[Draft]:
        """List all drafts, optionally filtered by account."""
        return self.drafts.list(account=account)

    def update_draft(self, draft: Draft) -> None:
        """Update an existing draft."""
        self.drafts.update(draft)

    def delete_draft(self, draft_id: str) -> bool:
        """Delete a draft.

        Returns:
            True if deleted, False if not found
        """
        return self.drafts.delete(draft_id)

    def send_draft(
        self, draft_id: str, skip_confirmation: bool = False
    ) -> SendResult:
        """Send a draft message.

        Args:
            draft_id: Draft ID to send
            skip_confirmation: Skip send policy checks

        Returns:
            SendResult with success status and message_id or error
        """
        draft = self.drafts.get(draft_id)
        if not draft:
            return SendResult(
                success=False,
                error=f"Draft not found: {draft_id}",
                timestamp=datetime.now(),
            )

        if not skip_confirmation:
            allowed, error = check_send_allowed(draft, draft.account)
            if not allowed:
                return SendResult(
                    success=False,
                    error=error or "Send blocked by policy",
                    timestamp=datetime.now(),
                )

        return send_draft(draft_id)

    # =========================================================================
    # Message Actions
    # =========================================================================

    def mark_read(self, message_id: str, account: str | None = None) -> None:
        """Mark a message as read."""
        account_name, _ = self.config.get_account(account)

        msg = self.cache.get_message(message_id)
        folder = msg.folder if msg else "INBOX"

        with get_imap_client(account_name) as client:
            client.add_flags(folder, message_id, [MessageFlag.SEEN])

        if msg:
            flags = list(msg.flags)
            if MessageFlag.SEEN not in flags:
                flags.append(MessageFlag.SEEN)
            self.cache.update_flags(message_id, flags)

    def mark_unread(self, message_id: str, account: str | None = None) -> None:
        """Mark a message as unread."""
        account_name, _ = self.config.get_account(account)

        msg = self.cache.get_message(message_id)
        folder = msg.folder if msg else "INBOX"

        with get_imap_client(account_name) as client:
            client.remove_flags(folder, message_id, [MessageFlag.SEEN])

        if msg:
            flags = [f for f in msg.flags if f != MessageFlag.SEEN]
            self.cache.update_flags(message_id, flags)

    def flag_message(self, message_id: str, account: str | None = None) -> None:
        """Flag/star a message."""
        account_name, _ = self.config.get_account(account)

        msg = self.cache.get_message(message_id)
        folder = msg.folder if msg else "INBOX"

        with get_imap_client(account_name) as client:
            client.add_flags(folder, message_id, [MessageFlag.FLAGGED])

        if msg:
            flags = list(msg.flags)
            if MessageFlag.FLAGGED not in flags:
                flags.append(MessageFlag.FLAGGED)
            self.cache.update_flags(message_id, flags)

    def unflag_message(self, message_id: str, account: str | None = None) -> None:
        """Remove flag from a message."""
        account_name, _ = self.config.get_account(account)

        msg = self.cache.get_message(message_id)
        folder = msg.folder if msg else "INBOX"

        with get_imap_client(account_name) as client:
            client.remove_flags(folder, message_id, [MessageFlag.FLAGGED])

        if msg:
            flags = [f for f in msg.flags if f != MessageFlag.FLAGGED]
            self.cache.update_flags(message_id, flags)

    def move_message(
        self,
        message_id: str,
        to_folder: str,
        from_folder: str = "INBOX",
        account: str | None = None,
    ) -> None:
        """Move a message to another folder."""
        account_name, _ = self.config.get_account(account)

        with get_imap_client(account_name) as client:
            client.move_message(message_id, from_folder, to_folder)

        self.cache.move_message(message_id, to_folder)

    def archive_message(self, message_id: str, account: str | None = None) -> None:
        """Archive a message."""
        account_name, _ = self.config.get_account(account)

        with get_imap_client(account_name) as client:
            client.archive_message(message_id)

        self.cache.move_message(message_id, "Archive")

    # =========================================================================
    # Folder Operations
    # =========================================================================

    def list_folders(self, account: str | None = None) -> list[FolderInfo]:
        """List all folders for an account."""
        account_name, _ = self.config.get_account(account)

        with get_imap_client(account_name) as client:
            return client.list_folders()

    def get_unread_counts(self, account: str | None = None) -> UnreadCounts:
        """Get unread message counts by folder."""
        account_name, _ = self.config.get_account(account)

        with get_imap_client(account_name) as client:
            return client.get_unread_counts()

    # =========================================================================
    # Attachment Operations
    # =========================================================================

    def list_attachments(self, message_id: str) -> list[dict[str, Any]]:
        """List attachments for a message.

        Returns:
            List of attachment info dicts with filename, size, content_type
        """
        msg = self.cache.get_message(message_id)
        if not msg:
            return []

        return [
            {
                "filename": att.filename,
                "size": att.size,
                "content_type": att.content_type,
            }
            for att in msg.attachments
        ]

    def download_attachment(
        self,
        message_id: str,
        filename: str,
        destination: Path | str,
        account: str | None = None,
    ) -> Path:
        """Download an attachment from a message.

        Args:
            message_id: Message ID
            filename: Attachment filename
            destination: Directory or file path to save to
            account: Account name (optional)

        Returns:
            Path to saved file

        Raises:
            FileNotFoundError: If message or attachment not found
        """
        msg = self.cache.get_message(message_id)
        if not msg:
            raise FileNotFoundError(f"Message not found: {message_id}")

        # Find the attachment
        attachment = next(
            (a for a in msg.attachments if a.filename == filename), None
        )
        if not attachment:
            raise FileNotFoundError(f"Attachment not found: {filename}")

        # Determine account
        account_name = msg.account

        # Fetch attachment content from server
        with get_imap_client(account_name) as client:
            content = client.fetch_attachment(msg.folder, message_id, filename)

        # Save to destination
        dest_path = Path(destination)
        if dest_path.is_dir():
            dest_path = dest_path / filename

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(content)

        return dest_path

    # =========================================================================
    # Cache & Status Operations
    # =========================================================================

    def get_cache_stats(self) -> CacheStats:
        """Get cache statistics."""
        return self.cache.get_stats()

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self.cache.clear()

    def refresh_cache(
        self, account: str | None = None, folder: str = "INBOX", limit: int = 200
    ) -> int:
        """Force refresh cache from server.

        Returns:
            Number of messages fetched
        """
        account_name, _ = self.config.get_account(account)

        with get_imap_client(account_name) as client:
            messages = client.fetch_messages(
                folder=folder,
                limit=limit,
                fetch_bodies=True,
            )

            for msg in messages:
                self.cache.store_message(msg)

            self.cache.mark_inbox_synced(account_name)

        self.cache.prune_old_messages(self.config.cache.window_days)
        return len(messages)

    def get_status(self) -> dict[str, Any]:
        """Get overall status information."""
        from . import __version__

        status = {
            "version": __version__,
            "accounts": {},
        }

        for name in self.config.accounts:
            try:
                with get_imap_client(name) as client:
                    status["accounts"][name] = {
                        "connected": True,
                        "folders": len(client.list_folders()),
                    }
            except Exception as e:
                status["accounts"][name] = {
                    "connected": False,
                    "error": str(e),
                }

        return status


# Singleton instance
_api_instance: ClerkAPI | None = None


def get_api() -> ClerkAPI:
    """Get the singleton ClerkAPI instance."""
    global _api_instance
    if _api_instance is None:
        _api_instance = ClerkAPI()
    return _api_instance
