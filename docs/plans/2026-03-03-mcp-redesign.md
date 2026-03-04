# MCP Server Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Simplify clerk's MCP server from 17 tools to 8, using SQL as the universal read interface, adding reply capability, incremental sync, and priority config.

**Architecture:** Rewrite `mcp_server.py` with 8 focused tools + 3 resources. Add `sync_state` table for UID-based incremental sync. Add `clerk_reply` tool that auto-populates reply headers using existing `DraftManager.create_reply()`. Add `priorities` config section exposed via `clerk://config` resource. Remove skill module entirely.

**Tech Stack:** Python 3.11+, FastMCP, SQLite, imapclient, pydantic

**Design doc:** `docs/plans/2026-03-03-mcp-redesign-design.md`

---

### Task 1: Add `sync_state` table to cache schema

**Files:**
- Modify: `src/clerk/cache.py:23-126` (SCHEMA constant)
- Modify: `src/clerk/cache.py:558-600` (add sync state methods)
- Test: `tests/test_cache.py`

**Step 1: Write the failing tests**

Add to `tests/test_cache.py`:

```python
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
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache.py::TestSyncState -v`
Expected: FAIL — `Cache` has no `get_sync_state` or `set_sync_state`

**Step 3: Add sync_state table and methods**

In `src/clerk/cache.py`, add to the SCHEMA constant (after the `cache_meta` table, around line 113):

```sql
-- Sync state for incremental IMAP fetching
CREATE TABLE IF NOT EXISTS sync_state (
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    last_uid INTEGER DEFAULT 0,
    last_sync_utc TEXT NOT NULL,
    PRIMARY KEY (account, folder)
);
```

Add two methods to the `Cache` class (after `get_stats`, around line 728):

```python
def get_sync_state(self, account: str, folder: str) -> dict[str, Any] | None:
    """Get the sync state for an account/folder pair."""
    with self._connect() as conn:
        row = conn.execute(
            "SELECT * FROM sync_state WHERE account = ? AND folder = ?",
            (account, folder),
        ).fetchone()
        if row:
            return dict(row)
    return None

def set_sync_state(self, account: str, folder: str, last_uid: int) -> None:
    """Update the sync state for an account/folder pair."""
    with self._connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO sync_state (account, folder, last_uid, last_sync_utc)
            VALUES (?, ?, ?, ?)
            """,
            (account, folder, last_uid, datetime.now(UTC).isoformat()),
        )
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache.py::TestSyncState -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add src/clerk/cache.py tests/test_cache.py
git commit -m "feat(cache): add sync_state table for incremental IMAP sync"
```

---

### Task 2: Add `priorities` config model

**Files:**
- Modify: `src/clerk/config.py:138-160` (add PrioritiesConfig, add to ClerkConfig)
- Test: `tests/test_config.py`

**Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
class TestPrioritiesConfig:
    def test_empty_priorities_by_default(self):
        config = ClerkConfig()
        assert config.priorities.senders == []
        assert config.priorities.topics == []

    def test_priorities_from_yaml(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
default_account: test
accounts:
  test:
    protocol: imap
    imap:
      host: localhost
      port: 993
      username: test
    smtp:
      host: localhost
      port: 587
      username: test
    from:
      address: test@example.com
priorities:
  senders:
    - "alice@example.com"
    - "@siue.edu"
  topics:
    - "IDOT"
    - "scanner"
""")
        config = load_config(config_file)
        assert config.priorities.senders == ["alice@example.com", "@siue.edu"]
        assert config.priorities.topics == ["IDOT", "scanner"]
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py::TestPrioritiesConfig -v`
Expected: FAIL — `ClerkConfig` has no `priorities` attribute

**Step 3: Add PrioritiesConfig model**

In `src/clerk/config.py`, add after `SendConfig` (around line 152):

```python
class PrioritiesConfig(BaseModel):
    """Priority filtering configuration for LLM agents."""

    senders: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
```

In the `ClerkConfig` class, add the field (around line 160):

```python
priorities: PrioritiesConfig = Field(default_factory=PrioritiesConfig)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py::TestPrioritiesConfig -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add src/clerk/config.py tests/test_config.py
git commit -m "feat(config): add priorities section for important senders/topics"
```

---

### Task 3: Add `clerk_sync` API method with incremental UID-based sync

**Files:**
- Modify: `src/clerk/api.py` (add `sync_folder` method)
- Modify: `src/clerk/imap_client.py` (add `fetch_messages_since_uid` method)
- Test: `tests/test_api.py`

**Step 1: Write the failing tests**

Add to `tests/test_api.py`:

```python
class TestSyncFolder:
    def test_incremental_sync_returns_count(self, tmp_path):
        """Sync should return number of new messages fetched."""
        cache = Cache(tmp_path / "test.db")
        config = ClerkConfig(
            accounts={
                "test": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="localhost", username="test"),
                    smtp=SmtpConfig(host="localhost", username="test"),
                    **{"from": FromAddress(address="test@example.com")},
                ),
            },
            default_account="test",
        )
        api = ClerkAPI(config=config, cache=cache)

        with patch("clerk.api.get_imap_client") as mock_imap:
            mock_client = MagicMock()
            mock_imap.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_imap.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.fetch_messages_since_uid.return_value = ([], 0)

            result = api.sync_folder(account="test", folder="INBOX")
            assert result["synced"] == 0
            assert result["account"] == "test"
            assert result["folder"] == "INBOX"

    def test_incremental_sync_updates_sync_state(self, tmp_path):
        """Sync should update the last_uid in sync_state."""
        cache = Cache(tmp_path / "test.db")
        config = ClerkConfig(
            accounts={
                "test": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="localhost", username="test"),
                    smtp=SmtpConfig(host="localhost", username="test"),
                    **{"from": FromAddress(address="test@example.com")},
                ),
            },
            default_account="test",
        )
        api = ClerkAPI(config=config, cache=cache)

        msg = Message(
            message_id="<msg1@example.com>",
            conv_id="abc123",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com", name="Alice")},
            to=[Address(addr="test@example.com", name="Test")],
            subject="Test",
            date=datetime.now(UTC),
        )

        with patch("clerk.api.get_imap_client") as mock_imap:
            mock_client = MagicMock()
            mock_imap.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_imap.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.fetch_messages_since_uid.return_value = ([msg], 100)

            result = api.sync_folder(account="test", folder="INBOX")
            assert result["synced"] == 1

            state = cache.get_sync_state("test", "INBOX")
            assert state is not None
            assert state["last_uid"] == 100
```

Note: You'll need to import `datetime` from `datetime`, `UTC` from `datetime`, `Cache`, `ClerkConfig`, `AccountConfig`, `ImapConfig`, `SmtpConfig`, `FromAddress`, `Address`, `Message`, `ClerkAPI` and `patch` from `unittest.mock`. Check the existing imports at the top of `tests/test_api.py` and add what's missing.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py::TestSyncFolder -v`
Expected: FAIL — `ClerkAPI` has no `sync_folder` method

**Step 3: Add `fetch_messages_since_uid` to ImapClient**

In `src/clerk/imap_client.py`, add a method to `ImapClient` class (after `fetch_messages`, around line 360):

```python
def fetch_messages_since_uid(
    self,
    folder: str = "INBOX",
    since_uid: int = 0,
    fetch_bodies: bool = False,
) -> tuple[list[Message], int]:
    """Fetch messages with UID greater than since_uid.

    Returns:
        Tuple of (messages, highest_uid_seen)
    """
    self.client.select_folder(folder, readonly=True)

    if since_uid > 0:
        # Fetch UIDs greater than the last known UID
        message_uids = self.client.search(["UID", f"{since_uid + 1}:*"])
        # Filter out the since_uid itself (IMAP ranges are inclusive)
        message_uids = [uid for uid in message_uids if uid > since_uid]
    else:
        # First sync — get recent messages
        message_uids = self.client.search(["ALL"])
        message_uids = sorted(message_uids, reverse=True)[:200]

    if not message_uids:
        return [], since_uid

    # Determine what to fetch
    fetch_items = ["FLAGS", "ENVELOPE", "INTERNALDATE", "RFC822.SIZE"]
    if fetch_bodies:
        fetch_items.append("BODY.PEEK[]")
    else:
        fetch_items.append("BODY.PEEK[HEADER]")

    fetch_data = self.client.fetch(message_uids, fetch_items)

    messages = []
    highest_uid = since_uid

    for uid in sorted(fetch_data.keys()):
        if uid > highest_uid:
            highest_uid = uid
        try:
            msg = self._parse_message(uid, fetch_data[uid], folder, fetch_bodies)
            messages.append(msg)
        except Exception as e:
            import sys
            print(f"Warning: Failed to parse message {uid}: {e}", file=sys.stderr)

    return messages, highest_uid
```

Note: the existing `fetch_messages` method already has a `_parse_message` helper-like flow inline (around lines 360-470). You'll need to extract the message parsing logic at lines 360-470 into a `_parse_message` method. Look at the existing code carefully — the variables `self.account_name`, the `has_body` check, and the `Message(...)` constructor call. Extract it so both `fetch_messages` and `fetch_messages_since_uid` can reuse it.

**Step 4: Add `sync_folder` to ClerkAPI**

In `src/clerk/api.py`, add after `refresh_cache` method (around line 686):

```python
def sync_folder(
    self,
    account: str | None = None,
    folder: str = "INBOX",
    full: bool = False,
) -> dict[str, Any]:
    """Sync a folder from IMAP, fetching only new messages.

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

    # Update sync state
    if highest_uid > since_uid:
        self.cache.set_sync_state(account_name, folder, highest_uid)

    self.cache.mark_inbox_synced(account_name)

    return {
        "synced": len(messages),
        "account": account_name,
        "folder": folder,
    }
```

**Step 5: Run tests to verify they pass**

Run: `pytest tests/test_api.py::TestSyncFolder -v`
Expected: PASS (2 tests)

**Step 6: Commit**

```bash
git add src/clerk/imap_client.py src/clerk/api.py tests/test_api.py
git commit -m "feat(api): add sync_folder with incremental UID-based IMAP sync"
```

---

### Task 4: Rewrite `mcp_server.py` — 8 tools + 3 resources

This is the core task. Rewrite `src/clerk/mcp_server.py` completely.

**Files:**
- Rewrite: `src/clerk/mcp_server.py`
- Create: `tests/test_mcp_redesign.py` (new test file for the new MCP server)
- Delete later (Task 7): `tests/test_mcp_server.py`, `tests/test_mcp_mutations.py`

**Step 1: Write the failing tests**

Create `tests/test_mcp_redesign.py`:

```python
"""Tests for the redesigned MCP server (8 tools + 3 resources)."""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from clerk.cache import Cache
from clerk.config import (
    AccountConfig,
    ClerkConfig,
    FromAddress,
    ImapConfig,
    PrioritiesConfig,
    SmtpConfig,
)
from clerk.models import Address, Message, MessageFlag


@pytest.fixture
def populated_cache(tmp_path):
    """Create a cache with test messages."""
    cache = Cache(tmp_path / "test.db")
    msg1 = Message(
        message_id="<msg1@example.com>",
        conv_id="conv001",
        account="test",
        folder="INBOX",
        **{"from": Address(addr="alice@example.com", name="Alice")},
        to=[Address(addr="test@example.com", name="Test")],
        subject="Hello",
        date=datetime(2026, 3, 1, tzinfo=UTC),
        body_text="Hello body",
        flags=[],
    )
    msg2 = Message(
        message_id="<msg2@example.com>",
        conv_id="conv001",
        account="test",
        folder="INBOX",
        **{"from": Address(addr="test@example.com", name="Test")},
        to=[Address(addr="alice@example.com", name="Alice")],
        subject="Re: Hello",
        date=datetime(2026, 3, 2, tzinfo=UTC),
        body_text="Reply body",
        flags=[MessageFlag.SEEN],
        in_reply_to="<msg1@example.com>",
        references=["<msg1@example.com>"],
    )
    cache.store_message(msg1)
    cache.store_message(msg2)
    return cache


@pytest.fixture
def mock_config():
    return ClerkConfig(
        accounts={
            "test": AccountConfig(
                protocol="imap",
                imap=ImapConfig(host="localhost", username="test"),
                smtp=SmtpConfig(host="localhost", username="test"),
                **{"from": FromAddress(address="test@example.com", name="Test User")},
            ),
        },
        default_account="test",
        priorities=PrioritiesConfig(
            senders=["alice@example.com", "@siue.edu"],
            topics=["IDOT", "scanner"],
        ),
    )


# --- clerk_sql ---

class TestClerkSql:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_select_returns_rows(self, _dirs, mock_get_api, populated_cache):
        from clerk.mcp_server import clerk_sql

        mock_api = MagicMock()
        mock_api.cache = populated_cache
        mock_get_api.return_value = mock_api

        result = clerk_sql(query="SELECT message_id, subject FROM messages ORDER BY date_utc")
        assert result["count"] == 2
        assert result["rows"][0]["subject"] == "Hello"

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_rejects_non_select(self, _dirs, mock_get_api, populated_cache):
        from clerk.mcp_server import clerk_sql

        mock_api = MagicMock()
        mock_api.cache = populated_cache
        mock_get_api.return_value = mock_api

        result = clerk_sql(query="DELETE FROM messages")
        assert "error" in result


# --- clerk_sync ---

class TestClerkSync:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_sync_calls_api(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_sync

        mock_api = MagicMock()
        mock_api.sync_folder.return_value = {"synced": 5, "account": "test", "folder": "INBOX"}
        mock_get_api.return_value = mock_api

        result = clerk_sync(account="test")
        assert result["synced"] == 5
        mock_api.sync_folder.assert_called_once_with(account="test", folder="INBOX", full=False)

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_sync_full(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_sync

        mock_api = MagicMock()
        mock_api.sync_folder.return_value = {"synced": 100, "account": "test", "folder": "INBOX"}
        mock_get_api.return_value = mock_api

        result = clerk_sync(account="test", full=True)
        mock_api.sync_folder.assert_called_once_with(account="test", folder="INBOX", full=True)


# --- clerk_reply ---

class TestClerkReply:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_reply_creates_draft(self, _dirs, mock_get_api, populated_cache):
        from clerk.mcp_server import clerk_reply

        mock_api = MagicMock()
        mock_api.cache = populated_cache

        # Mock create_draft to return a draft
        mock_draft = MagicMock()
        mock_draft.draft_id = "draft_abc"
        mock_draft.to = [Address(addr="alice@example.com", name="Alice")]
        mock_draft.cc = []
        mock_draft.subject = "Re: Hello"
        mock_draft.body_text = "Thanks for your message!"
        mock_api.create_draft.return_value = mock_draft
        mock_get_api.return_value = mock_api

        result = clerk_reply(
            message_id="<msg1@example.com>",
            body="Thanks for your message!",
        )

        assert result["draft_id"] == "draft_abc"
        assert "preview" in result
        assert result["subject"] == "Re: Hello"

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_reply_message_not_found(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_reply

        mock_api = MagicMock()
        mock_api.cache.get_message.return_value = None
        mock_get_api.return_value = mock_api

        result = clerk_reply(message_id="<nonexistent>", body="test")
        assert "error" in result


# --- clerk_draft ---

class TestClerkDraft:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_draft_creates_new_message(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_draft

        mock_draft = MagicMock()
        mock_draft.draft_id = "draft_xyz"
        mock_draft.to = [Address(addr="bob@example.com", name="")]
        mock_draft.cc = []
        mock_draft.subject = "New message"
        mock_draft.body_text = "Hello Bob"

        mock_api = MagicMock()
        mock_api.create_draft.return_value = mock_draft
        mock_get_api.return_value = mock_api

        result = clerk_draft(to="bob@example.com", subject="New message", body="Hello Bob")
        assert result["draft_id"] == "draft_xyz"
        assert "preview" in result


# --- clerk_send ---

class TestClerkSend:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_send_step1_returns_token(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_send

        mock_draft = MagicMock()
        mock_draft.account = "test"
        mock_draft.to = [Address(addr="bob@example.com", name="Bob")]
        mock_draft.cc = []
        mock_draft.subject = "Test"
        mock_draft.body_text = "Test body"

        mock_api = MagicMock()
        mock_api.get_draft.return_value = mock_draft
        mock_get_api.return_value = mock_api

        with patch("clerk.mcp_server.check_send_allowed", return_value=(True, None)):
            with patch("clerk.mcp_server.format_draft_preview", return_value="Preview text"):
                result = clerk_send(draft_id="draft_1")

        assert result["status"] == "pending_confirmation"
        assert "token" in result


# --- clerk_move ---

class TestClerkMoveRedesign:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_move_success(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_move

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api

        result = clerk_move(message_id="<msg1>", to_folder="Archive")
        assert result["status"] == "success"
        mock_api.move_message.assert_called_once()


# --- clerk_flag ---

class TestClerkFlagRedesign:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_flag_action(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_flag

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api

        result = clerk_flag(message_id="<msg1>", action="flag")
        assert result["status"] == "success"
        mock_api.flag_message.assert_called_once()

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_read_action(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_flag

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api

        result = clerk_flag(message_id="<msg1>", action="read")
        assert result["status"] == "success"
        mock_api.mark_read.assert_called_once()

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_invalid_action(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_flag

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api

        result = clerk_flag(message_id="<msg1>", action="invalid")
        assert "error" in result


# --- clerk_status ---

class TestClerkStatusRedesign:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_status(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_status

        mock_api = MagicMock()
        mock_api.get_status.return_value = {"version": "0.6.0", "accounts": {}}
        mock_get_api.return_value = mock_api

        result = clerk_status()
        assert "version" in result


# --- Resources ---

class TestResources:
    @patch("clerk.mcp_server.get_config")
    def test_schema_resource(self, mock_get_config):
        from clerk.mcp_server import resource_schema

        result = resource_schema()
        assert "messages" in result
        assert "messages_fts" in result
        assert "SELECT" in result  # Example queries

    @patch("clerk.mcp_server.get_config")
    def test_config_resource(self, mock_get_config, mock_config):
        from clerk.mcp_server import resource_config

        mock_get_config.return_value = mock_config
        result = resource_config()
        data = json.loads(result)
        assert "accounts" in data
        assert "priorities" in data
        assert data["default_account"] == "test"
        # Verify sensitive fields are redacted
        assert "password" not in json.dumps(data)

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.get_config")
    def test_folders_resource(self, mock_get_config, mock_get_api, mock_config):
        from clerk.mcp_server import resource_folders

        mock_get_config.return_value = mock_config
        mock_api = MagicMock()
        mock_api.list_folders.return_value = [
            MagicMock(name="INBOX"), MagicMock(name="Sent"),
        ]
        mock_get_api.return_value = mock_api

        result = resource_folders()
        data = json.loads(result)
        assert "test" in data
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp_redesign.py -v`
Expected: FAIL — imports will fail since the new functions don't exist yet

**Step 3: Rewrite `mcp_server.py`**

Replace the entire content of `src/clerk/mcp_server.py` with the new 8-tool + 3-resource implementation. The key structure:

```python
"""MCP Server for clerk — 8 tools + 3 resources for LLM email agents."""

import json
import secrets
import time
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from .api import get_api
from .cache import SCHEMA
from .config import ensure_dirs, get_config
from .smtp_client import check_send_allowed, format_draft_preview

mcp = FastMCP(name="clerk")

# Confirmation token storage
_confirmation_tokens: dict[str, tuple[str, float]] = {}
CONFIRMATION_TOKEN_EXPIRY_SECONDS = 300


def _generate_confirmation_token(draft_id: str) -> str:
    token = secrets.token_hex(16)
    _confirmation_tokens[draft_id] = (token, time.time() + CONFIRMATION_TOKEN_EXPIRY_SECONDS)
    return token


def _validate_confirmation_token(draft_id: str, token: str) -> tuple[bool, str | None]:
    if draft_id not in _confirmation_tokens:
        return False, "No confirmation token found. Call clerk_send without token first."
    stored_token, expiry = _confirmation_tokens[draft_id]
    if time.time() > expiry:
        del _confirmation_tokens[draft_id]
        return False, "Confirmation token expired. Call clerk_send again to get a new one."
    if not secrets.compare_digest(token, stored_token):
        return False, "Invalid confirmation token."
    del _confirmation_tokens[draft_id]
    return True, None


def _cleanup_expired_tokens() -> None:
    now = time.time()
    expired = [k for k, (_, exp) in _confirmation_tokens.items() if now > exp]
    for k in expired:
        del _confirmation_tokens[k]


# ============================================================================
# Tools (8)
# ============================================================================


@mcp.tool()
def clerk_sql(
    query: str,
    limit: int = 100,
) -> dict[str, Any]:
    """Execute a readonly SQL SELECT query on the clerk email cache.

    Use the clerk://schema resource to discover tables and columns.
    Returns raw rows as JSON dicts.

    Args:
        query: SQL SELECT query (only SELECT is allowed)
        limit: Maximum results (default: 100)

    Returns:
        Dictionary with rows (list of dicts) and count, or error
    """
    ensure_dirs()
    api = get_api()
    try:
        rows = api.cache.execute_readonly_sql(query, limit=limit)
        return {"rows": rows, "count": len(rows)}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"SQL error: {e}"}


@mcp.tool()
def clerk_sync(
    account: str | None = None,
    folder: str = "INBOX",
    full: bool = False,
) -> dict[str, Any]:
    """Sync email cache from IMAP server.

    By default, only fetches new messages since last sync (incremental).
    Use full=True to re-fetch everything in the folder.

    Args:
        account: Account name (syncs default account if not specified)
        folder: Folder to sync (default: INBOX)
        full: Re-fetch all messages instead of incremental sync

    Returns:
        Dictionary with synced count, account, folder
    """
    ensure_dirs()
    api = get_api()
    try:
        return api.sync_folder(account=account, folder=folder, full=full)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def clerk_reply(
    message_id: str,
    body: str,
    reply_all: bool = False,
    account: str | None = None,
) -> dict[str, Any]:
    """Reply to an email message.

    Auto-populates To, Cc (if reply_all), Subject, In-Reply-To, and References.
    Creates a draft and returns a preview for user confirmation.
    If the user approves, call clerk_send with the draft_id to send.

    Args:
        message_id: Message ID to reply to
        body: Reply body text
        reply_all: Include all original recipients in reply
        account: Account to send from (uses default if not specified)

    Returns:
        Dictionary with draft_id, preview, to, cc, subject for user confirmation,
        or error if message not found
    """
    ensure_dirs()
    api = get_api()

    # Find the original message to get its conversation
    msg = api.cache.get_message(message_id)
    if not msg:
        return {"error": f"Message not found: {message_id}. Try running clerk_sync first."}

    try:
        # Use the existing create_draft with reply_to_conv_id
        # But we need to handle reply_all properly
        draft = api.drafts.create_reply(
            account=account or msg.account,
            conv_id=msg.conv_id,
            body_text=body,
            reply_all=reply_all,
        )

        preview = f"To: {', '.join(str(a) for a in draft.to)}\n"
        if draft.cc:
            preview += f"Cc: {', '.join(str(a) for a in draft.cc)}\n"
        preview += f"Subject: {draft.subject}\n\n{draft.body_text}"

        return {
            "draft_id": draft.draft_id,
            "to": [str(a) for a in draft.to],
            "cc": [str(a) for a in draft.cc],
            "subject": draft.subject,
            "preview": preview,
            "message": "Show this preview to the user. If they approve, call clerk_send to send.",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def clerk_draft(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Compose a new email (not a reply).

    Creates a draft and returns a preview for user confirmation.
    If the user approves, call clerk_send with the draft_id to send.

    Args:
        to: Recipient email address (or comma-separated list)
        subject: Subject line
        body: Message body text
        cc: CC recipients (comma-separated, optional)
        account: Account to send from (uses default if not specified)

    Returns:
        Dictionary with draft_id and preview for user confirmation
    """
    ensure_dirs()
    api = get_api()

    to_addrs = [a.strip() for a in to.split(",")]
    cc_addrs = [a.strip() for a in cc.split(",")] if cc else None

    try:
        draft = api.create_draft(
            to=to_addrs,
            subject=subject,
            body=body,
            cc=cc_addrs,
            account=account,
        )

        preview = f"To: {', '.join(str(a) for a in draft.to)}\n"
        if draft.cc:
            preview += f"Cc: {', '.join(str(a) for a in draft.cc)}\n"
        preview += f"Subject: {draft.subject}\n\n{draft.body_text}"

        return {
            "draft_id": draft.draft_id,
            "preview": preview,
            "message": "Show this preview to the user. If they approve, call clerk_send to send.",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def clerk_send(
    draft_id: str,
    token: str | None = None,
) -> dict[str, Any]:
    """Send a draft email with two-step confirmation.

    Step 1: Call without token — returns preview and a confirmation token.
    Step 2: Call with the token — actually sends the email.

    Args:
        draft_id: ID of the draft to send
        token: Confirmation token from step 1 (required for step 2)

    Returns:
        Step 1: Preview + token (valid 5 minutes)
        Step 2: Send result with message_id
    """
    ensure_dirs()
    _cleanup_expired_tokens()
    api = get_api()

    draft = api.get_draft(draft_id)
    if not draft:
        return {"error": f"Draft not found: {draft_id}"}

    if token is None:
        # Step 1: generate preview and token
        allowed, error = check_send_allowed(draft, draft.account)
        if not allowed:
            return {"error": error}

        confirmation_token = _generate_confirmation_token(draft_id)

        return {
            "status": "pending_confirmation",
            "preview": format_draft_preview(draft),
            "token": confirmation_token,
            "expires_in_seconds": CONFIRMATION_TOKEN_EXPIRY_SECONDS,
            "message": "Call clerk_send again with this token to send.",
        }

    # Step 2: validate and send
    valid, error = _validate_confirmation_token(draft_id, token)
    if not valid:
        return {"error": error}

    result = api.send_draft(draft_id, skip_confirmation=True)

    if result.success:
        return {
            "status": "sent",
            "message_id": result.message_id,
            "timestamp": result.timestamp.isoformat(),
        }
    return {"status": "failed", "error": result.error}


@mcp.tool()
def clerk_move(
    message_id: str,
    to_folder: str,
    from_folder: str = "INBOX",
    account: str | None = None,
) -> dict[str, Any]:
    """Move an email message to another folder.

    Use clerk://folders resource to see available folders.

    Args:
        message_id: Message ID to move
        to_folder: Destination folder (e.g., "Archive", "Trash")
        from_folder: Source folder (default: INBOX)
        account: Account name (uses default if not specified)

    Returns:
        Dictionary with success status
    """
    ensure_dirs()
    api = get_api()
    try:
        api.move_message(message_id, to_folder, from_folder=from_folder, account=account)
        return {"status": "success", "message_id": message_id, "folder": to_folder}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def clerk_flag(
    message_id: str,
    action: Literal["flag", "unflag", "read", "unread"],
    account: str | None = None,
) -> dict[str, Any]:
    """Flag/unflag or mark read/unread on an email message.

    Args:
        message_id: Message ID
        action: One of "flag", "unflag", "read", "unread"
        account: Account name (uses default if not specified)

    Returns:
        Dictionary with success status
    """
    ensure_dirs()
    api = get_api()
    try:
        if action == "flag":
            api.flag_message(message_id, account=account)
        elif action == "unflag":
            api.unflag_message(message_id, account=account)
        elif action == "read":
            api.mark_read(message_id, account=account)
        elif action == "unread":
            api.mark_unread(message_id, account=account)
        else:
            return {"error": f"Invalid action: {action}. Use flag, unflag, read, or unread."}
        return {"status": "success", "message_id": message_id, "action": action}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def clerk_status() -> dict[str, Any]:
    """Get clerk status — version, accounts, connection health.

    Returns:
        Dictionary with version and per-account connection status
    """
    ensure_dirs()
    api = get_api()
    return api.get_status()


# ============================================================================
# Resources (3)
# ============================================================================

EXAMPLE_QUERIES = """
## Example Queries

```sql
-- Inbox: recent conversations
SELECT conv_id, from_addr, from_name, subject, date_utc, flags
FROM messages WHERE folder='INBOX' AND account='siue'
ORDER BY date_utc DESC LIMIT 20

-- Thread history (for context before replying)
SELECT message_id, from_addr, from_name, subject, date_utc, body_text
FROM messages WHERE conv_id = 'abc123def456'
ORDER BY date_utc ASC

-- Unread counts by folder
SELECT folder, COUNT(*) as unread
FROM messages WHERE flags NOT LIKE '%"seen"%'
GROUP BY folder

-- Full-text search
SELECT m.message_id, m.from_addr, m.subject, m.date_utc
FROM messages_fts f
JOIN messages m ON m.rowid = f.rowid
WHERE messages_fts MATCH 'quarterly report'
ORDER BY m.date_utc DESC LIMIT 20

-- Priority senders (combine with clerk://config priorities)
SELECT message_id, from_addr, subject, date_utc
FROM messages
WHERE from_addr LIKE '%@siue.edu%' AND flags NOT LIKE '%"seen"%'
ORDER BY date_utc DESC

-- Attachments for a message
SELECT attachments_json FROM messages WHERE message_id = '<msg-id>'

-- Pending drafts
SELECT * FROM drafts ORDER BY updated_at DESC

-- Send audit log
SELECT * FROM send_log ORDER BY timestamp DESC LIMIT 10
```
"""


@mcp.resource("clerk://schema")
def resource_schema() -> str:
    """Database schema and example queries for clerk_sql."""
    return SCHEMA + "\n" + EXAMPLE_QUERIES


@mcp.resource("clerk://config")
def resource_config() -> str:
    """Clerk configuration: accounts, priorities, settings (sensitive fields redacted)."""
    config = get_config()
    data: dict[str, Any] = {
        "default_account": config.default_account,
        "accounts": {},
        "priorities": {
            "senders": config.priorities.senders,
            "topics": config.priorities.topics,
        },
        "cache": {
            "window_days": config.cache.window_days,
            "inbox_freshness_min": config.cache.inbox_freshness_min,
        },
    }
    for name, acct in config.accounts.items():
        data["accounts"][name] = {
            "protocol": acct.protocol,
            "from": acct.from_.address,
        }
    return json.dumps(data, indent=2)


@mcp.resource("clerk://folders")
def resource_folders() -> str:
    """Available email folders per account."""
    api = get_api()
    config = get_config()
    result: dict[str, list[str]] = {}
    for name in config.accounts:
        try:
            folders = api.list_folders(account=name)
            result[name] = [f.name for f in folders]
        except Exception as e:
            result[name] = [f"Error: {e}"]
    return json.dumps(result, indent=2)


# ============================================================================
# Server Entry Point
# ============================================================================


def run_server() -> None:
    """Run the MCP server."""
    mcp.run()
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mcp_redesign.py -v`
Expected: PASS (at least 12 tests)

Then run the full suite to check for regressions:

Run: `pytest --tb=short`
Expected: Some tests in `test_mcp_server.py` and `test_mcp_mutations.py` may fail because functions were removed. That's expected — we'll handle cleanup in Task 7.

**Step 5: Commit**

```bash
git add src/clerk/mcp_server.py tests/test_mcp_redesign.py
git commit -m "feat(mcp): rewrite server — 8 tools + 3 resources

BREAKING: Removes clerk_inbox, clerk_show, clerk_search, clerk_search_sql,
clerk_archive, clerk_mark_read, clerk_mark_unread, clerk_attachments,
clerk_drafts, clerk_delete_draft tools.

Replaces with: clerk_sql (universal read), clerk_sync (incremental),
clerk_reply (auto-populated headers), clerk_draft, clerk_send,
clerk_move, clerk_flag (consolidated), clerk_status.

Resources: clerk://schema, clerk://config, clerk://folders"
```

---

### Task 5: Remove skill module and CLI group

**Files:**
- Delete: `src/clerk/skill.py`
- Modify: `src/clerk/cli.py:1149-1229` (remove skill_app typer group)
- Delete: `tests/test_skill.py`

**Step 1: Remove the skill CLI group from `cli.py`**

In `src/clerk/cli.py`, find the section starting with `# Skill Management Commands` (around line 1149) and delete everything from there through the end of `skill_status` function (around line 1229). That means removing:
- The comment `# Skill Management Commands`
- `skill_app = typer.Typer(...)`
- `app.add_typer(skill_app, name="skill")`
- `skill_install` function
- `skill_uninstall` function
- `skill_status` function

**Step 2: Delete skill module and tests**

```bash
rm src/clerk/skill.py tests/test_skill.py
```

**Step 3: Run tests to verify nothing else breaks**

Run: `pytest --tb=short -q`
Expected: All remaining tests pass. The only failures should be in old MCP test files (handled in Task 7).

**Step 4: Commit**

```bash
git add -u src/clerk/skill.py src/clerk/cli.py tests/test_skill.py
git commit -m "chore: remove skill module and CLI group

The MCP server with clerk://schema, clerk://config, and clerk://folders
resources replaces the Claude Code skill entirely."
```

---

### Task 6: Update config.yaml with priorities

**Files:**
- Modify: `~/.config/clerk/config.yaml`

**Step 1: Add priorities section to config**

Add the following to the end of `~/.config/clerk/config.yaml` (before any trailing whitespace):

```yaml
priorities:
  senders:
    - "hfujino@siue.edu"
    - "@siue.edu"
  topics:
    - "IDOT"
    - "scanner"
    - "VPN"
    - "VDMS"
```

**Step 2: Verify config loads correctly**

Run: `clerk status`
Expected: Should print status without errors (verifies the config parses correctly with the new priorities section).

**Step 3: Commit**

This is a local config file — no commit needed.

---

### Task 7: Clean up old tests and verify full suite

**Files:**
- Delete: `tests/test_mcp_server.py`
- Delete: `tests/test_mcp_mutations.py`
- Keep: `tests/test_mcp_sql.py` (the `TestExecuteReadonlySql` class tests `Cache.execute_readonly_sql` directly — still valid. The `TestClerkSqlTool` class may need updating since the tool signature didn't change.)
- Keep: `tests/test_mcp_redesign.py`

**Step 1: Check which old MCP tests still pass**

Run: `pytest tests/test_mcp_server.py tests/test_mcp_mutations.py tests/test_mcp_sql.py -v 2>&1 | grep -E "PASSED|FAILED"`

For any tests in `test_mcp_sql.py` that reference removed functions, update them. The `TestExecuteReadonlySql` class should be untouched. The `TestClerkSqlTool` class tests `clerk_sql` which still exists with the same signature, so it should pass.

**Step 2: Delete old test files**

```bash
rm tests/test_mcp_server.py tests/test_mcp_mutations.py
```

**Step 3: Run full test suite**

Run: `pytest --tb=short`
Expected: ALL tests pass. If any fail, fix them.

**Step 4: Run type checking and linting**

Run: `mypy src && ruff check src tests`
Expected: Clean

**Step 5: Commit**

```bash
git add -u tests/
git commit -m "chore: clean up old MCP test files, verify full suite passes"
```

---

### Task 8: Verify MCP server works end-to-end

**Step 1: Start the MCP server and test manually**

Run: `clerk mcp-server` in one terminal.

In another terminal (or via the MCP test client), verify:
1. `clerk_sync` with the demo account (requires Docker mail server) or the siue account
2. `clerk_sql` with a basic query
3. `clerk_status` returns account info

If Docker mail server is available:
```bash
docker-compose -f docker-compose.test.yml up -d
```

**Step 2: Verify tool count**

The MCP server should advertise exactly 8 tools when a client connects:
- `clerk_sql`
- `clerk_sync`
- `clerk_reply`
- `clerk_draft`
- `clerk_send`
- `clerk_move`
- `clerk_flag`
- `clerk_status`

And 3 resources:
- `clerk://schema`
- `clerk://config`
- `clerk://folders`

**Step 3: Test with real account**

If siue account is authenticated:
```bash
# In a Python shell or MCP client:
clerk_sync(account="siue")
clerk_sql(query="SELECT from_addr, subject, date_utc FROM messages WHERE account='siue' ORDER BY date_utc DESC LIMIT 5")
```

**Step 4: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: address end-to-end test findings"
```
