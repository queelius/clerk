"""ClerkAPI - Single source of truth for writes, sync, and status.

All mutations (sends, flags, moves, syncs) route through this layer. Reads
bypass it via ``clerk_sql`` for LLM flexibility. The MCP server and CLI are
thin adapters on top.
"""

import asyncio
import hashlib
import html
import json
import re
import secrets
import sys
from datetime import UTC, datetime, timedelta
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
from .smtp_client import SmtpClient


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


def format_draft_preview(draft: Draft) -> str:
    """Render a human- and LLM-readable preview of a draft before send."""
    lines = [f"From: {draft.account}"]
    lines.append(f"To: {', '.join(str(a) for a in draft.to)}")
    if draft.cc:
        lines.append(f"Cc: {', '.join(str(a) for a in draft.cc)}")
    if draft.bcc:
        lines.append(f"Bcc: {', '.join(str(a) for a in draft.bcc)}")
    lines.append(f"Subject: {draft.subject}")
    lines.append("")
    lines.append(draft.body_text)
    return "\n".join(lines)


def draft_content_hash(draft: Draft) -> str:
    """Stable hash of the parts of a draft a user would confirm in a preview.

    Includes everything the preview shows plus body_html (which is sent but
    not displayed). Used by the persistent two-step confirmation: if the
    draft is mutated between step 1 (preview) and step 2 (send), the hash
    changes and the stored token is invalidated.
    """
    canonical = {
        "account": draft.account,
        "to": [[a.addr, a.name] for a in draft.to],
        "cc": [[a.addr, a.name] for a in draft.cc],
        "bcc": [[a.addr, a.name] for a in draft.bcc],
        "subject": draft.subject,
        "body_text": draft.body_text,
        "body_html": draft.body_html,
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


SEND_TOKEN_TTL_SECONDS = 300
_SEND_TOKEN_PREFIX = "send_token:"


class ClerkAPI:
    """Unified API for clerk email operations.

    Single source of truth for mutations. The MCP server and CLI both delegate
    here; direct smtp_client / cache writes from other layers are a code smell.
    """

    def __init__(
        self,
        config: ClerkConfig | None = None,
        cache: Cache | None = None,
        draft_manager: DraftManager | None = None,
    ) -> None:
        ensure_dirs()
        self._config = config
        self._cache = cache
        self._draft_manager = draft_manager

    @property
    def config(self) -> ClerkConfig:
        if self._config is None:
            self._config = get_config()
        return self._config

    @property
    def cache(self) -> Cache:
        if self._cache is None:
            self._cache = get_cache()
        return self._cache

    @property
    def drafts(self) -> DraftManager:
        if self._draft_manager is None:
            self._draft_manager = get_draft_manager()
        return self._draft_manager

    # =========================================================================
    # Inbox & Message Operations
    # =========================================================================

    def _ensure_body(self, msg: Message, fresh: bool) -> None:
        """Fetch the body from IMAP if the cache has none or is stale.

        Cache hit with body_text=None is only trusted if body_fetched_at was
        recent — otherwise the fetch is retried. Prevents the "None-forever"
        cache bug.
        """
        needs_fetch = msg.body_text is None and (
            fresh
            or not self.cache.is_fresh(
                msg.message_id,
                self.config.cache.body_freshness_min,
                check_body=True,
            )
        )
        if not needs_fetch:
            return

        with get_imap_client(msg.account) as client:
            body_text, body_html = client.fetch_message_body(msg.folder, msg.message_id)
            if body_text is None and body_html:
                body_text = html_to_text(body_html)
            self.cache.update_body(msg.message_id, body_text, body_html)
            msg.body_text = body_text
            msg.body_html = body_html

    def get_conversation(
        self, conv_id: str, fresh: bool = False
    ) -> Conversation | None:
        """Get a conversation by ID, fetching bodies as needed."""
        conv = self.cache.get_conversation(conv_id)
        if conv:
            for msg in conv.messages:
                self._ensure_body(msg, fresh)
        return conv

    def get_message(self, message_id: str, fresh: bool = False) -> Message | None:
        """Get a single message by ID, fetching body as needed."""
        msg = self.cache.get_message(message_id)
        if msg:
            self._ensure_body(msg, fresh)
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
        """Create a new draft message."""
        account_name, _ = self.config.get_account(account)

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
        """Create a reply draft to an existing message."""
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
        return self.drafts.get(draft_id)

    def list_drafts(self, account: str | None = None) -> list[Draft]:
        return self.drafts.list(account=account)

    def update_draft(self, draft: Draft) -> None:
        self.drafts.update(draft)

    def delete_draft(self, draft_id: str) -> bool:
        return self.drafts.delete(draft_id)

    # =========================================================================
    # Send confirmation tokens (persistent, content-bound, single-use)
    # =========================================================================

    def generate_send_token(
        self, draft: Draft, ttl_seconds: int = SEND_TOKEN_TTL_SECONDS
    ) -> str:
        """Mint a send-confirmation token bound to the draft's content.

        Stored in ``cache_meta`` as a SHA256 hash (not the raw token) along
        with the draft's content hash and an absolute expiry timestamp.
        Survives MCP server restarts; consumed by ``consume_send_token``.
        """
        token = secrets.token_hex(16)
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        payload = json.dumps(
            {
                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                "content_hash": draft_content_hash(draft),
                "expires_at": expires_at.isoformat(),
            }
        )
        self.cache.set_meta(_SEND_TOKEN_PREFIX + draft.draft_id, payload)
        return token

    def consume_send_token(
        self, draft: Draft, token: str
    ) -> tuple[bool, str | None]:
        """Validate and single-use a send-confirmation token.

        Returns ``(True, None)`` on success. On any failure the token is
        removed and a clear reason is returned. Failure modes: missing,
        expired, wrong token, draft mutated after preview.
        """
        key = _SEND_TOKEN_PREFIX + draft.draft_id
        raw = self.cache.get_meta(key)
        if not raw:
            return (
                False,
                "No confirmation token found. Call clerk_send without a "
                "token first to get one.",
            )

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            self.cache.delete_meta(key)
            return False, "Corrupt confirmation token — please retry."

        try:
            expires_at = datetime.fromisoformat(data["expires_at"])
        except (KeyError, ValueError):
            self.cache.delete_meta(key)
            return False, "Corrupt confirmation token — please retry."

        if datetime.now(UTC) > expires_at:
            self.cache.delete_meta(key)
            return (
                False,
                "Confirmation token expired. Call clerk_send again to get a "
                "fresh preview and token.",
            )

        expected = data.get("token_hash", "")
        actual = hashlib.sha256(token.encode()).hexdigest()
        if not secrets.compare_digest(actual, expected):
            return False, "Invalid confirmation token."

        # Content binding: if the draft was edited between preview and send,
        # the hash differs and we refuse to send the mutated draft.
        if data.get("content_hash") != draft_content_hash(draft):
            self.cache.delete_meta(key)
            return (
                False,
                "Draft content changed after preview. Re-run clerk_send to "
                "review the new contents and get a fresh token.",
            )

        # Single-use: consume the token after successful validation.
        self.cache.delete_meta(key)
        return True, None

    # =========================================================================
    # Send (single choke point for all outbound email)
    # =========================================================================

    def check_send_allowed(
        self, draft: Draft, account_name: str
    ) -> tuple[bool, str | None]:
        """Validate a draft is safe to send.

        Enforces: persistent rate limit (from ``send_log``), blocked recipients,
        and draft/account match. Used by both the API send path and MCP's
        two-step preview.
        """
        send_config = self.config.send

        # Persistent rate limit: count rows in send_log within the last hour.
        hour_ago = datetime.now(UTC) - timedelta(hours=1)
        recent = self.cache.count_sends_since(account_name, hour_ago)
        if recent >= send_config.rate_limit:
            return False, (
                f"Rate limit exceeded: {recent}/{send_config.rate_limit} "
                f"sends in the last hour"
            )

        blocked = {addr.lower() for addr in send_config.blocked_recipients}
        for addr in draft.to + draft.cc + draft.bcc:
            if addr.addr.lower() in blocked:
                return False, f"Recipient {addr.addr} is blocked"

        if draft.account != account_name:
            return (
                False,
                f"Draft account '{draft.account}' doesn't match '{account_name}'",
            )

        return True, None

    async def send_draft_async(self, draft_id: str) -> SendResult:
        """Send a draft (single authoritative send path).

        Orchestrates policy checks, SMTP transport, audit log, and draft
        deletion. Two-step confirmation (persistent tokens bound to content
        hash) lives one layer up in the MCP server — this method assumes its
        caller has already established a human/LLM decision to send.
        """
        draft = self.drafts.get(draft_id)
        if not draft:
            return SendResult(success=False, error=f"Draft not found: {draft_id}")

        try:
            name, account_config = self.config.get_account(draft.account)
        except ValueError as e:
            return SendResult(success=False, error=str(e))

        allowed, error = self.check_send_allowed(draft, name)
        if not allowed:
            return SendResult(success=False, error=error)

        client = SmtpClient(name, account_config)
        result = await client.send_async(draft)

        if result.success:
            # Audit log is best-effort: a disk-full error here should not
            # swallow a successful send. Log to stderr and continue.
            try:
                self.cache.log_send(
                    account=name,
                    to=draft.to,
                    cc=draft.cc,
                    bcc=draft.bcc,
                    subject=draft.subject,
                    message_id=result.message_id,
                )
            except Exception as e:
                print(
                    f"Warning: audit log write failed after send: {e}",
                    file=sys.stderr,
                )

            try:
                self.drafts.delete(draft_id)
            except Exception as e:
                print(
                    f"Warning: draft delete failed after send: {e}",
                    file=sys.stderr,
                )

        return result

    def send_draft(self, draft_id: str) -> SendResult:
        """Synchronous wrapper for send_draft_async.

        Fails inside a running event loop; use send_draft_async there.
        """
        return asyncio.run(self.send_draft_async(draft_id))

    # =========================================================================
    # Message Actions
    # =========================================================================

    def set_flag(
        self,
        message_id: str,
        flag: MessageFlag,
        on: bool,
        account: str | None = None,
    ) -> None:
        """Add or remove a flag on a message.

        Server-first: IMAP call is authoritative. Cache write failures are
        logged but not raised — the cache self-heals on next sync.
        """
        account_name, _ = self.config.get_account(account)

        msg = self.cache.get_message(message_id)
        folder = msg.folder if msg else "INBOX"

        with get_imap_client(account_name) as client:
            if on:
                client.add_flags(folder, message_id, [flag])
            else:
                client.remove_flags(folder, message_id, [flag])

        if msg:
            try:
                if on:
                    flags = list(msg.flags)
                    if flag not in flags:
                        flags.append(flag)
                else:
                    flags = [f for f in msg.flags if f != flag]
                self.cache.update_flags(message_id, flags)
            except Exception as e:
                print(
                    f"Warning: cache update failed after IMAP flag change "
                    f"({flag.value}={on}): {e}. Will self-heal on next sync.",
                    file=sys.stderr,
                )

    def mark_read(self, message_id: str, account: str | None = None) -> None:
        """Mark a message as read."""
        self.set_flag(message_id, MessageFlag.SEEN, True, account=account)

    def mark_unread(self, message_id: str, account: str | None = None) -> None:
        """Mark a message as unread."""
        self.set_flag(message_id, MessageFlag.SEEN, False, account=account)

    def flag_message(self, message_id: str, account: str | None = None) -> None:
        """Flag/star a message."""
        self.set_flag(message_id, MessageFlag.FLAGGED, True, account=account)

    def unflag_message(self, message_id: str, account: str | None = None) -> None:
        """Remove flag from a message."""
        self.set_flag(message_id, MessageFlag.FLAGGED, False, account=account)

    def move_message(
        self,
        message_id: str,
        to_folder: str,
        from_folder: str = "INBOX",
        account: str | None = None,
    ) -> None:
        """Move a message to another folder (server-first, cache best-effort)."""
        account_name, _ = self.config.get_account(account)

        with get_imap_client(account_name) as client:
            client.move_message(message_id, from_folder, to_folder)

        try:
            self.cache.move_message(message_id, to_folder)
        except Exception as e:
            print(
                f"Warning: cache update failed after IMAP move to {to_folder}: "
                f"{e}. Will self-heal on next sync.",
                file=sys.stderr,
            )

    def archive_message(self, message_id: str, account: str | None = None) -> None:
        """Archive a message (server-first, cache best-effort)."""
        account_name, _ = self.config.get_account(account)

        with get_imap_client(account_name) as client:
            client.archive_message(message_id)

        try:
            self.cache.move_message(message_id, "Archive")
        except Exception as e:
            print(
                f"Warning: cache update failed after IMAP archive: {e}. "
                f"Will self-heal on next sync.",
                file=sys.stderr,
            )

    # =========================================================================
    # Folder Operations
    # =========================================================================

    def list_folders(self, account: str | None = None) -> list[FolderInfo]:
        account_name, _ = self.config.get_account(account)
        with get_imap_client(account_name) as client:
            return client.list_folders()

    def get_unread_counts(self, account: str | None = None) -> UnreadCounts:
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

        Advances sync state only when store succeeds; on store failure the
        sync state is left alone so the next sync retries the same UIDs.
        """
        account_name, _ = self.config.get_account(account)

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

        if highest_uid > since_uid:
            self.cache.set_sync_state(account_name, folder, highest_uid)

        # Only mark the folder as "freshly synced" when we actually saw the
        # server (not on partial-fetch failure before store). Storing above
        # is inside the `with` block; reaching here means it completed.
        self.cache.mark_inbox_synced(account_name)

        return {
            "synced": len(messages),
            "account": account_name,
            "folder": folder,
            "last_uid": highest_uid,
        }

    def sync_all(
        self, folder: str = "INBOX", full: bool = False
    ) -> dict[str, Any]:
        """Sync the given folder across all configured accounts."""
        results: dict[str, Any] = {"accounts": {}, "total_synced": 0}
        for acct_name in self.config.accounts:
            try:
                result = self.sync_folder(
                    account=acct_name, folder=folder, full=full
                )
                results["accounts"][acct_name] = result
                results["total_synced"] += result["synced"]
            except Exception as e:
                results["accounts"][acct_name] = {"error": str(e)}
        return results

    # =========================================================================
    # Cache & Status Operations
    # =========================================================================

    def get_cache_stats(self) -> CacheStats:
        return self.cache.get_stats()

    def clear_cache(self) -> None:
        self.cache.clear()

    def get_status(self) -> dict[str, Any]:
        """Get overall status: version + per-account connection health."""
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
