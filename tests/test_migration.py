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
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        assert "uid" in cols
        assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM send_log").fetchone()[0] == 1
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
