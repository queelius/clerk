# Faithful Mirror: Schema v2 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-key the message cache on the server-truthful identity `(account, folder, uid)` with INTEGER bitmask flags, a canonical `thread_subject`, body-preserving upserts, and a clean v1-to-v2 migration that keeps the audit log and drafts while rebuilding the derivable cache.

**Architecture:** This is the foundation plan of the "Faithful Mirror" round (see `docs/superpowers/specs/2026-06-04-faithful-mirror-trust-correctness-design.md`). It changes the cache schema and the model/parse plumbing that feeds it, but does NOT yet change sync behavior (eager bodies), mutation routing (UID-keyed), send-safety, or read-time signals. Those are follow-on plans (see Roadmap at the end). After this plan, the existing INBOX header-only sync still works, now writing UID-keyed rows with bitmask flags into a versioned, migration-safe schema.

**Tech Stack:** Python 3.11+, SQLite (FTS5, `PRAGMA user_version`, WAL), Pydantic v2, pytest.

---

## File Structure

- `src/clerk/models.py` (modify): add `Message.uid`; add `flags_to_bitmask` / `bitmask_to_flags` helpers next to `MessageFlag`.
- `src/clerk/threading.py` (modify): expose a public `normalize_subject`.
- `src/clerk/imap_client.py` (modify): set `uid` on the parsed `Message` in `_parse_message`.
- `src/clerk/cache.py` (modify): v2 `SCHEMA`, WAL/busy_timeout in `_connect`, `SCHEMA_VERSION`, migration, `store_message`/`_row_to_message`, bitmask `update_flags`, bitmask unread predicate + `thread_subject` in listings.
- `src/clerk/mcp_server.py` (modify): update `EXAMPLE_QUERIES` (bitmask unread predicate, bm25/snippet examples) so the agent stops writing `flags LIKE` against an INTEGER column.
- Tests: `tests/test_models.py`, `tests/test_threading.py`, `tests/test_imap_m365.py` (or a new `tests/test_parse_uid.py`), `tests/test_cache.py`, new `tests/test_migration.py`, `tests/test_mcp_sql.py`.

---

### Task 1: Flag bitmask helpers and `Message.uid`

**Files:**
- Modify: `src/clerk/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
from clerk.models import (
    MessageFlag,
    flags_to_bitmask,
    bitmask_to_flags,
)


def test_flags_to_bitmask_known_values():
    assert flags_to_bitmask([]) == 0
    assert flags_to_bitmask([MessageFlag.SEEN]) == 1
    assert flags_to_bitmask([MessageFlag.ANSWERED]) == 2
    assert flags_to_bitmask([MessageFlag.FLAGGED]) == 4
    assert flags_to_bitmask([MessageFlag.DELETED]) == 8
    assert flags_to_bitmask([MessageFlag.DRAFT]) == 16
    assert flags_to_bitmask([MessageFlag.SEEN, MessageFlag.FLAGGED]) == 5


def test_bitmask_roundtrip():
    flags = [MessageFlag.SEEN, MessageFlag.ANSWERED, MessageFlag.DELETED]
    assert set(bitmask_to_flags(flags_to_bitmask(flags))) == set(flags)


def test_bitmask_to_flags_zero_is_empty():
    assert bitmask_to_flags(0) == []


def test_message_carries_uid():
    from datetime import datetime
    from clerk.models import Message, Address

    msg = Message(
        message_id="<a@b>",
        conv_id="abc123",
        **{"from": Address(addr="x@y.com")},
        date=datetime(2026, 1, 1),
        uid=42,
    )
    assert msg.uid == 42
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -k "bitmask or uid" -v`
Expected: FAIL with `ImportError: cannot import name 'flags_to_bitmask'` (and `uid` unknown).

- [ ] **Step 3: Implement the helpers and field**

In `src/clerk/models.py`, change the imports line at the top from:

```python
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field
```

to:

```python
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field
```

Immediately after the `MessageFlag` class definition (after its last member `DRAFT = "draft"`), add:

```python
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
```

In the `Message` model, add the `uid` field directly below the `account` field (after line `account: str = Field(default="", description="Account name if multi-account")`):

```python
    uid: int | None = Field(
        default=None, description="Server IMAP UID within (account, folder)"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -k "bitmask or uid" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/models.py tests/test_models.py
git commit -m "feat(models): add flag bitmask helpers and Message.uid"
```

---

### Task 2: Public `normalize_subject` in threading

**Files:**
- Modify: `src/clerk/threading.py`
- Test: `tests/test_threading.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_threading.py`:

```python
from clerk.threading import normalize_subject


def test_normalize_subject_strips_prefixes():
    assert normalize_subject("Re: Budget") == "Budget"
    assert normalize_subject("RE: re: Fwd: Budget") == "Budget"
    assert normalize_subject("Fw: Hello") == "Hello"
    assert normalize_subject("Plain subject") == "Plain subject"
    assert normalize_subject("   spaced   ") == "spaced"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_threading.py -k normalize_subject -v`
Expected: FAIL with `ImportError: cannot import name 'normalize_subject'`.

- [ ] **Step 3: Implement the public wrapper**

In `src/clerk/threading.py`, the private `_normalize_subject` already exists. Add a public alias just above it (before `def _normalize_subject`):

```python
def normalize_subject(subject: str) -> str:
    """Public: normalize a subject by removing Re:/Fwd: prefixes.

    Used by the cache to persist a canonical per-thread subject so listings
    do not show an arbitrary Re:-prefixed variant.
    """
    return _normalize_subject(subject)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_threading.py -k normalize_subject -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clerk/threading.py tests/test_threading.py
git commit -m "feat(threading): expose public normalize_subject"
```

---

### Task 3: Populate `uid` when parsing IMAP messages

**Files:**
- Modify: `src/clerk/imap_client.py:457-476` (the `Message(...)` return in `_parse_message`)
- Test: `tests/test_parse_uid.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_parse_uid.py`:

```python
"""The parsed Message must carry the server UID it was fetched under."""
from datetime import datetime

from clerk.config import AccountConfig, ImapConfig, SmtpConfig
from clerk.imap_client import ImapClient


def _client() -> ImapClient:
    cfg = AccountConfig(
        protocol="imap",
        imap=ImapConfig(host="h", username="u"),
        smtp=SmtpConfig(host="h", username="u"),
        **{"from": {"address": "u@example.com"}},
    )
    return ImapClient("acct", cfg)


class _Env:
    # Minimal stand-in for an imapclient ENVELOPE object.
    def __init__(self):
        self.date = datetime(2026, 1, 2)
        self.subject = b"Hello"
        self.from_ = None
        self.message_id = b"<m1@example.com>"


def test_parse_message_sets_uid():
    client = _client()
    data = {
        b"ENVELOPE": _Env(),
        b"FLAGS": (b"\\Seen",),
        b"BODY[HEADER]": b"To: a@b.com\r\nSubject: Hello\r\n\r\n",
    }
    msg = client._parse_message(
        uid=777, data=data, folder="INBOX", has_body=False, fetch_time=datetime(2026, 1, 2)
    )
    assert msg is not None
    assert msg.uid == 777
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_parse_uid.py -v`
Expected: FAIL with `assert None == 777` (uid not set; defaults to None).

- [ ] **Step 3: Implement**

In `src/clerk/imap_client.py`, in `_parse_message`, the `return Message(` call (currently starting at line 457) sets `message_id=message_id,` then `conv_id=conv_id,`. Add `uid=uid,` as the first keyword argument so the return becomes:

```python
        return Message(
            uid=uid,
            message_id=message_id,
            conv_id=conv_id,
            folder=folder,
            account=self.account_name,
            **{"from": from_addr},
            to=to_addrs,
            cc=cc_addrs,
            reply_to=reply_to_addrs,
            date=date,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
            flags=flags,
            in_reply_to=in_reply_to,
            references=references,
            headers_fetched_at=fetch_time,
            body_fetched_at=fetch_time if has_body else None,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_parse_uid.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clerk/imap_client.py tests/test_parse_uid.py
git commit -m "feat(imap): carry server UID onto parsed Message"
```

---

### Task 4: Cache schema v2 + WAL + UID/bitmask store/read roundtrip

This is the core task. It rewrites the `SCHEMA`, adds WAL/busy_timeout and `SCHEMA_VERSION`, and rewrites `store_message` and `_row_to_message` to the v2 shape with body-preserving upsert.

**Files:**
- Modify: `src/clerk/cache.py` (SCHEMA block lines 27-139; `_connect` 157-166; `store_message` 195-229; `_row_to_message` 168-193; `update_body` keeps message_id key)
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cache.py` (these use the `tmp_path` pattern already used in the file):

```python
from datetime import UTC, datetime

from clerk.cache import Cache
from clerk.models import Address, Message, MessageFlag


def _msg(uid, message_id, *, body_text=None, body_html=None, subject="Re: Budget",
         flags=None, conv_id="aa11bb22cc33", references=None, in_reply_to=None):
    return Message(
        uid=uid,
        message_id=message_id,
        conv_id=conv_id,
        account="acct",
        folder="INBOX",
        **{"from": Address(addr="boss@work.com", name="Boss")},
        date=datetime(2026, 1, 1, tzinfo=UTC),
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        flags=flags or [],
        references=references or [],
        in_reply_to=in_reply_to,
        headers_fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_store_and_get_roundtrip_with_uid_and_flags(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(10, "<m10@x>", body_text="hi", flags=[MessageFlag.SEEN]))
    got = cache.get_message("<m10@x>")
    assert got is not None
    assert got.uid == 10
    assert MessageFlag.SEEN in got.flags
    assert got.body_text == "hi"


def test_flags_stored_as_integer_bitmask(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(11, "<m11@x>", flags=[MessageFlag.SEEN, MessageFlag.FLAGGED]))
    with cache._connect() as conn:
        row = conn.execute("SELECT flags FROM messages WHERE uid = 11").fetchone()
    assert row["flags"] == 5  # SEEN(1) | FLAGGED(4)


def test_thread_subject_persisted_normalized(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(12, "<m12@x>", subject="Re: Fwd: Budget"))
    with cache._connect() as conn:
        row = conn.execute("SELECT thread_subject FROM messages WHERE uid = 12").fetchone()
    assert row["thread_subject"] == "Budget"


def test_header_resync_preserves_existing_body(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(13, "<m13@x>", body_text="full body"))
    # Re-store the same (account, folder, uid) with no body (header-only re-sync).
    cache.store_message(_msg(13, "<m13@x>", body_text=None, flags=[MessageFlag.SEEN]))
    got = cache.get_message("<m13@x>")
    assert got.body_text == "full body"          # body preserved
    assert MessageFlag.SEEN in got.flags          # flags updated


def test_wal_mode_enabled(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    with cache._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_fresh_db_has_user_version_2(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    with cache._connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache.py -k "uid or bitmask or thread_subject or resync or wal or user_version" -v`
Expected: FAIL (no `uid` column / `store_message` rejects unknown columns / no `thread_subject`).

- [ ] **Step 3: Rewrite the SCHEMA**

In `src/clerk/cache.py`, add the version constant just below the imports / `_CONV_ID_PREFIX_RE` line:

```python
SCHEMA_VERSION = 2
```

Replace the `messages` table definition and its indexes (lines 28-61, from `-- Core message storage` through the `idx_messages_account` index) with:

```sql
-- Core message storage. Identity is the server-truthful (account, folder, uid).
CREATE TABLE IF NOT EXISTS messages (
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    uid INTEGER NOT NULL,

    message_id TEXT,
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
```

In the same `SCHEMA` string, add a `status` column to the `send_log` table definition. Change the `send_log` block (lines 129-138) so the column list ends with:

```sql
    subject TEXT NOT NULL,
    message_id TEXT,
    status TEXT NOT NULL DEFAULT 'sent'
);
```

(The FTS5 virtual table and the three triggers stay exactly as they are: they reference `message_id, subject, body_text, from_name, from_addr`, all still present.)

- [ ] **Step 4: Add WAL/busy_timeout and version stamping to `_connect`/`_ensure_schema`**

Replace `_connect` (lines 157-166) with:

```python
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
```

Replace `_ensure_schema` (lines 151-155) with:

```python
    def _ensure_schema(self) -> None:
        """Create schema if absent; migrate a legacy (v1) cache to v2."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION and self._is_legacy_v1(conn):
                self._migrate_v1_to_v2(conn)
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
```

(The `_is_legacy_v1` and `_migrate_v1_to_v2` methods are added in Task 5. To keep this task runnable on a fresh DB, add temporary stubs now and flesh them out in Task 5. Add these two methods to the `Cache` class:)

```python
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
        """Filled in by Task 5."""
        raise NotImplementedError
```

- [ ] **Step 5: Rewrite `store_message` and `_row_to_message`**

At the top of `cache.py`, extend the model imports (lines 13-21) to include the bitmask helpers, and add the threading helpers import below the config import:

```python
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
```

Replace `store_message` (lines 195-229) with:

```python
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
                    body_text=COALESCE(excluded.body_text, messages.body_text),
                    body_html=COALESCE(excluded.body_html, messages.body_html),
                    body_skipped=excluded.body_skipped,
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
                    0,
                    flags_to_bitmask(msg.flags),
                    json.dumps([a.model_dump() for a in msg.attachments]),
                    msg.in_reply_to,
                    json.dumps(msg.references),
                    (msg.headers_fetched_at or datetime.now(UTC)).isoformat(),
                    msg.body_fetched_at.isoformat() if msg.body_fetched_at else None,
                ),
            )
```

Replace `_row_to_message` (lines 168-193) with:

```python
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
```

- [ ] **Step 6: Run the new tests, then the whole cache suite, and fix fixtures**

Run: `pytest tests/test_cache.py -k "uid or bitmask or thread_subject or resync or wal or user_version" -v`
Expected: PASS (7 tests).

Then run the full file: `pytest tests/test_cache.py -v`
Existing tests that build a `Message` (or use a `sample_message` fixture) will fail because they omit `uid` or expect the old JSON flags column. For each failure, add `uid=<n>` to the `Message(...)` construction (any positive int, unique per message in a test), and update any assertion that read the raw `flags` column as JSON to expect the integer bitmask. Re-run until the whole file is green.

- [ ] **Step 7: Commit**

```bash
git add src/clerk/cache.py tests/test_cache.py
git commit -m "feat(cache): schema v2 with UID identity, bitmask flags, body-preserving upsert, WAL"
```

---

### Task 5: v1-to-v2 migration (preserve drafts + send_log, rebuild messages)

**Files:**
- Modify: `src/clerk/cache.py` (replace the `_migrate_v1_to_v2` stub from Task 4)
- Test: `tests/test_migration.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_migration.py`:

```python
"""A pre-v2 cache.db must migrate cleanly: drafts and send_log survive,
messages/FTS are rebuilt in the v2 shape, user_version becomes 2."""
import sqlite3

from clerk.cache import Cache

# Minimal legacy (v1) schema: messages keyed on message_id, flags as TEXT,
# no uid column; send_log without a status column; a drafts row to preserve.
_V1 = """
CREATE TABLE messages (
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
CREATE TABLE drafts (
    draft_id TEXT PRIMARY KEY, account TEXT NOT NULL, to_json TEXT NOT NULL,
    cc_json TEXT DEFAULT '[]', bcc_json TEXT DEFAULT '[]', subject TEXT NOT NULL,
    body_text TEXT NOT NULL, body_html TEXT, reply_to_conv_id TEXT, in_reply_to TEXT,
    references_json TEXT DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sync_state (
    account TEXT NOT NULL, folder TEXT NOT NULL, last_uid INTEGER DEFAULT 0,
    last_sync_utc TEXT NOT NULL, PRIMARY KEY (account, folder)
);
CREATE TABLE send_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, account TEXT NOT NULL,
    to_json TEXT NOT NULL, cc_json TEXT DEFAULT '[]', bcc_json TEXT DEFAULT '[]',
    subject TEXT NOT NULL, message_id TEXT
);
"""


def _make_v1(path):
    conn = sqlite3.connect(path)
    conn.executescript(_V1)
    conn.execute(
        "INSERT INTO drafts (draft_id, account, to_json, subject, body_text, created_at, updated_at) "
        "VALUES ('d1','acct','[]','Hi','body','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO send_log (timestamp, account, to_json, subject, message_id) "
        "VALUES ('2026-01-01T00:00:00+00:00','acct','[]','Sent','<s1@x>')"
    )
    conn.commit()
    conn.close()


def test_migration_preserves_drafts_and_send_log_and_rebuilds_messages(tmp_path):
    db = tmp_path / "cache.db"
    _make_v1(db)

    cache = Cache(db_path=db)  # opening triggers migration

    with cache._connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        # messages rebuilt in v2 shape
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        assert "uid" in cols
        # non-derivable data preserved
        assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM send_log").fetchone()[0] == 1
        # send_log gained the status column, defaulting historical rows to 'sent'
        sl_cols = {r[1] for r in conn.execute("PRAGMA table_info(send_log)")}
        assert "status" in sl_cols
        assert conn.execute("SELECT status FROM send_log").fetchone()[0] == "sent"


def test_second_open_is_idempotent(tmp_path):
    db = tmp_path / "cache.db"
    _make_v1(db)
    Cache(db_path=db)
    cache = Cache(db_path=db)  # second open: already v2
    with cache._connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_migration.py -v`
Expected: FAIL with `NotImplementedError` (the Task 4 stub).

- [ ] **Step 3: Implement the migration**

In `src/clerk/cache.py`, replace the `_migrate_v1_to_v2` stub with:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_migration.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/cache.py tests/test_migration.py
git commit -m "feat(cache): v1-to-v2 migration preserving drafts and audit log"
```

---

### Task 6: Bitmask unread predicate + canonical `thread_subject` in listings

**Files:**
- Modify: `src/clerk/cache.py` (`find_conversations_by_prefix` 262-281; `list_conversations` 382-403; `update_flags` 479-485)
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cache.py` (reuses the `_msg` helper from Task 4):

```python
def test_list_conversations_unread_uses_bitmask(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(20, "<u1@x>", conv_id="ff00ff00ff00", flags=[]))           # unread
    cache.store_message(_msg(21, "<u2@x>", conv_id="ee11ee11ee11",
                             flags=[MessageFlag.SEEN]))                                  # read
    summaries = cache.list_conversations(account="acct", unread_only=True)
    conv_ids = {s.conv_id for s in summaries}
    assert "ff00ff00ff00" in conv_ids
    assert "ee11ee11ee11" not in conv_ids


def test_listing_subject_is_canonical(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(22, "<c1@x>", conv_id="abcabcabcabc", subject="Re: Budget"))
    summaries = cache.list_conversations(account="acct")
    match = [s for s in summaries if s.conv_id == "abcabcabcabc"][0]
    assert match.subject == "Budget"   # canonical, not "Re: Budget"


def test_update_flags_writes_bitmask(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(23, "<f1@x>", flags=[]))
    cache.update_flags("<f1@x>", [MessageFlag.SEEN, MessageFlag.ANSWERED])
    with cache._connect() as conn:
        assert conn.execute("SELECT flags FROM messages WHERE uid = 23").fetchone()["flags"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache.py -k "unread_uses_bitmask or canonical or update_flags_writes" -v`
Expected: FAIL (unread predicate still matches the JSON text form; subject is `MIN(subject)`; `update_flags` writes JSON).

- [ ] **Step 3: Implement**

In `find_conversations_by_prefix`, in the SELECT (lines ~266-269) change:

```sql
                    MIN(subject) as subject,
                    COUNT(*) as message_count,
                    SUM(CASE WHEN flags NOT LIKE '%"seen"%' THEN 1 ELSE 0 END) as unread_count,
```

to:

```sql
                    MIN(thread_subject) as subject,
                    COUNT(*) as message_count,
                    SUM(CASE WHEN flags & 1 = 0 THEN 1 ELSE 0 END) as unread_count,
```

In `list_conversations`, in the SELECT (lines ~386-388) make the identical change:

```sql
                    MIN(thread_subject) as subject,
                    COUNT(*) as message_count,
                    SUM(CASE WHEN flags & 1 = 0 THEN 1 ELSE 0 END) as unread_count,
```

Replace `update_flags` (lines 479-485) with the bitmask form (and import is already added in Task 4):

```python
    def update_flags(self, message_id: str, flags: Sequence[MessageFlag]) -> None:
        """Update message flags (stored as an INTEGER bitmask)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE messages SET flags = ? WHERE message_id = ?",
                (flags_to_bitmask(flags), message_id),
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cache.py -k "unread_uses_bitmask or canonical or update_flags_writes" -v`
Expected: PASS.

Then run the full file to confirm no regressions: `pytest tests/test_cache.py -v` (PASS).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/cache.py tests/test_cache.py
git commit -m "feat(cache): bitmask unread predicate and canonical thread_subject in listings"
```

---

### Task 7: Update agent-facing example queries for the bitmask schema

The `clerk://schema` resource returns the live `SCHEMA` (auto-updated) plus `EXAMPLE_QUERIES`. The example queries still teach the agent the obsolete `flags NOT LIKE '%"seen"%'` form, which now returns wrong results against an INTEGER column. Fix them and add relevance-ranked search examples.

**Files:**
- Modify: `src/clerk/mcp_server.py:607-648` (`EXAMPLE_QUERIES`)
- Test: `tests/test_mcp_sql.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mcp_sql.py`:

```python
from clerk.mcp_server import EXAMPLE_QUERIES


def test_example_queries_use_bitmask_not_like():
    assert 'flags NOT LIKE' not in EXAMPLE_QUERIES
    assert 'flags & 1 = 0' in EXAMPLE_QUERIES


def test_example_queries_show_relevance_search():
    assert 'bm25(' in EXAMPLE_QUERIES
    assert 'snippet(' in EXAMPLE_QUERIES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_sql.py -k example_queries -v`
Expected: FAIL (`flags NOT LIKE` still present; no `bm25(`/`snippet(`).

- [ ] **Step 3: Implement**

In `src/clerk/mcp_server.py`, replace the `EXAMPLE_QUERIES` string (lines 607-648) with:

```python
EXAMPLE_QUERIES = """
## Example Queries

Flags are an INTEGER bitmask: SEEN=1, ANSWERED=2, FLAGGED=4, DELETED=8, DRAFT=16.
Unread means the SEEN bit is clear: `flags & 1 = 0`.

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
FROM messages WHERE flags & 1 = 0
GROUP BY folder

-- Full-text search, ranked by relevance, with a highlighted snippet
SELECT m.message_id, m.from_addr, m.subject, m.date_utc,
       snippet(messages_fts, 2, '[', ']', ' ... ', 10) AS preview
FROM messages_fts f
JOIN messages m ON m.rowid = f.rowid
WHERE messages_fts MATCH 'quarterly report'
ORDER BY bm25(messages_fts) LIMIT 20

-- Priority senders (advisory: clerk does not act on priorities; you apply them)
SELECT message_id, from_addr, subject, date_utc
FROM messages
WHERE from_addr LIKE '%@siue.edu%' AND flags & 1 = 0
ORDER BY date_utc DESC

-- Attachments for a message
SELECT attachments_json FROM messages WHERE message_id = '<msg-id>'

-- Pending drafts
SELECT * FROM drafts ORDER BY updated_at DESC

-- Send audit log
SELECT * FROM send_log ORDER BY timestamp DESC LIMIT 10
```
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mcp_sql.py -k example_queries -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite + lint + types**

Run: `pytest -q`
Expected: PASS (all green).

Run: `ruff check src tests` then `mypy src`
Expected: clean (fix any import-ordering or type nits ruff/mypy report).

- [ ] **Step 6: Commit**

```bash
git add src/clerk/mcp_server.py tests/test_mcp_sql.py
git commit -m "docs(mcp): teach bitmask unread predicate and bm25/snippet search"
```

---

## Self-Review

- **Spec coverage (Workstream 1):** schema versioning (Task 4/5, `PRAGMA user_version`), preserve-non-derivable/rebuild-derivable migration (Task 5), `messages` PK `(account, folder, uid)` (Task 4), `message_id` demoted to indexed attribute (Task 4), `thread_subject`/`root_message_id` (Task 4, used in Task 6), flags bitmask (Tasks 1, 4, 6), FTS recreation + bm25/snippet exposure (Task 4 schema, Task 7 examples), body-clobber fix via `ON CONFLICT ... COALESCE` (Task 4), WAL + busy_timeout (Task 4), `send_log.status` column for the later send-safety plan (Task 4 schema + Task 5 migration). Canonical-subject nit fixed (Task 6).
- **Deferred by design (other plans):** eager bodies + paging + reconciliation + dead-letter (Workstream 2), UID-keyed mutations + atomic move (Workstream 3), send reservation logic (Workstream 4), staleness signal + BeautifulSoup + prune wiring + DraftManager-via-Cache (Workstream 5), README/CLAUDE.md + priorities-advisory in `clerk://config` (Workstream 6), `_parse_message`/send-layer/integration/OAuth tests + CI floor (Workstream 7).
- **Placeholder scan:** Task 4 deliberately lands a temporary `_migrate_v1_to_v2` stub that raises, then Task 5 replaces it; this is sequenced, not a placeholder. Task 4 Step 6 asks the engineer to update existing fixtures by reading the failing tests; the change required (add `uid=`, expect integer flags) is stated explicitly.
- **Type consistency:** `flags_to_bitmask`/`bitmask_to_flags` (Task 1) are used in `cache.py` (Tasks 4, 6); `normalize_subject` + `compute_root_id` (Task 2 + existing) used in Task 4; `Message.uid` (Task 1) populated in Task 3, stored in Task 4, read in Task 4. `send_log.status` introduced in the schema (Task 4) and migration (Task 5), consumed later by Workstream 4. Names match across tasks.

---

## Roadmap: follow-on plans (authored just-in-time after this lands)

Each is a separate plan file under `docs/superpowers/plans/`; each produces working, tested software on top of schema v2.

1. **Sync engine v2** (Workstream 2): eager bodies (with size cap + `body_skipped`), paged first/full sync with per-chunk watermark (removes the silent 200 cap), two-phase incremental sync (append + CONDSTORE/QRESYNC-or-fallback reconcile of flags/expunges for cached UIDs), parse-failure dead-letter. Touches `imap_client.py`, `api.py`, `cache.py`.
2. **UID-keyed mutations** (Workstream 3): flag/move/body-fetch by stored `(folder, uid)`; `UID MOVE` or `COPY`+`UID STORE`+`UID EXPUNGE`. Touches `imap_client.py`, `api.py`, `cache.py`.
3. **Send-safety reservation** (Workstream 4): `log_send` becomes a pre-send `status='pending'` reservation flipped to `sent`/`failed`; rate limiter counts `pending`+`sent`. Touches `cache.py`, `api.py`.
4. **Read honesty + small fixes** (Workstream 5): per-folder `last_sync_at` staleness signal in `clerk_status`; BeautifulSoup `html_to_text`; `DraftManager` through `Cache`; wire `prune_old_messages` (off by default); cache size/oldest in status. Touches `api.py`, `drafts.py`, `cache.py`, `mcp_server.py`, `pyproject.toml`.
5. **Doc honesty** (Workstream 6): README tool inventory + remove fictional attachment command; CLAUDE.md remove `search.py`/operators; `clerk://config` marks `priorities` advisory.
6. **Test hardening** (Workstream 7): `_parse_message` fixtures; blocked-recipients / FROM-mismatch / audit-log-failure send-safety tests; rewrite the dead integration suite against the current API; OAuth refresh; `--cov-fail-under` floor.
