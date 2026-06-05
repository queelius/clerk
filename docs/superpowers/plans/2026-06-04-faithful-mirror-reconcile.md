# Faithful Mirror: Multi-device Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make incremental sync correct the flags and remove the ghost rows of already-cached messages that were read, flagged, moved, or deleted on another device, by re-fetching FLAGS for the most-recent cached UIDs each sync (updating changed flags, deleting UIDs the server no longer returns).

**Architecture:** Plan 4 of the "Faithful Mirror" round (spec: `docs/superpowers/specs/2026-06-04-faithful-mirror-trust-correctness-design.md`, Workstream 2). The UID-watermark sync is append-only: it never re-examines an already-cached message, so flag/folder/delete changes made outside clerk drift forever. This plan adds a bounded reconciliation phase to `sync_folder`: it re-fetches FLAGS for the highest N cached UIDs in the folder; UIDs the server returns get their flags corrected (only when changed, to avoid FTS churn); UIDs the server omits were expunged elsewhere and are deleted. A single `FETCH FLAGS` yields both halves (returned = update, absent = delete). Reconciliation is bounded to the recent window (config `reconcile_window`), which covers the mail a user re-triages on a phone; drift on older mail is still corrected only by a full sync. The efficient CONDSTORE/QRESYNC delta path is deferred to a later follow-on; this brute-force path is the universally-supported fallback and delivers the correctness goal on its own.

**Tech Stack:** Python 3.11+, imapclient, SQLite, Pydantic v2, pytest, unittest.mock.

---

## File Structure

- `src/clerk/config.py` (modify): add `CacheConfig.reconcile_window`.
- `src/clerk/cache.py` (modify): add `get_recent_uid_flags`, `update_flags_by_uid`, `delete_by_uid` (UID-keyed, which also seed Plan 6's UID-keyed mutations).
- `src/clerk/imap_client.py` (modify): add `fetch_flags`.
- `src/clerk/api.py` (modify): add `_reconcile_recent` and call it as the first phase of `sync_folder`.
- Tests: `tests/test_config.py`, `tests/test_cache.py`, `tests/test_fetch_uids.py`, `tests/test_api.py`.

---

### Task 1: Add `CacheConfig.reconcile_window`

**Files:**
- Modify: `src/clerk/config.py` (`CacheConfig`)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py` (reuse the existing `from clerk.config import CacheConfig`):

```python
def test_cache_config_reconcile_window_default():
    assert CacheConfig().reconcile_window == 500


def test_cache_config_reconcile_window_can_disable():
    assert CacheConfig(reconcile_window=0).reconcile_window == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -k reconcile_window -v`
Expected: FAIL (`AttributeError`, no such field).

- [ ] **Step 3: Implement**

In `src/clerk/config.py`, in `CacheConfig`, add below `sync_chunk_size`:

```python
    reconcile_window: int = Field(
        default=500,
        ge=0,
        description="Re-check flags/expunges for the most-recent N cached UIDs per sync (0 disables)",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -k reconcile_window -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/config.py tests/test_config.py
git commit -m "feat(config): add CacheConfig.reconcile_window"
```

---

### Task 2: UID-keyed cache helpers for reconciliation

**Files:**
- Modify: `src/clerk/cache.py`
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cache.py` (reuse the `_msg` helper from Plan 1, which builds a `Message` keyed on `account='acct', folder='INBOX'`):

```python
def test_get_recent_uid_flags_returns_highest_uids(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(1, "<a1@x>", flags=[MessageFlag.SEEN]))
    cache.store_message(_msg(2, "<a2@x>", flags=[]))
    cache.store_message(_msg(3, "<a3@x>", flags=[MessageFlag.FLAGGED]))
    # window of 2 returns the two highest UIDs with their flag bitmasks
    recent = cache.get_recent_uid_flags("acct", "INBOX", 2)
    assert set(recent.keys()) == {2, 3}
    assert recent[3] == 4  # FLAGGED bit


def test_update_flags_by_uid(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(5, "<u5@x>", flags=[]))
    cache.update_flags_by_uid("acct", "INBOX", 5, [MessageFlag.SEEN])
    assert MessageFlag.SEEN in cache.get_message("<u5@x>").flags


def test_delete_by_uid(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(6, "<d6@x>"))
    cache.delete_by_uid("acct", "INBOX", 6)
    assert cache.get_message("<d6@x>") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache.py -k "recent_uid_flags or update_flags_by_uid or delete_by_uid" -v`
Expected: FAIL (`AttributeError`: no `get_recent_uid_flags`).

- [ ] **Step 3: Implement**

In `src/clerk/cache.py`, add these three methods to the `Cache` class (place them directly after `update_flags`, around line 582):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cache.py -k "recent_uid_flags or update_flags_by_uid or delete_by_uid" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/cache.py tests/test_cache.py
git commit -m "feat(cache): UID-keyed flag-read, flag-update, and delete helpers"
```

---

### Task 3: `imap_client.fetch_flags`

**Files:**
- Modify: `src/clerk/imap_client.py`
- Test: `tests/test_fetch_uids.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_fetch_uids.py` (the `_client()` helper and `MagicMock` import exist from Plan 3):

```python
from clerk.models import MessageFlag


def test_fetch_flags_empty_returns_empty():
    c = _client()
    assert c.fetch_flags("INBOX", []) == {}


def test_fetch_flags_maps_returned_uids_only():
    c = _client()
    # Server returns FLAGS for uid 1 and 2; uid 3 (requested) is absent (expunged).
    c._client.fetch.return_value = {
        1: {b"FLAGS": (b"\\Seen",)},
        2: {b"FLAGS": ()},
    }
    result = c.fetch_flags("INBOX", [1, 2, 3])
    assert set(result.keys()) == {1, 2}
    assert MessageFlag.SEEN in result[1]
    assert result[2] == []
    # FLAGS-only fetch (no body)
    args, _ = c._client.fetch.call_args
    assert args[1] == ["FLAGS"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fetch_uids.py -k fetch_flags -v`
Expected: FAIL (no `fetch_flags`).

- [ ] **Step 3: Implement**

In `src/clerk/imap_client.py`, add this method to `ImapClient` (place it directly below `fetch_uids`):

```python
    def fetch_flags(
        self, folder: str, uids: Sequence[int]
    ) -> dict[int, list[MessageFlag]]:
        """Fetch just FLAGS for a set of UIDs.

        Returns {uid: flags} for the UIDs the server still has. UIDs that were
        requested but are absent from the response have been expunged, so the
        caller can delete them. Read-only select.
        """
        if not uids:
            return {}
        self.client.select_folder(folder, readonly=True)
        fetch_data = self.client.fetch(list(uids), ["FLAGS"])
        return {
            uid: imap_flags_to_model(data.get(b"FLAGS", ()))
            for uid, data in fetch_data.items()
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fetch_uids.py -k fetch_flags -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/imap_client.py tests/test_fetch_uids.py
git commit -m "feat(imap): add fetch_flags for reconciliation"
```

---

### Task 4: Reconciliation phase in `sync_folder`

This is the core task. It adds `_reconcile_recent` and calls it as the first phase of `sync_folder`.

**Files:**
- Modify: `src/clerk/api.py` (add `flags_to_bitmask` import; add `_reconcile_recent`; modify `sync_folder`)
- Test: `tests/test_api.py` (add `TestReconcile`)

- [ ] **Step 1: Write the failing tests**

First, ensure `MessageFlag` is importable at module scope in `tests/test_api.py`: add `MessageFlag` to the existing `from clerk.models import Address, Message` line so it reads `from clerk.models import Address, Message, MessageFlag`.

Then add a new test class to `tests/test_api.py`:

```python
class TestReconcile:
    """sync_folder reconciles flags and expunges for the recent cached window."""

    def _store(self, cache, uid, flags=None):
        cache.store_message(
            Message(
                uid=uid,
                message_id=f"<r{uid}@x>",
                conv_id=f"rc{uid}",
                account="test",
                folder="INBOX",
                **{"from": Address(addr="a@x.com")},
                to=[Address(addr="t@x.com")],
                subject="s",
                date=datetime.now(UTC),
                flags=flags or [],
                headers_fetched_at=datetime.now(UTC),
            )
        )

    def _client(self, monkeypatch, flags_by_uid):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.search_uids.return_value = []  # no new mail this sync
        mock_client.fetch_flags.return_value = flags_by_uid
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)
        return mock_client

    def test_reconcile_updates_changed_flags(self, api, cache, monkeypatch):
        self._store(cache, 5, flags=[])  # unread in cache
        self._client(monkeypatch, {5: [MessageFlag.SEEN]})  # read on the server
        api.sync_folder(account="test", folder="INBOX")
        assert MessageFlag.SEEN in cache.get_message("<r5@x>").flags

    def test_reconcile_deletes_expunged_ghost(self, api, cache, monkeypatch):
        self._store(cache, 6)
        self._client(monkeypatch, {})  # server returns nothing -> uid 6 expunged
        api.sync_folder(account="test", folder="INBOX")
        assert cache.get_message("<r6@x>") is None

    def test_reconcile_bounded_by_window(self, api, cache, monkeypatch):
        for u in (1, 2, 3):
            self._store(cache, u)
        monkeypatch.setattr(api.config.cache, "reconcile_window", 2)
        mock_client = self._client(monkeypatch, {2: [], 3: []})
        api.sync_folder(account="test", folder="INBOX")
        args, _ = mock_client.fetch_flags.call_args
        assert set(args[1]) == {2, 3}  # only the 2 most-recent UIDs

    def test_reconcile_disabled_when_window_zero(self, api, cache, monkeypatch):
        self._store(cache, 8)
        monkeypatch.setattr(api.config.cache, "reconcile_window", 0)
        mock_client = self._client(monkeypatch, {})
        api.sync_folder(account="test", folder="INBOX")
        mock_client.fetch_flags.assert_not_called()

    def test_reconcile_skips_unchanged_flags(self, api, cache, monkeypatch):
        self._store(cache, 9, flags=[MessageFlag.SEEN])
        self._client(monkeypatch, {9: [MessageFlag.SEEN]})  # unchanged
        spy = MagicMock()
        monkeypatch.setattr(cache, "update_flags_by_uid", spy)
        api.sync_folder(account="test", folder="INBOX")
        spy.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_api.py -k TestReconcile -v`
Expected: FAIL (sync_folder has no reconciliation phase; `fetch_flags` never called).

- [ ] **Step 3: Implement**

In `src/clerk/api.py`, add `flags_to_bitmask` to the model imports. Change the import block:

```python
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
    flags_to_bitmask,
)
```

Add the `_reconcile_recent` helper to `ClerkAPI`, placed directly above `sync_folder` (after `_prepare_body_for_storage`):

```python
    def _reconcile_recent(
        self, client: Any, account_name: str, folder: str
    ) -> dict[str, int]:
        """Reconcile flags and expunges for the most-recent cached UIDs.

        Re-fetches FLAGS for the recent window (config reconcile_window). UIDs
        the server returns get their flags corrected when they differ; UIDs it
        omits were expunged elsewhere and are deleted. Bounded to the recent
        window; drift on older mail is corrected only by a full sync. A single
        FETCH FLAGS gives both halves (returned = update, absent = delete).
        """
        window = self.config.cache.reconcile_window
        if window <= 0:
            return {"reconciled": 0, "expunged": 0}
        cached = self.cache.get_recent_uid_flags(account_name, folder, window)
        if not cached:
            return {"reconciled": 0, "expunged": 0}

        server_flags = client.fetch_flags(folder, list(cached.keys()))
        reconciled = 0
        expunged = 0
        for uid, cached_mask in cached.items():
            if uid in server_flags:
                if flags_to_bitmask(server_flags[uid]) != cached_mask:
                    self.cache.update_flags_by_uid(
                        account_name, folder, uid, server_flags[uid]
                    )
                    reconciled += 1
            else:
                self.cache.delete_by_uid(account_name, folder, uid)
                expunged += 1
        return {"reconciled": reconciled, "expunged": expunged}
```

In `sync_folder`, add the reconciliation phase as the first step inside the `with` block, and include its counts in the return value. Replace the current `with get_imap_client(account_name) as client:` block opener plus its first comment line:

```python
        with get_imap_client(account_name) as client:
            # 1. Retry UIDs that failed to parse on a previous sync.
```

with:

```python
        with get_imap_client(account_name) as client:
            # 0. Reconcile flags and expunges for the recent cached window
            #    (changes made on another device since the last sync).
            recon = self._reconcile_recent(client, account_name, folder)

            # 1. Retry UIDs that failed to parse on a previous sync.
```

Then change the `return {` dict at the end of `sync_folder` from:

```python
        return {
            "synced": synced,
            "account": account_name,
            "folder": folder,
            "last_uid": highest_uid,
        }
```

to:

```python
        return {
            "synced": synced,
            "reconciled": recon["reconciled"],
            "expunged": recon["expunged"],
            "account": account_name,
            "folder": folder,
            "last_uid": highest_uid,
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_api.py -k TestReconcile -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the full suite, lint, and types**

Run: `pytest -q` (expect all pass; the existing sync/eager tests are unaffected because reconciliation no-ops on an empty cache, returning before any `fetch_flags` call)
Run: `ruff check src tests` (expect clean)
Run: `mypy src` (expect exactly 3 errors, all in `mcp_server.py`; confirm no new ones)

- [ ] **Step 6: Commit**

```bash
git add src/clerk/api.py tests/test_api.py
git commit -m "feat(sync): reconcile flags and expunges for the recent cached window"
```

---

## Self-Review

- **Spec coverage (Workstream 2, reconciliation slice):** flag reconciliation for already-cached messages (Tasks 2, 3, 4; `test_reconcile_updates_changed_flags`); expunge/ghost removal (`test_reconcile_deletes_expunged_ghost`); bounded to a recent window to cap per-sync cost (`test_reconcile_bounded_by_window`, `test_reconcile_disabled_when_window_zero`); FTS-churn avoidance by skipping unchanged flags (`test_reconcile_skips_unchanged_flags`).
- **Deferred (later plans, intentionally absent):** CONDSTORE/QRESYNC efficient delta path (a follow-on over the same semantics; this brute-force path is the universal fallback); reconciliation of mail older than the window (a full sync still covers it; CONDSTORE later removes the bound); read-honesty staleness signal etc. (Plan 5); UID-keyed mutations for the user-facing flag/move ops + send-safety + docs/tests (Plan 6).
- **Placeholder scan:** none; every step has concrete code and commands.
- **Type consistency:** `get_recent_uid_flags(...) -> dict[int, int]`, `update_flags_by_uid(account, folder, uid, flags)`, `delete_by_uid(account, folder, uid)` (Task 2) are exactly what `_reconcile_recent` (Task 4) calls; `fetch_flags(folder, uids) -> dict[int, list[MessageFlag]]` (Task 3) is called in `_reconcile_recent`; `CacheConfig.reconcile_window: int` (Task 1) is read in `_reconcile_recent`; `flags_to_bitmask` (from models) is imported in api.py (Task 4) and used to compare server flags against the cached bitmask. `_reconcile_recent(self, client, account_name, folder)` is defined once (Task 4) and called once (Task 4).
- **No-op-on-empty invariant:** `_reconcile_recent` returns before any IMAP call when `reconcile_window <= 0` or the cache has no rows in the folder, so all existing sync/eager tests (which sync into an empty cache) are unaffected; the new return keys (`reconciled`, `expunged`) are additive and do not break callers that read `synced`/`account`/`folder`/`last_uid`.
- **Known minor (documented):** reconciliation runs every sync over a flags-only FETCH of the recent window (cheap; no bodies). Flag changes are written only when they differ from the cache, so FTS triggers fire only for genuinely-changed rows. A message moved OUT of the folder on another device looks like an expunge here (absent from the folder's UID set) and is deleted from the cached folder view, which is correct for a per-folder mirror.

---

## Roadmap reminder (remaining Faithful Mirror plans)

- Plan 5: read honesty (last_sync_at staleness signal in clerk_status, BeautifulSoup html_to_text swap, DraftManager via Cache, prune wiring off-by-default, body_skipped count in status, force-fetch a skipped body on read).
- Plan 6: UID-keyed user-facing mutations (flag/move keyed on (account, folder, uid) instead of a Message-ID search) + atomic UID MOVE; send-safety reservation (log_send pending->sent/failed); doc honesty (README/CLAUDE.md); test hardening (the dead integration suite, _parse_message fixtures, send-safety layers, OAuth refresh, CI floor).
- Later follow-on: CONDSTORE/QRESYNC efficient reconciliation delta (over the same semantics this plan establishes).
