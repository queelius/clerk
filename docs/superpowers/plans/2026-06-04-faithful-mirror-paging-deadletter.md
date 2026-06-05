# Faithful Mirror: Paging and Dead-letter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the silent first-sync 200-message cap by paging the fetch in chunks with a per-chunk, resumable watermark, and stop losing un-parseable messages by recording them in a retrying dead-letter set.

**Architecture:** Plan 3 of the "Faithful Mirror" round (spec: `docs/superpowers/specs/2026-06-04-faithful-mirror-trust-correctness-design.md`, Workstream 2). It splits `imap_client.fetch_messages_since_uid` (which capped at 200 and fetched everything at once) into a cheap `search_uids` (all matching UIDs, ascending, no cap) and a `fetch_uids` (fetch + parse a specific UID list, reporting parse failures). `api.sync_folder` then orchestrates: retry dead-lettered UIDs, then page the new UIDs ascending, advancing `sync_state` after each chunk so an interrupted sync resumes instead of skipping. UIDs are processed ascending so the watermark is monotonic and resumable. It does NOT add multi-device reconciliation of already-cached UIDs (Plan 4).

**Tech Stack:** Python 3.11+, imapclient, SQLite, Pydantic v2, pytest, unittest.mock.

---

## File Structure

- `src/clerk/config.py` (modify): add `CacheConfig.sync_chunk_size`.
- `src/clerk/imap_client.py` (modify): add `search_uids` and `fetch_uids`; delete the now-superseded `fetch_messages_since_uid`.
- `src/clerk/cache.py` (modify): add dead-letter helpers (`record_deadletter`, `get_deadletter_uids`, `clear_deadletter`) backed by `cache_meta` JSON, plus a `_DEADLETTER_MAX_ATTEMPTS` constant.
- `src/clerk/api.py` (modify): rewrite `sync_folder` to page + dead-letter using the new IMAP methods.
- Tests: `tests/test_config.py`, `tests/test_imap_m365.py` (or a new `tests/test_fetch_uids.py`), `tests/test_cache.py`, `tests/test_api.py`.

---

### Task 1: Add `CacheConfig.sync_chunk_size`

**Files:**
- Modify: `src/clerk/config.py` (`CacheConfig`)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py` (the `from clerk.config import CacheConfig` import exists from Plan 2; reuse it):

```python
def test_cache_config_sync_chunk_size_default():
    assert CacheConfig().sync_chunk_size == 200


def test_cache_config_sync_chunk_size_custom():
    assert CacheConfig(sync_chunk_size=50).sync_chunk_size == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -k sync_chunk_size -v`
Expected: FAIL (`AttributeError`, no such field).

- [ ] **Step 3: Implement**

In `src/clerk/config.py`, in `CacheConfig`, add below `body_max_bytes`:

```python
    sync_chunk_size: int = Field(
        default=200,
        ge=1,
        description="Messages fetched per sync chunk (watermark advances per chunk)",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -k sync_chunk_size -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/config.py tests/test_config.py
git commit -m "feat(config): add CacheConfig.sync_chunk_size"
```

---

### Task 2: `imap_client.search_uids` (all matching UIDs, ascending, no cap)

**Files:**
- Modify: `src/clerk/imap_client.py`
- Test: `tests/test_fetch_uids.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_fetch_uids.py`:

```python
"""search_uids returns all matching UIDs ascending, with no 200 cap."""
from unittest.mock import MagicMock

from clerk.config import AccountConfig, ImapConfig, SmtpConfig
from clerk.imap_client import ImapClient


def _client():
    cfg = AccountConfig(
        protocol="imap",
        imap=ImapConfig(host="h", username="u"),
        smtp=SmtpConfig(host="h", username="u"),
        **{"from": {"address": "u@example.com"}},
    )
    c = ImapClient("acct", cfg)
    c._client = MagicMock()
    return c


def test_search_uids_full_sync_returns_all_ascending():
    c = _client()
    c._client.search.return_value = [5, 1, 3]  # server order arbitrary
    uids = c.search_uids("INBOX", since_uid=0)
    assert uids == [1, 3, 5]
    c._client.search.assert_called_once_with(["ALL"])


def test_search_uids_incremental_filters_and_sorts():
    c = _client()
    # The "*" range can echo the since_uid itself; it must be filtered out.
    c._client.search.return_value = [50, 60, 55]
    uids = c.search_uids("INBOX", since_uid=50)
    assert uids == [55, 60]
    c._client.search.assert_called_once_with(["UID", "51:*"])


def test_search_uids_no_200_cap():
    c = _client()
    c._client.search.return_value = list(range(1, 501))  # 500 UIDs
    uids = c.search_uids("INBOX", since_uid=0)
    assert len(uids) == 500
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fetch_uids.py -k search_uids -v`
Expected: FAIL (`AttributeError`: `ImapClient` has no `search_uids`).

- [ ] **Step 3: Implement**

In `src/clerk/imap_client.py`, add this method to the `ImapClient` class (place it just above the existing `fetch_messages_since_uid`):

```python
    def search_uids(self, folder: str = "INBOX", since_uid: int = 0) -> list[int]:
        """Return all UIDs in the folder above since_uid, ascending.

        since_uid=0 returns every UID (full sync). There is no cap: the caller
        pages the subsequent fetch. The UID range form `since+1:*` can echo the
        boundary UID on some servers, so results are filtered to be strictly
        greater than since_uid. Read-only select.
        """
        self.client.select_folder(folder, readonly=True)
        if since_uid > 0:
            uids = self.client.search(["UID", f"{since_uid + 1}:*"])
            uids = [u for u in uids if u > since_uid]
        else:
            uids = self.client.search(["ALL"])
        return sorted(uids)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fetch_uids.py -k search_uids -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/imap_client.py tests/test_fetch_uids.py
git commit -m "feat(imap): add search_uids (uncapped, ascending)"
```

---

### Task 3: `imap_client.fetch_uids` (fetch a UID list, report parse failures)

**Files:**
- Modify: `src/clerk/imap_client.py`
- Test: `tests/test_fetch_uids.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_fetch_uids.py`:

```python
from datetime import datetime


class _Env:
    def __init__(self, mid):
        self.date = datetime(2026, 1, 2)
        self.subject = b"Hi"
        self.from_ = None
        self.message_id = mid


def test_fetch_uids_empty_returns_empty():
    c = _client()
    assert c.fetch_uids("INBOX", [], fetch_bodies=True) == ([], [])


def test_fetch_uids_parses_and_reports_failures():
    c = _client()
    # uid 1 parses; uid 2 has no ENVELOPE so _parse_message returns None (failure).
    c._client.fetch.return_value = {
        1: {b"ENVELOPE": _Env(b"<m1@x>"), b"FLAGS": (), b"BODY[]": b"Subject: Hi\r\n\r\nbody"},
        2: {b"FLAGS": ()},  # missing ENVELOPE -> parse returns None
    }
    messages, failed = c.fetch_uids("INBOX", [1, 2], fetch_bodies=True)
    assert [m.uid for m in messages] == [1]
    assert failed == [2]


def test_fetch_uids_requests_full_body_when_eager():
    c = _client()
    c._client.fetch.return_value = {}
    c.fetch_uids("INBOX", [1], fetch_bodies=True)
    args, _ = c._client.fetch.call_args
    assert "BODY.PEEK[]" in args[1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fetch_uids.py -k fetch_uids -v`
Expected: FAIL (`AttributeError`: no `fetch_uids`).

- [ ] **Step 3: Implement**

In `src/clerk/imap_client.py`, add this method to `ImapClient` (place it directly below `search_uids`):

```python
    def fetch_uids(
        self,
        folder: str,
        uids: Sequence[int],
        fetch_bodies: bool = True,
    ) -> tuple[list[Message], list[int]]:
        """Fetch and parse a specific set of UIDs.

        Returns (parsed_messages, failed_uids). failed_uids are UIDs the server
        returned but that could not be parsed (so the caller can dead-letter
        them). UIDs the server does not return (for example, expunged between
        search and fetch) appear in neither list. Read-only select.
        """
        if not uids:
            return [], []
        self.client.select_folder(folder, readonly=True)
        fetch_items = ["FLAGS", "ENVELOPE", "INTERNALDATE", "RFC822.SIZE"]
        fetch_items.append("BODY.PEEK[]" if fetch_bodies else "BODY.PEEK[HEADER]")
        fetch_data = self.client.fetch(list(uids), fetch_items)
        messages: list[Message] = []
        failed: list[int] = []
        now = datetime.now(UTC)
        for uid in sorted(fetch_data.keys()):
            try:
                msg = self._parse_message(uid, fetch_data[uid], folder, fetch_bodies, now)
                if msg:
                    messages.append(msg)
                else:
                    failed.append(uid)
            except Exception as e:
                import sys

                print(f"Warning: failed to parse message {uid}: {e}", file=sys.stderr)
                failed.append(uid)
        return messages, failed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fetch_uids.py -k fetch_uids -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/imap_client.py tests/test_fetch_uids.py
git commit -m "feat(imap): add fetch_uids reporting parse failures"
```

---

### Task 4: Cache dead-letter helpers

**Files:**
- Modify: `src/clerk/cache.py`
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cache.py`:

```python
def test_record_and_get_deadletter(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    assert cache.get_deadletter_uids("acct", "INBOX") == []
    cache.record_deadletter("acct", "INBOX", 7)
    assert cache.get_deadletter_uids("acct", "INBOX") == [7]


def test_deadletter_ages_out_after_max_attempts(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    for _ in range(3):  # _DEADLETTER_MAX_ATTEMPTS
        cache.record_deadletter("acct", "INBOX", 9)
    assert 9 not in cache.get_deadletter_uids("acct", "INBOX")


def test_clear_deadletter(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.record_deadletter("acct", "INBOX", 5)
    cache.clear_deadletter("acct", "INBOX", 5)
    assert cache.get_deadletter_uids("acct", "INBOX") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache.py -k deadletter -v`
Expected: FAIL (`AttributeError`: no `record_deadletter`).

- [ ] **Step 3: Implement**

In `src/clerk/cache.py`, add the constant just below the existing `SCHEMA_VERSION = 2` line:

```python
# Parse-failure dead-letter: retry a UID this many times before giving up.
_DEADLETTER_MAX_ATTEMPTS = 3
```

Add these three methods to the `Cache` class (place them just after `count_sends_since`, the last method before the module-level `get_cache`):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cache.py -k deadletter -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/cache.py tests/test_cache.py
git commit -m "feat(cache): dead-letter set for parse failures"
```

---

### Task 5: Paged, dead-letter-aware `sync_folder`

This is the core task. It rewrites `sync_folder`, deletes the superseded `fetch_messages_since_uid`, and rewrites the sync tests.

**Files:**
- Modify: `src/clerk/api.py` (`sync_folder`)
- Modify: `src/clerk/imap_client.py` (delete `fetch_messages_since_uid`)
- Test: `tests/test_api.py` (replace `TestSyncFolder`; update `TestEagerBodies`)

- [ ] **Step 1: Write the failing tests**

Replace the ENTIRE `TestSyncFolder` class in `tests/test_api.py` with:

```python
class TestSyncFolder:
    """sync_folder pages new UIDs ascending, advances the watermark per chunk,
    and dead-letters parse failures so they are retried, not lost."""

    def _client(self, monkeypatch, new_uids, fetch_result):
        """fetch_result is a (messages, failed) tuple, or a callable(uids)->tuple."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.search_uids.return_value = new_uids
        if callable(fetch_result):
            mock_client.fetch_uids.side_effect = (
                lambda folder, uids, fetch_bodies=True: fetch_result(list(uids))
            )
        else:
            mock_client.fetch_uids.return_value = fetch_result
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)
        return mock_client

    def _msg(self, uid):
        return Message(
            uid=uid,
            message_id=f"<m{uid}@x>",
            conv_id=f"c{uid}",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="a@x.com")},
            to=[Address(addr="test@example.com")],
            subject="s",
            date=datetime.now(UTC),
            headers_fetched_at=datetime.now(UTC),
        )

    def test_no_new_messages_returns_zero(self, api, monkeypatch):
        self._client(monkeypatch, [], ([], []))
        result = api.sync_folder(account="test", folder="INBOX")
        assert result["synced"] == 0
        assert result["account"] == "test"
        assert result["folder"] == "INBOX"

    def test_sync_stores_and_advances_watermark(self, api, cache, monkeypatch):
        m = self._msg(100)
        self._client(monkeypatch, [100], ([m], []))
        result = api.sync_folder(account="test", folder="INBOX")
        assert result["synced"] == 1
        assert cache.get_sync_state("test", "INBOX")["last_uid"] == 100

    def test_search_uses_existing_watermark(self, api, cache, monkeypatch):
        cache.set_sync_state("test", "INBOX", 50)
        mock_client = self._client(monkeypatch, [], ([], []))
        api.sync_folder(account="test", folder="INBOX")
        mock_client.search_uids.assert_called_once_with("INBOX", 50)

    def test_full_sync_ignores_watermark(self, api, cache, monkeypatch):
        cache.set_sync_state("test", "INBOX", 50)
        mock_client = self._client(monkeypatch, [], ([], []))
        api.sync_folder(account="test", folder="INBOX", full=True)
        mock_client.search_uids.assert_called_once_with("INBOX", 0)

    def test_no_new_messages_keeps_watermark(self, api, cache, monkeypatch):
        cache.set_sync_state("test", "INBOX", 50)
        self._client(monkeypatch, [], ([], []))
        api.sync_folder(account="test", folder="INBOX")
        assert cache.get_sync_state("test", "INBOX")["last_uid"] == 50

    def test_paging_no_200_cap(self, api, cache, monkeypatch):
        uids = list(range(1, 251))  # 250 messages, above the old 200 cap
        msgs = {u: self._msg(u) for u in uids}
        self._client(monkeypatch, uids, lambda chunk: ([msgs[u] for u in chunk], []))
        result = api.sync_folder(account="test", folder="INBOX")
        assert result["synced"] == 250
        assert cache.get_stats().message_count == 250
        assert cache.get_sync_state("test", "INBOX")["last_uid"] == 250

    def test_fetch_is_chunked(self, api, cache, monkeypatch):
        monkeypatch.setattr(api.config.cache, "sync_chunk_size", 2)
        uids = [1, 2, 3, 4, 5]
        msgs = {u: self._msg(u) for u in uids}
        mock_client = self._client(
            monkeypatch, uids, lambda chunk: ([msgs[u] for u in chunk], [])
        )
        api.sync_folder(account="test", folder="INBOX")
        assert mock_client.fetch_uids.call_count == 3  # chunks of 2: (2,2,1)

    def test_parse_failure_recorded_and_watermark_advances(self, api, cache, monkeypatch):
        self._client(monkeypatch, [10], ([], [10]))  # uid 10 fails to parse
        result = api.sync_folder(account="test", folder="INBOX")
        assert result["synced"] == 0
        assert 10 in cache.get_deadletter_uids("test", "INBOX")
        # The bad message does not block sync: the watermark still advances.
        assert cache.get_sync_state("test", "INBOX")["last_uid"] == 10

    def test_deadletter_retried_and_cleared_on_success(self, api, cache, monkeypatch):
        cache.record_deadletter("test", "INBOX", 10)
        m = self._msg(10)
        self._client(monkeypatch, [], ([m], []))  # no new uids; dl retry succeeds
        result = api.sync_folder(account="test", folder="INBOX")
        assert result["synced"] == 1
        assert cache.get_deadletter_uids("test", "INBOX") == []
        assert cache.get_message("<m10@x>") is not None
```

Then UPDATE the `TestEagerBodies` class (added in Plan 2). Replace its `_mock_client` helper and its `test_sync_requests_bodies_eagerly` method with:

```python
    def _mock_client(self, monkeypatch, messages, highest_uid):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.search_uids.return_value = [m.uid for m in messages]
        mock_client.fetch_uids.return_value = (messages, [])
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)
        return mock_client

    def test_sync_requests_bodies_eagerly(self, api, monkeypatch):
        msg = self._msg(40, "<e0@x>", body_text="hi")
        mock_client = self._mock_client(monkeypatch, [msg], 40)
        api.sync_folder(account="test", folder="INBOX")
        assert mock_client.fetch_uids.called
        _, kwargs = mock_client.fetch_uids.call_args
        assert kwargs.get("fetch_bodies") is True
```

Leave the other `TestEagerBodies` tests (`test_synced_body_is_full_text_searchable`, `test_html_only_body_gets_text_for_search`, `test_oversized_body_is_skipped`, `test_cap_measures_bytes_not_chars`) unchanged: they call `self._mock_client(...)` and assert on the cache, which still works with the new helper.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_api.py -k "TestSyncFolder or EagerBodies" -v`
Expected: FAIL (sync_folder still calls `fetch_messages_since_uid`; no `search_uids`/`fetch_uids` orchestration; dead-letter not wired).

- [ ] **Step 3: Rewrite `sync_folder`**

In `src/clerk/api.py`, replace the entire `sync_folder` method body (keep the signature and docstring intro, update the body) with:

```python
    def sync_folder(
        self,
        account: str | None = None,
        folder: str = "INBOX",
        full: bool = False,
    ) -> dict[str, Any]:
        """Sync a folder from IMAP.

        Pages new UIDs ascending, advancing sync_state after each chunk so an
        interrupted sync resumes rather than skipping. Retries previously
        dead-lettered (unparseable) UIDs first; records new parse failures in
        the dead-letter set so a single bad message neither blocks the sync nor
        is silently lost.
        """
        account_name, _ = self.config.get_account(account)

        since_uid = 0
        if not full:
            state = self.cache.get_sync_state(account_name, folder)
            if state:
                since_uid = state["last_uid"]

        chunk_size = self.config.cache.sync_chunk_size
        synced = 0
        highest_uid = since_uid

        with get_imap_client(account_name) as client:
            # 1. Retry UIDs that failed to parse on a previous sync.
            dl_uids = self.cache.get_deadletter_uids(account_name, folder)
            if dl_uids:
                msgs, _failed = client.fetch_uids(folder, dl_uids, fetch_bodies=True)
                stored = set()
                for msg in msgs:
                    self._prepare_body_for_storage(msg)
                    self.cache.store_message(msg)
                    self.cache.clear_deadletter(account_name, folder, msg.uid)
                    stored.add(msg.uid)
                    synced += 1
                # Any dead-letter UID not stored this round (still unparseable,
                # or expunged) ages out via the attempt cap.
                for uid in dl_uids:
                    if uid not in stored:
                        self.cache.record_deadletter(account_name, folder, uid)

            # 2. Page new UIDs above the watermark, ascending.
            new_uids = client.search_uids(folder, since_uid)
            for i in range(0, len(new_uids), chunk_size):
                chunk = new_uids[i : i + chunk_size]
                msgs, failed = client.fetch_uids(folder, chunk, fetch_bodies=True)
                for msg in msgs:
                    self._prepare_body_for_storage(msg)
                    self.cache.store_message(msg)
                    synced += 1
                for uid in failed:
                    self.cache.record_deadletter(account_name, folder, uid)
                chunk_max = max(chunk)
                if chunk_max > highest_uid:
                    highest_uid = chunk_max
                    self.cache.set_sync_state(account_name, folder, highest_uid)

        self.cache.mark_inbox_synced(account_name)

        return {
            "synced": synced,
            "account": account_name,
            "folder": folder,
            "last_uid": highest_uid,
        }
```

- [ ] **Step 4: Delete the superseded `fetch_messages_since_uid`**

In `src/clerk/imap_client.py`, delete the entire `fetch_messages_since_uid` method (the method that does `SEARCH ALL ... [:200]`). Confirm it is no longer referenced: `grep -rn "fetch_messages_since_uid" src/` must return nothing. (The other method `fetch_messages` is separate and stays.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_api.py -k "TestSyncFolder or EagerBodies" -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite, lint, and types**

Run: `pytest -q` (expect all pass)
Run: `ruff check src tests` (expect clean)
Run: `mypy src` (expect exactly 3 errors, all in `mcp_server.py`; confirm no new ones)

- [ ] **Step 7: Commit**

```bash
git add src/clerk/api.py src/clerk/imap_client.py tests/test_api.py
git commit -m "feat(sync): page uncapped, per-chunk watermark, dead-letter parse failures"
```

---

## Self-Review

- **Spec coverage (Workstream 2, paging+dead-letter slice):** remove the silent 200 cap via `search_uids` + paged `fetch_uids` (Tasks 2, 3, 5; `test_paging_no_200_cap`); per-chunk resumable watermark (Task 5; `test_fetch_is_chunked`, ascending processing, `set_sync_state` per chunk); parse-failure dead-letter that retries and ages out (Tasks 4, 5; `test_parse_failure_recorded_and_watermark_advances`, `test_deadletter_retried_and_cleared_on_success`, `test_deadletter_ages_out_after_max_attempts`).
- **Deferred (later plans, intentionally absent):** two-phase reconciliation of flags/moves/expunges for already-cached UIDs (Plan 4); BeautifulSoup html_to_text swap, staleness signal, prune wiring (Plan 5); UID-keyed mutations, send-safety reservation, doc/test hardening (Plan 6).
- **Placeholder scan:** none; every step has concrete code and commands.
- **Type consistency:** `search_uids(folder, since_uid) -> list[int]` (Task 2) and `fetch_uids(folder, uids, fetch_bodies) -> tuple[list[Message], list[int]]` (Task 3) are the methods `sync_folder` calls (Task 5). `record_deadletter`/`get_deadletter_uids`/`clear_deadletter` (Task 4) are called by `sync_folder` (Task 5). `CacheConfig.sync_chunk_size: int` (Task 1) is read in `sync_folder` (Task 5). `Sequence` is already imported in `imap_client.py`.
- **Resumability invariant:** UIDs are processed ascending and `set_sync_state` runs after each chunk's stores, so an interruption leaves the watermark at the last fully-stored chunk; the next sync re-fetches only from there (idempotent via the upsert).
- **Known minor (documented):** maxed-out dead-letter entries remain in the `cache_meta` JSON (not returned by `get_deadletter_uids` once at the cap); negligible for a personal mailbox and cleared by `clear()`/cache reset. An expunged dead-letter UID ages out via the attempt cap rather than being detected as expunged (expunge detection is Plan 4).

---

## Roadmap reminder (remaining Faithful Mirror plans)

- Plan 4: two-phase reconciliation of flags/moves/expunges for already-cached UIDs (CONDSTORE/QRESYNC where advertised, else bounded FLAGS diff + UID SEARCH ALL set-difference). The multi-device-drift fix.
- Plan 5: read honesty (last_sync_at staleness signal, BeautifulSoup html_to_text, DraftManager via Cache, prune wiring off-by-default, body_skipped count in status, force-fetch a skipped body on read).
- Plan 6: UID-keyed mutations + atomic move; send-safety reservation; doc honesty; test hardening (the dead integration suite, _parse_message fixtures, send-safety layers, OAuth refresh, CI floor).
