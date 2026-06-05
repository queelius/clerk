"""SQLite cache with FTS5 for message storage and search."""

import json
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import get_data_dir
from .models import (
    Address,
    Attachment,
    CacheStats,
    Conversation,
    ConversationSummary,
    Message,
    MessageFlag,
    bitmask_to_flags,
    flags_to_bitmask,
)
from .threading import compute_root_id, normalize_subject

# Conversation IDs are 12-hex SHA256 prefixes. Validate caller-supplied
# prefixes so they cannot inject SQL LIKE wildcards (%, _, \).
_CONV_ID_PREFIX_RE = re.compile(r"^[0-9a-f]{1,12}$")

SCHEMA_VERSION = 2

# Parse-failure dead-letter: retry a UID this many times before giving up.
_DEADLETTER_MAX_ATTEMPTS = 3

SCHEMA = """
-- Core message storage. Identity is the server-truthful (account, folder, uid).
CREATE TABLE IF NOT EXISTS messages (
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    uid INTEGER NOT NULL,

    message_id TEXT NOT NULL,
    conv_id TEXT NOT NULL,
    root_message_id TEXT,
    thread_subject TEXT DEFAULT '',

    from_addr TEXT NOT NULL,
    from_name TEXT DEFAULT '',
    to_json TEXT DEFAULT '[]',
    cc_json TEXT DEFAULT '[]',
    reply_to_json TEXT DEFAULT '[]',

    subject TEXT DEFAULT '',
    date_utc TEXT NOT NULL,

    body_text TEXT,
    body_html TEXT,
    body_skipped INTEGER NOT NULL DEFAULT 0,

    flags INTEGER NOT NULL DEFAULT 0,
    attachments_json TEXT DEFAULT '[]',

    in_reply_to TEXT,
    references_json TEXT DEFAULT '[]',

    headers_fetched_at TEXT NOT NULL,
    body_fetched_at TEXT,

    PRIMARY KEY (account, folder, uid)
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id);
CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(date_utc DESC);
CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_addr);
CREATE INDEX IF NOT EXISTS idx_messages_folder ON messages(folder);
CREATE INDEX IF NOT EXISTS idx_messages_account ON messages(account);
CREATE INDEX IF NOT EXISTS idx_messages_message_id ON messages(message_id);
CREATE INDEX IF NOT EXISTS idx_messages_flags ON messages(flags);

-- Full-text search on cached content
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    message_id,
    subject,
    body_text,
    from_name,
    from_addr,
    content=messages,
    content_rowid=rowid
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, message_id, subject, body_text, from_name, from_addr)
    VALUES (new.rowid, new.message_id, new.subject, new.body_text, new.from_name, new.from_addr);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, message_id, subject, body_text, from_name, from_addr)
    VALUES ('delete', old.rowid, old.message_id, old.subject, old.body_text, old.from_name, old.from_addr);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, message_id, subject, body_text, from_name, from_addr)
    VALUES ('delete', old.rowid, old.message_id, old.subject, old.body_text, old.from_name, old.from_addr);
    INSERT INTO messages_fts(rowid, message_id, subject, body_text, from_name, from_addr)
    VALUES (new.rowid, new.message_id, new.subject, new.body_text, new.from_name, new.from_addr);
END;

-- Draft storage (local only)
CREATE TABLE IF NOT EXISTS drafts (
    draft_id TEXT PRIMARY KEY,
    account TEXT NOT NULL,

    to_json TEXT NOT NULL,
    cc_json TEXT DEFAULT '[]',
    bcc_json TEXT DEFAULT '[]',

    subject TEXT NOT NULL,
    body_text TEXT NOT NULL,
    body_html TEXT,

    reply_to_conv_id TEXT,
    in_reply_to TEXT,
    references_json TEXT DEFAULT '[]',

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Cache metadata
CREATE TABLE IF NOT EXISTS cache_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Sync state for incremental IMAP fetching
CREATE TABLE IF NOT EXISTS sync_state (
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    last_uid INTEGER DEFAULT 0,
    last_sync_utc TEXT NOT NULL,
    PRIMARY KEY (account, folder)
);

-- Send audit log (append-only, not pruned with cache)
CREATE TABLE IF NOT EXISTS send_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    account TEXT NOT NULL,
    to_json TEXT NOT NULL,
    cc_json TEXT DEFAULT '[]',
    bcc_json TEXT DEFAULT '[]',
    subject TEXT NOT NULL,
    message_id TEXT,
    status TEXT NOT NULL DEFAULT 'sent'
);
"""


class Cache:
    """SQLite-based message cache with FTS5 support."""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = get_data_dir() / "cache.db"
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create schema if absent; migrate a legacy (v1) cache to v2."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION and self._is_legacy_v1(conn):
                self._migrate_v1_to_v2(conn)
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Context manager for database connections (WAL, 5s busy timeout)."""
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _is_legacy_v1(self, conn: sqlite3.Connection) -> bool:
        """A pre-v2 cache: a messages table exists but lacks the uid column."""
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchone()
        if not row:
            return False
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        return "uid" not in cols

    def _migrate_v1_to_v2(self, conn: sqlite3.Connection) -> None:
        """Rebuild the derivable cache; preserve drafts and send_log.

        messages/messages_fts are reconstructible from the server, so we drop
        and let _ensure_schema recreate them in the v2 shape; sync_state is
        reset so the next sync is a full re-sync. drafts (unsent work) and
        send_log (audit trail) are kept; send_log gains a 'status' column.
        """
        # Drop the derivable tables (dropping messages also drops its triggers).
        conn.execute("DROP TABLE IF EXISTS messages")
        conn.execute("DROP TABLE IF EXISTS messages_fts")
        conn.execute("DELETE FROM sync_state")

        # Preserve send_log; add the status column if missing.
        sl_cols = {r[1] for r in conn.execute("PRAGMA table_info(send_log)")}
        if "status" not in sl_cols:
            conn.execute(
                "ALTER TABLE send_log ADD COLUMN status TEXT NOT NULL DEFAULT 'sent'"
            )

    def _row_to_message(self, row: sqlite3.Row) -> Message:
        """Convert a database row to a Message object."""
        return Message(
            uid=row["uid"],
            message_id=row["message_id"],
            conv_id=row["conv_id"],
            account=row["account"],
            folder=row["folder"],
            **{"from": Address(addr=row["from_addr"], name=row["from_name"] or "")},
            to=[Address(**a) for a in json.loads(row["to_json"])],
            cc=[Address(**a) for a in json.loads(row["cc_json"])],
            reply_to=[Address(**a) for a in json.loads(row["reply_to_json"])],
            subject=row["subject"] or "",
            date=datetime.fromisoformat(row["date_utc"]),
            body_text=row["body_text"],
            body_html=row["body_html"],
            body_skipped=bool(row["body_skipped"]),
            flags=bitmask_to_flags(row["flags"]),
            attachments=[Attachment(**a) for a in json.loads(row["attachments_json"])],
            in_reply_to=row["in_reply_to"],
            references=json.loads(row["references_json"]),
            headers_fetched_at=datetime.fromisoformat(row["headers_fetched_at"]),
            body_fetched_at=(
                datetime.fromisoformat(row["body_fetched_at"])
                if row["body_fetched_at"]
                else None
            ),
        )

    def store_message(self, msg: Message) -> None:
        """Store or update a message, keyed on (account, folder, uid).

        Body-preserving upsert: a header-only re-sync (body_text/html None)
        updates headers/flags/folder but keeps any body already fetched.
        """
        if msg.uid is None:
            raise ValueError("store_message requires msg.uid (the server UID)")

        root_message_id = compute_root_id(
            msg.message_id, msg.references, msg.in_reply_to
        )
        thread_subject = normalize_subject(msg.subject) if msg.subject else ""

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (
                    account, folder, uid,
                    message_id, conv_id, root_message_id, thread_subject,
                    from_addr, from_name, to_json, cc_json, reply_to_json,
                    subject, date_utc, body_text, body_html, body_skipped,
                    flags, attachments_json, in_reply_to, references_json,
                    headers_fetched_at, body_fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account, folder, uid) DO UPDATE SET
                    message_id=excluded.message_id,
                    conv_id=excluded.conv_id,
                    root_message_id=excluded.root_message_id,
                    thread_subject=excluded.thread_subject,
                    from_addr=excluded.from_addr,
                    from_name=excluded.from_name,
                    to_json=excluded.to_json,
                    cc_json=excluded.cc_json,
                    reply_to_json=excluded.reply_to_json,
                    subject=excluded.subject,
                    date_utc=excluded.date_utc,
                    -- body_skipped is set on INSERT and preserved on re-store
                    -- (the sync-engine task owns clearing it when a body is later fetched).
                    body_text=COALESCE(excluded.body_text, messages.body_text),
                    body_html=COALESCE(excluded.body_html, messages.body_html),
                    flags=excluded.flags,
                    attachments_json=excluded.attachments_json,
                    in_reply_to=excluded.in_reply_to,
                    references_json=excluded.references_json,
                    headers_fetched_at=excluded.headers_fetched_at,
                    body_fetched_at=COALESCE(excluded.body_fetched_at, messages.body_fetched_at)
                """,
                (
                    msg.account,
                    msg.folder,
                    msg.uid,
                    msg.message_id,
                    msg.conv_id,
                    root_message_id,
                    thread_subject,
                    msg.from_.addr,
                    msg.from_.name,
                    json.dumps([a.model_dump() for a in msg.to]),
                    json.dumps([a.model_dump() for a in msg.cc]),
                    json.dumps([a.model_dump() for a in msg.reply_to]),
                    msg.subject,
                    msg.date.isoformat(),
                    msg.body_text,
                    msg.body_html,
                    1 if msg.body_skipped else 0,
                    flags_to_bitmask(msg.flags),
                    json.dumps([a.model_dump() for a in msg.attachments]),
                    msg.in_reply_to,
                    json.dumps(msg.references),
                    (msg.headers_fetched_at or datetime.now(UTC)).isoformat(),
                    msg.body_fetched_at.isoformat() if msg.body_fetched_at else None,
                ),
            )

    def get_message(self, message_id: str) -> Message | None:
        """Get a message by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if row:
                return self._row_to_message(row)
        return None

    def find_conversations_by_prefix(self, prefix: str) -> list[ConversationSummary]:
        """Find all conversations matching an ID prefix.

        Returns lightweight summaries for disambiguation when multiple
        conversations match. Any prefix length (1 to 12 hex chars) is
        supported.

        Args:
            prefix: Conversation ID prefix — must be hex digits only.

        Returns:
            List of ConversationSummary objects for matching conversations,
            sorted by latest date descending. Empty list if the prefix is
            malformed (not hex).
        """
        if not _CONV_ID_PREFIX_RE.match(prefix):
            # Not a valid conv_id prefix — return empty rather than pass
            # caller-controlled text into a LIKE pattern.
            return []

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    conv_id,
                    MAX(date_utc) as latest_date,
                    MIN(thread_subject) as subject,
                    COUNT(*) as message_count,
                    SUM(CASE WHEN flags & 1 = 0 THEN 1 ELSE 0 END) as unread_count,
                    GROUP_CONCAT(DISTINCT from_addr) as participants,
                    (SELECT body_text FROM messages m2
                     WHERE m2.conv_id = messages.conv_id
                     ORDER BY date_utc DESC LIMIT 1) as snippet,
                    MIN(account) as account
                FROM messages
                WHERE conv_id LIKE ?
                GROUP BY conv_id
                ORDER BY latest_date DESC
                """,
                (prefix + "%",),
            ).fetchall()

            summaries = []
            for row in rows:
                participants = row["participants"].split(",") if row["participants"] else []
                snippet = (row["snippet"] or "")[:100]

                summaries.append(
                    ConversationSummary(
                        conv_id=row["conv_id"],
                        subject=row["subject"] or "(no subject)",
                        participants=participants,
                        message_count=row["message_count"],
                        unread_count=row["unread_count"],
                        latest_date=datetime.fromisoformat(row["latest_date"]),
                        snippet=snippet,
                        account=row["account"],
                    )
                )

            return summaries

    def get_conversation(self, conv_id: str) -> Conversation | None:
        """Get a conversation by ID or unique prefix.

        Supports partial ID prefix matching with any prefix length.
        Returns None if no match or if multiple conversations match
        (use find_conversations_by_prefix() for disambiguation in that case).

        Args:
            conv_id: Full conversation ID or unique prefix

        Returns:
            Conversation with all messages, or None if not found or ambiguous.
        """
        with self._connect() as conn:
            # Try exact match first
            rows = conn.execute(
                "SELECT * FROM messages WHERE conv_id = ? ORDER BY date_utc ASC",
                (conv_id,),
            ).fetchall()

            if rows:
                return self._build_conversation(rows)

            # Try prefix match - check if unique
            matches = self.find_conversations_by_prefix(conv_id)
            if len(matches) == 1:
                # Unique match - fetch full conversation
                rows = conn.execute(
                    "SELECT * FROM messages WHERE conv_id = ? ORDER BY date_utc ASC",
                    (matches[0].conv_id,),
                ).fetchall()
                return self._build_conversation(rows)

            # No match or ambiguous (multiple matches)
            return None

    def _build_conversation(self, rows: list[sqlite3.Row]) -> Conversation:
        """Build a Conversation object from message rows."""
        messages = [self._row_to_message(row) for row in rows]
        conv_id = messages[0].conv_id
        participants = set()
        unread_count = 0

        for msg in messages:
            participants.add(msg.from_.addr)
            for addr in msg.to + msg.cc:
                participants.add(addr.addr)
            if not msg.is_read:
                unread_count += 1

        return Conversation(
            conv_id=conv_id,
            subject=messages[0].subject,
            participants=sorted(participants),
            message_count=len(messages),
            unread_count=unread_count,
            latest_date=max(m.date for m in messages),
            messages=messages,
            account=messages[0].account,
        )

    def list_conversations(
        self,
        account: str | None = None,
        folder: str = "INBOX",
        unread_only: bool = False,
        limit: int = 20,
    ) -> list[ConversationSummary]:
        """List conversations with summaries."""
        with self._connect() as conn:
            # Build query
            where_clauses = ["folder = ?"]
            params: list[Any] = [folder]

            if account:
                where_clauses.append("account = ?")
                params.append(account)

            # Get distinct conversations ordered by latest message
            query = f"""
                SELECT
                    conv_id,
                    MAX(date_utc) as latest_date,
                    MIN(thread_subject) as subject,
                    COUNT(*) as message_count,
                    SUM(CASE WHEN flags & 1 = 0 THEN 1 ELSE 0 END) as unread_count,
                    GROUP_CONCAT(DISTINCT from_addr) as participants,
                    (SELECT body_text FROM messages m2
                     WHERE m2.conv_id = messages.conv_id
                     ORDER BY date_utc DESC LIMIT 1) as snippet,
                    MIN(account) as account
                FROM messages
                WHERE {' AND '.join(where_clauses)}
                GROUP BY conv_id
                {"HAVING unread_count > 0" if unread_only else ""}
                ORDER BY latest_date DESC
                LIMIT ?
            """
            params.append(limit)

            rows = conn.execute(query, params).fetchall()

            summaries = []
            for row in rows:
                participants = row["participants"].split(",") if row["participants"] else []
                snippet = (row["snippet"] or "")[:100]

                summaries.append(
                    ConversationSummary(
                        conv_id=row["conv_id"],
                        subject=row["subject"] or "(no subject)",
                        participants=participants,
                        message_count=row["message_count"],
                        unread_count=row["unread_count"],
                        latest_date=datetime.fromisoformat(row["latest_date"]),
                        snippet=snippet,
                        account=row["account"],
                    )
                )

            return summaries

    def execute_readonly_sql(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Execute a readonly SQL SELECT and return raw rows as dicts.

        Security model: the SQLite connection is opened with
        ``mode=ro`` + ``nolock=1`` and ``execute`` runs a single statement,
        so write DDL/DML (``INSERT``, ``DROP``, ``ATTACH``, ``PRAGMA`` with
        side effects, etc.) cannot succeed regardless of the text of ``sql``.
        We keep one lightweight shape check (must start with ``SELECT``) to
        give callers clear errors on obvious misuse, and wrap the query so
        the caller's ``limit`` is always enforced even if their SQL already
        has its own ``LIMIT``.

        Args:
            sql: SQL SELECT query.
            params: Query parameters (optional).
            limit: Maximum rows returned. Always enforced.

        Returns:
            List of ``{column_name: value}`` dicts, up to ``limit`` rows.

        Raises:
            ValueError: If the query is not a SELECT.
        """
        stripped = sql.strip().rstrip(";")
        if not stripped:
            raise ValueError("Query is empty")
        # Accept SELECT and WITH (Common Table Expressions). Writes are
        # blocked by the read-only connection regardless of the text, but
        # a clean early error here helps LLM callers recognize misuse.
        first_token = stripped.upper().split(None, 1)[0]
        if first_token not in ("SELECT", "WITH"):
            raise ValueError("Only SELECT / WITH (CTE) queries are allowed")

        # Wrap in an outer SELECT so the limit is always applied, even if
        # the caller already has a LIMIT clause, subqueries, UNION, etc.
        wrapped = f"SELECT * FROM ({stripped}) LIMIT ?"

        db_uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            bind: tuple[Any, ...] = (
                (*tuple(params), limit) if params else (limit,)
            )
            rows = conn.execute(wrapped, bind).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def update_flags(self, message_id: str, flags: Sequence[MessageFlag]) -> None:
        """Update message flags (stored as an INTEGER bitmask)."""
        # Keyed by message_id (unique within INBOX-only scope today); the
        # UID-keyed mutations follow-on plan will re-key this on (account, folder, uid).
        with self._connect() as conn:
            conn.execute(
                "UPDATE messages SET flags = ? WHERE message_id = ?",
                (flags_to_bitmask(flags), message_id),
            )

    def get_recent_uid_flags(
        self, account: str, folder: str, limit: int
    ) -> dict[int, int]:
        """The highest `limit` cached UIDs in a folder, mapped to their flag bitmask.

        Used by reconciliation to compare cached flags against the server cheaply.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT uid, flags FROM messages "
                "WHERE account = ? AND folder = ? ORDER BY uid DESC LIMIT ?",
                (account, folder, limit),
            ).fetchall()
        return {row["uid"]: row["flags"] for row in rows}

    def update_flags_by_uid(
        self, account: str, folder: str, uid: int, flags: Sequence[MessageFlag]
    ) -> None:
        """Update flags for a specific (account, folder, uid)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE messages SET flags = ? "
                "WHERE account = ? AND folder = ? AND uid = ?",
                (flags_to_bitmask(flags), account, folder, uid),
            )

    def delete_by_uid(self, account: str, folder: str, uid: int) -> None:
        """Delete a specific (account, folder, uid) row (expunged server-side)."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM messages WHERE account = ? AND folder = ? AND uid = ?",
                (account, folder, uid),
            )

    def update_body(
        self, message_id: str, body_text: str | None, body_html: str | None
    ) -> None:
        """Update message body content."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE messages
                SET body_text = ?, body_html = ?, body_fetched_at = ?, body_skipped = 0
                WHERE message_id = ?
                """,
                (body_text, body_html, datetime.now(UTC).isoformat(), message_id),
            )

    def move_message(self, message_id: str, folder: str) -> None:
        """Update message folder."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE messages SET folder = ? WHERE message_id = ?",
                (folder, message_id),
            )

    def delete_message(self, message_id: str) -> None:
        """Delete a message from cache."""
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE message_id = ?", (message_id,))

    def is_fresh(
        self, message_id: str, freshness_minutes: int, check_body: bool = False
    ) -> bool:
        """Check if cached data is fresh enough.

        When ``check_body`` is True, returns True only if the body was
        fetched recently AND we actually have body content (``body_text``
        or ``body_html`` non-null). Fixes a bug where a prior fetch that
        stored both bodies as None would appear "fresh" forever and
        prevent any re-fetch.
        """
        with self._connect() as conn:
            if check_body:
                row = conn.execute(
                    "SELECT body_fetched_at, body_text, body_html "
                    "FROM messages WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
                if not row or not row["body_fetched_at"]:
                    return False
                if row["body_text"] is None and row["body_html"] is None:
                    return False
                fetched_at = datetime.fromisoformat(row["body_fetched_at"])
            else:
                row = conn.execute(
                    "SELECT headers_fetched_at FROM messages WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
                if not row:
                    return False
                fetched_at = datetime.fromisoformat(row["headers_fetched_at"])

            return datetime.now(UTC) - fetched_at < timedelta(minutes=freshness_minutes)

    def is_inbox_fresh(self, account: str, freshness_minutes: int = 5) -> bool:
        """Check if inbox listing is fresh enough."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM cache_meta WHERE key = ?",
                (f"inbox_sync_{account}",),
            ).fetchone()
            if not row:
                return False
            synced_at = datetime.fromisoformat(row["value"])
            return datetime.now(UTC) - synced_at < timedelta(minutes=freshness_minutes)

    def mark_inbox_synced(self, account: str) -> None:
        """Mark inbox as synced."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)",
                (f"inbox_sync_{account}", datetime.now(UTC).isoformat()),
            )

    def get_last_sync(self, account: str) -> datetime | None:
        """The last time this account was synced, or None if never."""
        raw = self.get_meta(f"inbox_sync_{account}")
        return datetime.fromisoformat(raw) if raw else None

    def get_meta(self, key: str) -> str | None:
        """Get a value from cache_meta."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM cache_meta WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Set a value in cache_meta."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)",
                (key, value),
            )

    def delete_meta(self, key: str) -> None:
        """Delete a value from cache_meta. No-op if absent."""
        with self._connect() as conn:
            conn.execute("DELETE FROM cache_meta WHERE key = ?", (key,))

    def prune_old_messages(self, window_days: int = 7) -> int:
        """Remove messages older than the cache window. Returns count deleted."""
        cutoff = datetime.now(UTC) - timedelta(days=window_days)
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM messages WHERE date_utc < ?", (cutoff.isoformat(),)
            )
            return cursor.rowcount

    def clear(self) -> None:
        """Clear all cached data (except send log)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM messages")
            conn.execute("DELETE FROM cache_meta")
            conn.execute("DELETE FROM drafts")
            # Rebuild FTS index
            conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")

    def get_stats(self) -> CacheStats:
        """Get cache statistics."""
        with self._connect() as conn:
            msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            conv_count = conn.execute(
                "SELECT COUNT(DISTINCT conv_id) FROM messages"
            ).fetchone()[0]

            oldest = conn.execute(
                "SELECT MIN(date_utc) FROM messages"
            ).fetchone()[0]
            newest = conn.execute(
                "SELECT MAX(date_utc) FROM messages"
            ).fetchone()[0]

            last_sync = conn.execute(
                "SELECT MAX(value) FROM cache_meta WHERE key LIKE 'inbox_sync_%'"
            ).fetchone()[0]

            skipped = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE body_skipped = 1"
            ).fetchone()[0]

            # Get file size
            cache_size = self.db_path.stat().st_size if self.db_path.exists() else 0

            return CacheStats(
                message_count=msg_count,
                conversation_count=conv_count,
                oldest_message=datetime.fromisoformat(oldest) if oldest else None,
                newest_message=datetime.fromisoformat(newest) if newest else None,
                cache_size_bytes=cache_size,
                body_skipped_count=skipped,
                last_sync=datetime.fromisoformat(last_sync) if last_sync else None,
            )

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

    def log_send(
        self,
        account: str,
        to: list[Address],
        cc: list[Address],
        bcc: list[Address],
        subject: str,
        message_id: str | None,
    ) -> None:
        """Log a sent message to the audit log."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO send_log (timestamp, account, to_json, cc_json, bcc_json, subject, message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    account,
                    json.dumps([a.model_dump() for a in to]),
                    json.dumps([a.model_dump() for a in cc]),
                    json.dumps([a.model_dump() for a in bcc]),
                    subject,
                    message_id,
                ),
            )

    def count_sends_since(self, account: str, since: datetime) -> int:
        """Count rows in send_log for an account at or after a timestamp.

        Backs the persistent rate limiter: survives restarts, is atomic with
        respect to concurrent MCP clients. Callers pass a UTC datetime.
        Only 'pending' and 'sent' rows are counted; 'failed' rows are excluded
        so a failed send does not consume quota.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM send_log "
                "WHERE account = ? AND timestamp >= ? AND status IN ('pending', 'sent')",
                (account, since.isoformat()),
            ).fetchone()
            return int(row[0]) if row else 0

    def reserve_send(
        self,
        account: str,
        to: list[Address],
        cc: list[Address],
        bcc: list[Address],
        subject: str,
    ) -> int:
        """Reserve a send_log row (status='pending') before sending; return its id.

        The pending row counts toward the rate limit immediately, so a send is
        counted even if finalize_send never runs (e.g. a disk error after SMTP).
        """
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO send_log
                    (timestamp, account, to_json, cc_json, bcc_json, subject, message_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    datetime.now(UTC).isoformat(),
                    account,
                    json.dumps([a.model_dump() for a in to]),
                    json.dumps([a.model_dump() for a in cc]),
                    json.dumps([a.model_dump() for a in bcc]),
                    subject,
                    None,
                ),
            )
            if cur.lastrowid is None:
                raise RuntimeError("reserve_send: INSERT did not produce a row id")
            return cur.lastrowid

    def finalize_send(
        self, send_id: int, status: str, message_id: str | None
    ) -> None:
        """Mark a reserved send row as 'sent' (with its message id) or 'failed'."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE send_log SET status = ?, message_id = ? WHERE id = ?",
                (status, message_id, send_id),
            )

    def _deadletter_key(self, account: str, folder: str) -> str:
        return f"deadletter:{account}:{folder}"

    def record_deadletter(self, account: str, folder: str, uid: int) -> int:
        """Increment the parse-failure attempt count for a UID; return the count."""
        key = self._deadletter_key(account, folder)
        raw = self.get_meta(key)
        data = json.loads(raw) if raw else {}
        count = int(data.get(str(uid), 0)) + 1
        data[str(uid)] = count
        self.set_meta(key, json.dumps(data))
        return count

    def get_deadletter_uids(
        self, account: str, folder: str, max_attempts: int = _DEADLETTER_MAX_ATTEMPTS
    ) -> list[int]:
        """UIDs still eligible for retry (attempt count below max_attempts), ascending."""
        raw = self.get_meta(self._deadletter_key(account, folder))
        if not raw:
            return []
        data = json.loads(raw)
        return sorted(int(uid) for uid, count in data.items() if int(count) < max_attempts)

    def clear_deadletter(self, account: str, folder: str, uid: int) -> None:
        """Remove a UID from the dead-letter set (it parsed successfully)."""
        key = self._deadletter_key(account, folder)
        raw = self.get_meta(key)
        if not raw:
            return
        data = json.loads(raw)
        if str(uid) in data:
            del data[str(uid)]
            self.set_meta(key, json.dumps(data))


# Global cache instance
_cache: Cache | None = None


def get_cache() -> Cache:
    """Get or create the global cache instance."""
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache
