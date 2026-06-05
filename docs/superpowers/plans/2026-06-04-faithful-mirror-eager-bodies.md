# Faithful Mirror: Eager Bodies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make full-text search honest by fetching message bodies eagerly during sync and converting HTML-only bodies to text at store time, so `messages_fts MATCH` never silently misses a synced message, with a body-size cap to keep the cache bounded.

**Architecture:** Plan 2 of the "Faithful Mirror" round (spec: `docs/superpowers/specs/2026-06-04-faithful-mirror-trust-correctness-design.md`, Workstream 2). It changes the forward sync path only: `api.sync_folder` now requests full bodies, runs each fetched message through a body-preparation step (HTML to text fallback, oversize cap with `body_skipped`), and stores it. It does NOT change the first-sync 200-message cap (paging is Plan 3), parse-failure handling (dead-letter is Plan 3), or multi-device reconciliation (Plan 4). The HTML to text converter is the existing `api.html_to_text` (regex today; Plan 5 swaps it for BeautifulSoup, which this path inherits for free).

**Tech Stack:** Python 3.11+, SQLite FTS5, Pydantic v2, pytest, unittest.mock.

---

## File Structure

- `src/clerk/models.py` (modify): add `Message.body_skipped` boolean.
- `src/clerk/config.py` (modify): add `CacheConfig.body_max_bytes`.
- `src/clerk/cache.py` (modify): `store_message` writes `body_skipped` from the model instead of a literal `0`.
- `src/clerk/api.py` (modify): `sync_folder` fetches bodies eagerly; new `_prepare_body_for_storage` helper applies the HTML to text fallback and the oversize cap.
- Tests: `tests/test_models.py`, `tests/test_config.py`, `tests/test_cache.py`, `tests/test_api.py`.

Note on scope already decided in Plan 1: `store_message`'s upsert deliberately OMITS `body_skipped` from its `ON CONFLICT DO UPDATE SET` clause, so the value is set on INSERT and preserved on a header-only re-store. This plan writes the INSERT value from the model; it does not change the upsert preservation behavior.

---

### Task 1: Add `Message.body_skipped`

**Files:**
- Modify: `src/clerk/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py` (the imports for `Message`, `Address`, `datetime` already exist at module scope from Plan 1; reuse them, do not re-import):

```python
def test_message_body_skipped_defaults_false():
    msg = Message(
        message_id="<a@b>",
        conv_id="abc123",
        **{"from": Address(addr="x@y.com")},
        date=datetime(2026, 1, 1),
        uid=1,
    )
    assert msg.body_skipped is False


def test_message_body_skipped_settable():
    msg = Message(
        message_id="<a@b>",
        conv_id="abc123",
        **{"from": Address(addr="x@y.com")},
        date=datetime(2026, 1, 1),
        uid=1,
        body_skipped=True,
    )
    assert msg.body_skipped is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -k body_skipped -v`
Expected: FAIL (Pydantic ignores the unknown kwarg, so `msg.body_skipped` raises `AttributeError`, or the default-false test errors on the missing attribute).

- [ ] **Step 3: Implement**

In `src/clerk/models.py`, in the `Message` model, add the field directly below the `body_html` field (after the line `body_html: str | None = Field(default=None, description="HTML body")`):

```python
    body_skipped: bool = Field(
        default=False,
        description="True if the body was too large to cache; fetch on demand",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -k body_skipped -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/models.py tests/test_models.py
git commit -m "feat(models): add Message.body_skipped"
```

---

### Task 2: Add `CacheConfig.body_max_bytes`

**Files:**
- Modify: `src/clerk/config.py:138-143` (the `CacheConfig` class)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py` (merge the import into the existing `from clerk.config import ...` block if one exists, to keep ruff clean):

```python
from clerk.config import CacheConfig


def test_cache_config_body_max_bytes_default():
    assert CacheConfig().body_max_bytes == 1_000_000


def test_cache_config_body_max_bytes_custom():
    assert CacheConfig(body_max_bytes=2048).body_max_bytes == 2048
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -k body_max_bytes -v`
Expected: FAIL (`AttributeError`: `CacheConfig` has no `body_max_bytes`).

- [ ] **Step 3: Implement**

In `src/clerk/config.py`, in the `CacheConfig` class, add the field below `body_freshness_min`:

```python
    body_max_bytes: int = Field(
        default=1_000_000,
        ge=1024,
        description="Bodies larger than this are not cached (fetched on demand)",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -k body_max_bytes -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/config.py tests/test_config.py
git commit -m "feat(config): add CacheConfig.body_max_bytes"
```

---

### Task 3: Persist `body_skipped` from the model in `store_message`

**Files:**
- Modify: `src/clerk/cache.py` (`store_message`, the `body_skipped` VALUES entry)
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cache.py` (reuse the `_msg` helper added in Plan 1 Task 4, which builds a `Message`; it accepts keyword overrides for the fields it sets, but NOT `body_skipped`. Construct the Message directly here so you can set `body_skipped`):

```python
def test_store_message_persists_body_skipped_true(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    msg = _msg(30, "<bs1@x>")
    msg.body_skipped = True
    cache.store_message(msg)
    with cache._connect() as conn:
        row = conn.execute(
            "SELECT body_skipped FROM messages WHERE account='acct' AND folder='INBOX' AND uid=30"
        ).fetchone()
    assert row["body_skipped"] == 1


def test_store_message_persists_body_skipped_false(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(31, "<bs2@x>"))
    with cache._connect() as conn:
        row = conn.execute(
            "SELECT body_skipped FROM messages WHERE account='acct' AND folder='INBOX' AND uid=31"
        ).fetchone()
    assert row["body_skipped"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache.py -k body_skipped -v`
Expected: FAIL on the `True` case (`store_message` currently hard-codes `0`, so the column is always 0).

- [ ] **Step 3: Implement**

In `src/clerk/cache.py`, in `store_message`, find the VALUES tuple entry that currently passes the literal `0` for `body_skipped` (it sits between `msg.body_html,` and `flags_to_bitmask(msg.flags),`). Replace that single line:

```python
                    0,
```

with:

```python
                    1 if msg.body_skipped else 0,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cache.py -k body_skipped -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/cache.py tests/test_cache.py
git commit -m "feat(cache): persist Message.body_skipped on insert"
```

---

### Task 4: Eager bodies, HTML to text, and size cap in `sync_folder`

This is the core task. `sync_folder` now fetches full bodies and runs each message through `_prepare_body_for_storage` before storing.

**Files:**
- Modify: `src/clerk/api.py` (`sync_folder` lines 540-582; add `_prepare_body_for_storage` helper)
- Test: `tests/test_api.py` (update two existing assertions in `TestSyncFolder`; add a new `TestEagerBodies` class)

- [ ] **Step 1: Write the failing tests**

Add a new test class to `tests/test_api.py` (the fixtures `api`, `cache`, and helpers `Message`, `Address`, `MagicMock`, `datetime`, `UTC` are already imported/defined at module scope):

```python
class TestEagerBodies:
    """Sync fetches full bodies and makes them searchable (FTS completeness)."""

    def _mock_client(self, monkeypatch, messages, highest_uid):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.fetch_messages_since_uid.return_value = (messages, highest_uid)
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)
        return mock_client

    def _msg(self, uid, mid, **kw):
        return Message(
            uid=uid,
            message_id=mid,
            conv_id=f"conv{uid}",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com", name="Alice")},
            to=[Address(addr="test@example.com")],
            date=datetime.now(UTC),
            subject="Subject",
            headers_fetched_at=datetime.now(UTC),
            body_fetched_at=datetime.now(UTC),
            **kw,
        )

    def test_sync_requests_bodies_eagerly(self, api, monkeypatch):
        mock_client = self._mock_client(monkeypatch, [], 0)
        api.sync_folder(account="test", folder="INBOX")
        _, kwargs = mock_client.fetch_messages_since_uid.call_args
        assert kwargs["fetch_bodies"] is True

    def test_synced_body_is_full_text_searchable(self, api, cache, monkeypatch):
        msg = self._msg(40, "<e1@x>", body_text="the quarterly pineapple report")
        self._mock_client(monkeypatch, [msg], 40)
        api.sync_folder(account="test", folder="INBOX")
        rows = cache.execute_readonly_sql(
            "SELECT m.message_id FROM messages_fts f "
            "JOIN messages m ON m.rowid = f.rowid "
            "WHERE messages_fts MATCH 'pineapple'"
        )
        assert any(r["message_id"] == "<e1@x>" for r in rows)

    def test_html_only_body_gets_text_for_search(self, api, cache, monkeypatch):
        msg = self._msg(41, "<e2@x>", body_text=None, body_html="<p>secret mango harvest</p>")
        self._mock_client(monkeypatch, [msg], 41)
        api.sync_folder(account="test", folder="INBOX")
        stored = cache.get_message("<e2@x>")
        assert stored is not None
        assert "mango" in (stored.body_text or "")
        rows = cache.execute_readonly_sql(
            "SELECT m.message_id FROM messages_fts f "
            "JOIN messages m ON m.rowid = f.rowid "
            "WHERE messages_fts MATCH 'mango'"
        )
        assert any(r["message_id"] == "<e2@x>" for r in rows)

    def test_oversized_body_is_skipped(self, api, cache, monkeypatch):
        big = "x" * 2_000_000  # exceeds the 1_000_000 default cap
        msg = self._msg(42, "<e3@x>", body_text=big)
        self._mock_client(monkeypatch, [msg], 42)
        api.sync_folder(account="test", folder="INBOX")
        stored = cache.get_message("<e3@x>")
        assert stored is not None
        assert stored.body_text is None
        with cache._connect() as conn:
            row = conn.execute(
                "SELECT body_skipped FROM messages WHERE message_id='<e3@x>'"
            ).fetchone()
        assert row["body_skipped"] == 1
```

Then UPDATE the two existing assertions in `TestSyncFolder` that currently expect header-only fetch. In `test_incremental_sync_uses_existing_uid` and `test_full_sync_ignores_sync_state`, change:

```python
        mock_client.fetch_messages_since_uid.assert_called_once_with(
            folder="INBOX",
            since_uid=50,
            fetch_bodies=False,
        )
```

to use `fetch_bodies=True` (keep the `since_uid` value as it is in each test: 50 in the first, 0 in the second):

```python
        mock_client.fetch_messages_since_uid.assert_called_once_with(
            folder="INBOX",
            since_uid=50,
            fetch_bodies=True,
        )
```

and the second one to `since_uid=0, fetch_bodies=True`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_api.py -k "EagerBodies or uses_existing_uid or full_sync_ignores" -v`
Expected: FAIL (sync still calls with `fetch_bodies=False`; no `_prepare_body_for_storage`; bodies not searchable; oversize not skipped).

- [ ] **Step 3: Implement**

In `src/clerk/api.py`, add the helper method to `ClerkAPI` (place it directly above `sync_folder`, after the `get_unread_counts` method around line 535):

```python
    def _prepare_body_for_storage(self, msg: Message) -> None:
        """Make a fetched body cache-ready before store.

        - Oversize guard: bodies whose combined text+html length exceeds the
          configured cap are dropped (body_skipped=True) and fetched on demand
          on explicit read.
        - FTS guard: if only HTML is present (common with Exchange/Outlook),
          derive a plain-text body so the FTS index has searchable content.
        """
        cap = self.config.cache.body_max_bytes
        total = len(msg.body_text or "") + len(msg.body_html or "")
        if total > cap:
            msg.body_text = None
            msg.body_html = None
            msg.body_skipped = True
            return
        if msg.body_text is None and msg.body_html:
            msg.body_text = html_to_text(msg.body_html)
```

In `sync_folder`, change the fetch call to request bodies and run each message through the helper. Replace the body of the `with get_imap_client(...)` block (currently):

```python
        with get_imap_client(account_name) as client:
            messages, highest_uid = client.fetch_messages_since_uid(
                folder=folder,
                since_uid=since_uid,
                fetch_bodies=False,
            )

            for msg in messages:
                self.cache.store_message(msg)
```

with:

```python
        with get_imap_client(account_name) as client:
            messages, highest_uid = client.fetch_messages_since_uid(
                folder=folder,
                since_uid=since_uid,
                fetch_bodies=True,
            )

            for msg in messages:
                self._prepare_body_for_storage(msg)
                self.cache.store_message(msg)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_api.py -k "EagerBodies or uses_existing_uid or full_sync_ignores" -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite, lint, and types**

Run: `pytest -q` (expect all pass)
Run: `ruff check src tests` (expect clean)
Run: `mypy src` (3 pre-existing errors in mcp_server.py are unrelated; confirm no new ones)

- [ ] **Step 6: Commit**

```bash
git add src/clerk/api.py tests/test_api.py
git commit -m "feat(sync): eager bodies with HTML-to-text and oversize cap"
```

---

## Self-Review

- **Spec coverage (Workstream 2, eager-bodies slice):** eager bodies on sync (Task 4, `fetch_bodies=True`); HTML to text at store so FTS is complete for Exchange/Outlook HTML-only mail (Task 4 helper + `test_html_only_body_gets_text_for_search`); body size cap with `body_skipped` (Tasks 1, 2, 3, 4 + `test_oversized_body_is_skipped`); FTS completeness verified by an actual MATCH (`test_synced_body_is_full_text_searchable`).
- **Deferred (later plans, intentionally absent here):** paging / removal of the first-sync 200 cap (Plan 3); parse-failure dead-letter (Plan 3); two-phase CONDSTORE/QRESYNC reconciliation of flags and expunges (Plan 4); BeautifulSoup swap for `html_to_text` (Plan 5, inherited by this path automatically); `body_skipped` count surfaced in `clerk_status` and force-fetch-on-read of a skipped body (Plan 5).
- **Placeholder scan:** none; every step has concrete code and commands.
- **Type consistency:** `Message.body_skipped: bool` (Task 1) is written by `store_message` (Task 3) and set by `_prepare_body_for_storage` (Task 4); `CacheConfig.body_max_bytes: int` (Task 2) is read by `_prepare_body_for_storage` (Task 4). `_prepare_body_for_storage(self, msg: Message) -> None` is defined once (Task 4) and called once (Task 4). `html_to_text` is the existing `api.html_to_text`, already imported in `api.py`.
- **Known minor (documented, not fixed here):** force-fetching a `body_skipped` message via `clerk_read` will populate `body_text` while leaving `body_skipped=1`; harmless (the flag is only a hint for the Plan 5 status count) and refined in Plan 5.

---

## Roadmap reminder (remaining Faithful Mirror plans)

- Plan 3: paging (remove the first-sync 200 cap, per-chunk watermark) + parse-failure dead-letter.
- Plan 4: two-phase reconciliation of flags/moves/expunges for already-cached UIDs (CONDSTORE/QRESYNC where advertised, else bounded FLAGS diff + UID SEARCH ALL set-difference).
- Plan 5: read honesty (last_sync_at staleness signal, BeautifulSoup html_to_text, DraftManager via Cache, prune wiring off-by-default, body_skipped count in status).
- Plan 6: UID-keyed mutations + atomic move; send-safety reservation; doc honesty; test hardening (the dead integration suite, `_parse_message` fixtures, send-safety layers, OAuth refresh, CI floor).
