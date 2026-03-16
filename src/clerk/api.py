"""ClerkAPI - Shared business logic layer for clerk.

This module provides a unified API that both the CLI and MCP server use.
All email operations go through this layer to ensure consistent behavior.
"""

import html
import re
from datetime import datetime
from typing import Any

from .cache import Cache, get_cache
from .config import ClerkConfig, ensure_dirs, get_config
from .drafts import DraftManager, get_draft_manager
from .imap_client import get_imap_client
from .models import (
    Address,
    CacheStats,
    Conversation,
    Draft,
    FolderInfo,
    Message,
    MessageFlag,
    SendResult,
    UnreadCounts,
)
from .smtp_client import check_send_allowed, send_draft


def html_to_text(html_body: str) -> str:
    """Convert HTML email body to readable plain text.

    Handles the common case of Exchange/Outlook HTML-only emails.
    """
    text = re.sub(r"<style[^>]*>.*?</style>", "", html_body, flags=re.DOTALL)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</div>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return text.strip()


class ClerkAPI:
    """Unified API for clerk email operations.

    This class centralizes all business logic and can be used by:
    - MCP server tools
    - Programmatic usage
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
                if msg.body_text is None and (fresh or not self.cache.is_fresh(
                    msg.message_id,
                    self.config.cache.body_freshness_min,
                    check_body=True,
                )):
                    with get_imap_client(msg.account) as client:
                        body_text, body_html = client.fetch_message_body(
                            msg.folder, msg.message_id
                        )
                        if body_text is None and body_html:
                            body_text = html_to_text(body_html)
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

        if msg and msg.body_text is None and (fresh or not self.cache.is_fresh(
            msg.message_id, self.config.cache.body_freshness_min, check_body=True
        )):
            with get_imap_client(msg.account) as client:
                body_text, body_html = client.fetch_message_body(
                    msg.folder, msg.message_id
                )
                if body_text is None and body_html:
                    body_text = html_to_text(body_html)
                self.cache.update_body(msg.message_id, body_text, body_html)
                msg.body_text = body_text
                msg.body_html = body_html

        return msg

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

    def create_reply(
        self,
        message_id: str,
        body: str,
        reply_all: bool = False,
        account: str | None = None,
    ) -> Draft:
        """Create a reply draft to an existing message.

        Args:
            message_id: Message ID to reply to
            body: Reply body text
            reply_all: Include all original recipients
            account: Account name (uses message's account if not provided)

        Returns:
            Created Draft

        Raises:
            ValueError: If original message not found
        """
        # Use cache lookup — we only need metadata (conv_id, account), not body
        msg = self.cache.get_message(message_id)
        if not msg:
            raise ValueError(f"Message not found: {message_id}")

        reply_account = account or msg.account
        account_name, _ = self.config.get_account(reply_account)

        return self.drafts.create_reply(
            account=account_name,
            conv_id=msg.conv_id,
            body_text=body,
            reply_all=reply_all,
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
    # Sync Operations
    # =========================================================================

    def sync_folder(
        self,
        account: str | None = None,
        folder: str = "INBOX",
        full: bool = False,
    ) -> dict[str, Any]:
        """Sync a folder from IMAP, fetching only new messages.

        Uses UID-based incremental sync: only messages with UIDs higher than
        the last known UID are fetched. On first sync (or full=True), fetches
        the most recent batch.

        Args:
            account: Account name (uses default if not provided)
            folder: Folder to sync (default: INBOX)
            full: If True, re-fetch everything (ignore sync state)

        Returns:
            Dict with synced count, account, folder
        """
        account_name, _ = self.config.get_account(account)

        # Get the last known UID for this folder
        since_uid = 0
        if not full:
            state = self.cache.get_sync_state(account_name, folder)
            if state:
                since_uid = state["last_uid"]

        with get_imap_client(account_name) as client:
            messages, highest_uid = client.fetch_messages_since_uid(
                folder=folder,
                since_uid=since_uid,
                fetch_bodies=False,
            )

            for msg in messages:
                self.cache.store_message(msg)

        # Update sync state only if we saw new UIDs
        if highest_uid > since_uid:
            self.cache.set_sync_state(account_name, folder, highest_uid)

        self.cache.mark_inbox_synced(account_name)

        return {
            "synced": len(messages),
            "account": account_name,
            "folder": folder,
        }

    # =========================================================================
    # Cache & Status Operations
    # =========================================================================

    def get_cache_stats(self) -> CacheStats:
        """Get cache statistics."""
        return self.cache.get_stats()

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self.cache.clear()

    def get_status(self) -> dict[str, Any]:
        """Get overall status information."""
        from . import __version__

        status: dict[str, Any] = {
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
