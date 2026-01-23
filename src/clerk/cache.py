"""SQLite cache with FTS5 for message storage and search."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import get_config, get_data_dir
from .search import SearchQuery, build_fts_query, build_where_clauses, parse_search_query
from .models import (
    Address,
    Attachment,
    CacheStats,
    Conversation,
    ConversationSummary,
    Message,
    MessageFlag,
)


SCHEMA = """
-- Core message storage
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    conv_id TEXT NOT NULL,
    account TEXT NOT NULL,
    folder TEXT NOT NULL,

    from_addr TEXT NOT NULL,
    from_name TEXT DEFAULT '',
    to_json TEXT DEFAULT '[]',
    cc_json TEXT DEFAULT '[]',
    reply_to_json TEXT DEFAULT '[]',

    subject TEXT DEFAULT '',
    date_utc TEXT NOT NULL,

    body_text TEXT,
    body_html TEXT,

    flags TEXT DEFAULT '[]',
    attachments_json TEXT DEFAULT '[]',

    in_reply_to TEXT,
    references_json TEXT DEFAULT '[]',

    headers_fetched_at TEXT NOT NULL,
    body_fetched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id);
CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(date_utc DESC);
CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_addr);
CREATE INDEX IF NOT EXISTS idx_messages_folder ON messages(folder);
CREATE INDEX IF NOT EXISTS idx_messages_account ON messages(account);

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

-- Send audit log (append-only, not pruned with cache)
CREATE TABLE IF NOT EXISTS send_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    account TEXT NOT NULL,
    to_json TEXT NOT NULL,
    cc_json TEXT DEFAULT '[]',
    bcc_json TEXT DEFAULT '[]',
    subject TEXT NOT NULL,
    message_id TEXT
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
        """Create database schema if not exists."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _row_to_message(self, row: sqlite3.Row) -> Message:
        """Convert a database row to a Message object."""
        return Message(
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
            flags=[MessageFlag(f) for f in json.loads(row["flags"])],
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
        """Store or update a message in the cache."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO messages (
                    message_id, conv_id, account, folder,
                    from_addr, from_name, to_json, cc_json, reply_to_json,
                    subject, date_utc, body_text, body_html,
                    flags, attachments_json, in_reply_to, references_json,
                    headers_fetched_at, body_fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.message_id,
                    msg.conv_id,
                    msg.account,
                    msg.folder,
                    msg.from_.addr,
                    msg.from_.name,
                    json.dumps([a.model_dump() for a in msg.to]),
                    json.dumps([a.model_dump() for a in msg.cc]),
                    json.dumps([a.model_dump() for a in msg.reply_to]),
                    msg.subject,
                    msg.date.isoformat(),
                    msg.body_text,
                    msg.body_html,
                    json.dumps([f.value for f in msg.flags]),
                    json.dumps([a.model_dump() for a in msg.attachments]),
                    msg.in_reply_to,
                    json.dumps(msg.references),
                    (msg.headers_fetched_at or datetime.now(timezone.utc)).isoformat(),
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
        conversations match. Any prefix length is supported.

        Args:
            prefix: Conversation ID prefix to match

        Returns:
            List of ConversationSummary objects for matching conversations,
            sorted by latest date descending.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    conv_id,
                    MAX(date_utc) as latest_date,
                    MIN(subject) as subject,
                    COUNT(*) as message_count,
                    SUM(CASE WHEN flags NOT LIKE '%"seen"%' THEN 1 ELSE 0 END) as unread_count,
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
            params: list = [folder]

            if account:
                where_clauses.append("account = ?")
                params.append(account)

            # Get distinct conversations ordered by latest message
            query = f"""
                SELECT
                    conv_id,
                    MAX(date_utc) as latest_date,
                    MIN(subject) as subject,
                    COUNT(*) as message_count,
                    SUM(CASE WHEN flags NOT LIKE '%"seen"%' THEN 1 ELSE 0 END) as unread_count,
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

    def search(
        self,
        query: str,
        account: str | None = None,
        limit: int = 20,
    ) -> list[Message]:
        """Full-text search using FTS5."""
        with self._connect() as conn:
            # Quote the query to handle special characters like @
            # FTS5 uses double quotes for phrases
            escaped_query = '"' + query.replace('"', '""') + '"'

            if account:
                sql = """
                    SELECT m.* FROM messages m
                    JOIN messages_fts ON m.rowid = messages_fts.rowid
                    WHERE messages_fts MATCH ? AND m.account = ?
                    ORDER BY rank
                    LIMIT ?
                """
                rows = conn.execute(sql, (escaped_query, account, limit)).fetchall()
            else:
                sql = """
                    SELECT m.* FROM messages m
                    JOIN messages_fts ON m.rowid = messages_fts.rowid
                    WHERE messages_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """
                rows = conn.execute(sql, (escaped_query, limit)).fetchall()

            return [self._row_to_message(row) for row in rows]

    def search_advanced(
        self,
        query: str | SearchQuery,
        account: str | None = None,
        folder: str | None = None,
        limit: int = 20,
    ) -> list[Message]:
        """Advanced search with support for operators.

        Supports operators like:
        - from:alice, to:bob
        - subject:meeting, body:quarterly
        - has:attachment
        - is:unread, is:read, is:flagged
        - after:2025-01-01, before:2025-12-31, date:2025-06-15

        Args:
            query: Search query string or pre-parsed SearchQuery object
            account: Filter by account (optional)
            folder: Filter by folder (optional)
            limit: Maximum results to return

        Returns:
            List of matching messages
        """
        # Parse query if it's a string
        if isinstance(query, str):
            parsed = parse_search_query(query)
        else:
            parsed = query

        with self._connect() as conn:
            # Build FTS query for text-based searches
            fts_query = build_fts_query(parsed)
            use_fts = fts_query != "*"

            # Build WHERE clauses for non-FTS filters
            where_clauses, where_params = build_where_clauses(parsed)

            # Add account filter if specified
            if account:
                where_clauses.append("m.account = ?")
                where_params.append(account)

            # Add folder filter if specified
            if folder:
                where_clauses.append("m.folder = ?")
                where_params.append(folder)

            # Build the SQL query
            if use_fts:
                # Use FTS join
                sql = """
                    SELECT m.* FROM messages m
                    JOIN messages_fts ON m.rowid = messages_fts.rowid
                    WHERE messages_fts MATCH ?
                """
                params: list[Any] = [fts_query]

                if where_clauses:
                    sql += " AND " + " AND ".join(where_clauses)
                    params.extend(where_params)

                sql += " ORDER BY rank LIMIT ?"
                params.append(limit)
            else:
                # No FTS needed, just filter by WHERE clauses
                sql = "SELECT * FROM messages m"

                if where_clauses:
                    sql += " WHERE " + " AND ".join(where_clauses)
                    params = list(where_params)
                else:
                    params = []

                sql += " ORDER BY date_utc DESC LIMIT ?"
                params.append(limit)

            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_message(row) for row in rows]

    def execute_raw_query(
        self,
        sql: str,
        params: tuple | list | None = None,
        limit: int = 100,
    ) -> list[Message]:
        """Execute a raw SQL SELECT query on the messages table.

        This is for power users who need custom queries. Only SELECT
        statements are allowed for safety.

        Args:
            sql: SQL query (must be SELECT)
            params: Query parameters (optional)
            limit: Maximum results (enforced even if not in query)

        Returns:
            List of messages matching the query

        Raises:
            ValueError: If query is not a SELECT statement
        """
        # Safety check: only allow SELECT
        sql_upper = sql.strip().upper()
        if not sql_upper.startswith("SELECT"):
            raise ValueError("Only SELECT queries are allowed")

        # Disallow dangerous keywords
        dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"]
        for keyword in dangerous:
            if keyword in sql_upper:
                raise ValueError(f"Query contains disallowed keyword: {keyword}")

        with self._connect() as conn:
            # Add LIMIT if not present
            if "LIMIT" not in sql_upper:
                sql = f"{sql.rstrip(';')} LIMIT {limit}"

            if params:
                rows = conn.execute(sql, params).fetchall()
            else:
                rows = conn.execute(sql).fetchall()

            return [self._row_to_message(row) for row in rows]

    def update_flags(self, message_id: str, flags: list[MessageFlag]) -> None:
        """Update message flags."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE messages SET flags = ? WHERE message_id = ?",
                (json.dumps([f.value for f in flags]), message_id),
            )

    def update_body(
        self, message_id: str, body_text: str | None, body_html: str | None
    ) -> None:
        """Update message body content."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE messages
                SET body_text = ?, body_html = ?, body_fetched_at = ?
                WHERE message_id = ?
                """,
                (body_text, body_html, datetime.now(timezone.utc).isoformat(), message_id),
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
        """Check if cached data is fresh enough."""
        with self._connect() as conn:
            if check_body:
                row = conn.execute(
                    "SELECT body_fetched_at FROM messages WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
                if not row or not row["body_fetched_at"]:
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

            return datetime.now(timezone.utc) - fetched_at < timedelta(minutes=freshness_minutes)

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
            return datetime.now(timezone.utc) - synced_at < timedelta(minutes=freshness_minutes)

    def mark_inbox_synced(self, account: str) -> None:
        """Mark inbox as synced."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)",
                (f"inbox_sync_{account}", datetime.now(timezone.utc).isoformat()),
            )

    def prune_old_messages(self, window_days: int = 7) -> int:
        """Remove messages older than the cache window. Returns count deleted."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
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

            # Get file size
            cache_size = self.db_path.stat().st_size if self.db_path.exists() else 0

            return CacheStats(
                message_count=msg_count,
                conversation_count=conv_count,
                oldest_message=datetime.fromisoformat(oldest) if oldest else None,
                newest_message=datetime.fromisoformat(newest) if newest else None,
                cache_size_bytes=cache_size,
                last_sync=datetime.fromisoformat(last_sync) if last_sync else None,
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
                    datetime.now(timezone.utc).isoformat(),
                    account,
                    json.dumps([a.model_dump() for a in to]),
                    json.dumps([a.model_dump() for a in cc]),
                    json.dumps([a.model_dump() for a in bcc]),
                    subject,
                    message_id,
                ),
            )


# Global cache instance
_cache: Cache | None = None


def get_cache() -> Cache:
    """Get or create the global cache instance."""
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache
