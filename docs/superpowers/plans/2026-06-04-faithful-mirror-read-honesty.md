# Faithful Mirror: Read Honesty and Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the agent aware of cache state and improve body fidelity: a real HTML-to-text parser, a `body_skipped` honesty signal (count in status, cleared on fetch), a staleness signal and cache summary in `clerk_status`, optional bounded-growth pruning (off by default), and a single SQLite connection discipline for drafts.

**Architecture:** Plan 5 of the "Faithful Mirror" round (spec: `docs/superpowers/specs/2026-06-04-faithful-mirror-trust-correctness-design.md`, Workstream 5). Five mostly-independent improvements: (1) swap the regex `html_to_text` for BeautifulSoup; (2) clear `body_skipped` when a body is later fetched and count skipped bodies in stats; (3) enrich `get_status` (hence `clerk_status`) with per-account last-sync time + a fresh/stale verdict and a cache summary; (4) wire the existing `prune_old_messages` behind a `prune_enabled` flag (default off); (5) route `DraftManager` CRUD through `Cache._connect` so drafts get WAL + busy_timeout. Force-fetch of a skipped body already works through the existing lazy `_ensure_body` path; this plan only adds the `body_skipped` clear-on-fetch.

**Tech Stack:** Python 3.11+, BeautifulSoup4, SQLite, Pydantic v2, pytest, unittest.mock.

---

## File Structure

- `pyproject.toml` (modify): add `beautifulsoup4` dependency.
- `src/clerk/api.py` (modify): rewrite `html_to_text` with bs4; enrich `get_status`; call prune in `sync_folder`.
- `src/clerk/cache.py` (modify): `update_body` clears `body_skipped`; `get_stats` counts skipped bodies; add `get_last_sync`.
- `src/clerk/config.py` (modify): add `CacheConfig.prune_enabled`.
- `src/clerk/models.py` (modify): add `CacheStats.body_skipped_count`.
- `src/clerk/drafts.py` (modify): route CRUD through `self.cache._connect`.
- Tests: `tests/test_api.py`, `tests/test_cache.py`, `tests/test_config.py`, `tests/test_models.py` (if needed), `tests/test_drafts.py`.

---

### Task 1: Replace the regex `html_to_text` with BeautifulSoup

**Files:**
- Modify: `pyproject.toml` (dependencies)
- Modify: `src/clerk/api.py` (`html_to_text`)
- Test: `tests/test_api.py` (existing `TestHtmlToText` must keep passing; add one)

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, in the `[project]` `dependencies` list, add a line (keep the list alphabetical-ish, place after `aiosmtplib`):

```
    "beautifulsoup4>=4.12.0",
```

Then run `pip install -e ".[dev]"` so bs4 is importable.

- [ ] **Step 2: Write the failing test**

The existing `TestHtmlToText` tests already pin the contract. Add one more to `tests/test_api.py` inside `TestHtmlToText`:

```python
    def test_nested_blocks_separate_lines(self):
        html = "<div><p>First para</p><p>Second para</p></div>"
        result = html_to_text(html)
        assert "First para" in result
        assert "Second para" in result
        # the two paragraphs are not run together
        assert "First paraSecond para" not in result
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/test_api.py::TestHtmlToText -v`
Expected: the new test FAILS with the regex implementation (it runs adjacent block text together in some cases) OR the suite still passes; either way, proceed to swap the implementation and confirm ALL `TestHtmlToText` tests pass afterward. (The point of this task is the bs4 swap; the existing 5 tests are the real contract.)

- [ ] **Step 4: Implement with BeautifulSoup**

In `src/clerk/api.py`, replace the entire `html_to_text` function with:

```python
def html_to_text(html_body: str) -> str:
    """Convert an HTML email body to readable plain text.

    Handles the common case of Exchange/Outlook HTML-only emails using a real
    parser: scripts/styles are dropped, <br> and block elements become line
    breaks, entities are decoded, and runs of whitespace are collapsed.
    """
    soup = BeautifulSoup(html_body, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(["p", "div"]):
        block.append("\n")
    text = soup.get_text()
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return text.strip()
```

Add the import at the top of `src/clerk/api.py` (with the other imports):

```python
from bs4 import BeautifulSoup
```

`re` is still used (whitespace collapsing) and stays imported. The `html` module import may now be unused; if `html` is not referenced anywhere else in `api.py`, remove the `import html` line to keep ruff clean. (Check with `grep -n "html\." src/clerk/api.py`; if only the removed function used it, remove the import.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_api.py::TestHtmlToText -v`
Expected: PASS (all 6 tests, including `test_decodes_entities`, `test_preserves_line_breaks`, `test_strips_style_blocks`, `test_real_outlook_fragment`, and the new nested-blocks test).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/clerk/api.py tests/test_api.py
git commit -m "feat(api): use BeautifulSoup for html_to_text"
```

---

### Task 2: Clear `body_skipped` on fetch; count skipped bodies in stats

**Files:**
- Modify: `src/clerk/models.py` (`CacheStats`)
- Modify: `src/clerk/cache.py` (`update_body`, `get_stats`)
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cache.py` (reuse the `_msg` helper):

```python
def test_get_stats_counts_body_skipped(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    normal = _msg(70, "<n70@x>", body_text="hi")
    skipped = _msg(71, "<s71@x>")
    skipped.body_skipped = True
    cache.store_message(normal)
    cache.store_message(skipped)
    assert cache.get_stats().body_skipped_count == 1


def test_update_body_clears_body_skipped(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    skipped = _msg(72, "<s72@x>")
    skipped.body_skipped = True
    cache.store_message(skipped)
    cache.update_body("<s72@x>", "recovered text", None)
    got = cache.get_message("<s72@x>")
    assert got.body_text == "recovered text"
    assert got.body_skipped is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache.py -k "counts_body_skipped or clears_body_skipped" -v`
Expected: FAIL (`CacheStats` has no `body_skipped_count`; `update_body` leaves `body_skipped=1`).

- [ ] **Step 3: Implement**

In `src/clerk/models.py`, add a field to `CacheStats` (after `cache_size_bytes`):

```python
    body_skipped_count: int = 0
```

In `src/clerk/cache.py`, change `update_body` to also clear the skip flag. Replace its `UPDATE` statement:

```python
                """
                UPDATE messages
                SET body_text = ?, body_html = ?, body_fetched_at = ?
                WHERE message_id = ?
                """,
                (body_text, body_html, datetime.now(UTC).isoformat(), message_id),
```

with:

```python
                """
                UPDATE messages
                SET body_text = ?, body_html = ?, body_fetched_at = ?, body_skipped = 0
                WHERE message_id = ?
                """,
                (body_text, body_html, datetime.now(UTC).isoformat(), message_id),
```

In `get_stats`, compute the count and pass it. After the `newest` query, add:

```python
            skipped = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE body_skipped = 1"
            ).fetchone()[0]
```

and add `body_skipped_count=skipped,` to the `CacheStats(...)` constructor call.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cache.py -k "counts_body_skipped or clears_body_skipped" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/models.py src/clerk/cache.py tests/test_cache.py
git commit -m "feat(cache): count skipped bodies and clear body_skipped on fetch"
```

---

### Task 3: Staleness signal and cache summary in `get_status`

**Files:**
- Modify: `src/clerk/cache.py` (add `get_last_sync`)
- Modify: `src/clerk/api.py` (`get_status`)
- Test: `tests/test_cache.py`, `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cache.py`:

```python
def test_get_last_sync_none_then_set(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    assert cache.get_last_sync("acct") is None
    cache.mark_inbox_synced("acct")
    assert cache.get_last_sync("acct") is not None
```

Add a new test class to `tests/test_api.py`:

```python
class TestStatusEnrichment:
    """clerk_status (via get_status) reports staleness and a cache summary."""

    def _mock_imap(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.list_folders.return_value = []
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)

    def test_status_reports_fresh_after_sync(self, api, cache, monkeypatch):
        self._mock_imap(monkeypatch)
        cache.mark_inbox_synced("test")
        status = api.get_status()
        assert status["accounts"]["test"]["last_sync"] is not None
        assert status["accounts"]["test"]["stale"] is False

    def test_status_reports_stale_when_old(self, api, cache, monkeypatch):
        from datetime import timedelta

        self._mock_imap(monkeypatch)
        old = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        cache.set_meta("inbox_sync_test", old)
        status = api.get_status()
        assert status["accounts"]["test"]["stale"] is True

    def test_status_includes_cache_summary(self, api, cache, monkeypatch):
        self._mock_imap(monkeypatch)
        m = Message(
            uid=80, message_id="<c80@x>", conv_id="cc80", account="test", folder="INBOX",
            **{"from": Address(addr="a@x.com")}, to=[Address(addr="t@x.com")],
            subject="s", date=datetime.now(UTC), body_text="hi",
            headers_fetched_at=datetime.now(UTC),
        )
        cache.store_message(m)
        status = api.get_status()
        assert status["cache"]["message_count"] == 1
        assert "body_skipped_count" in status["cache"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cache.py -k get_last_sync -v` and `pytest tests/test_api.py -k TestStatusEnrichment -v`
Expected: FAIL (`get_last_sync` missing; `get_status` has no `last_sync`/`stale`/`cache`).

- [ ] **Step 3: Implement**

In `src/clerk/cache.py`, add this method (place it near `is_inbox_fresh`/`mark_inbox_synced`):

```python
    def get_last_sync(self, account: str) -> datetime | None:
        """The last time this account was synced, or None if never."""
        raw = self.get_meta(f"inbox_sync_{account}")
        return datetime.fromisoformat(raw) if raw else None
```

In `src/clerk/api.py`, replace the `get_status` method with:

```python
    def get_status(self) -> dict[str, Any]:
        """Overall status: version, per-account connection + freshness, cache summary."""
        from . import __version__

        freshness = self.config.cache.inbox_freshness_min
        now = datetime.now(UTC)
        status: dict[str, Any] = {"version": __version__, "accounts": {}}

        for name in self.config.accounts:
            acct: dict[str, Any] = {}
            try:
                with get_imap_client(name) as client:
                    acct["connected"] = True
                    acct["folders"] = len(client.list_folders())
            except Exception as e:
                acct["connected"] = False
                acct["error"] = str(e)
            last_sync = self.cache.get_last_sync(name)
            acct["last_sync"] = last_sync.isoformat() if last_sync else None
            acct["stale"] = (
                last_sync is None or (now - last_sync) > timedelta(minutes=freshness)
            )
            status["accounts"][name] = acct

        stats = self.cache.get_stats()
        status["cache"] = {
            "message_count": stats.message_count,
            "body_skipped_count": stats.body_skipped_count,
            "oldest_message": (
                stats.oldest_message.isoformat() if stats.oldest_message else None
            ),
            "newest_message": (
                stats.newest_message.isoformat() if stats.newest_message else None
            ),
            "cache_size_bytes": stats.cache_size_bytes,
        }
        return status
```

(`timedelta` is already imported in `api.py`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_cache.py -k get_last_sync -v` and `pytest tests/test_api.py -k TestStatusEnrichment -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite to catch status-shape assumptions**

Run: `pytest -q`
The added keys (`last_sync`, `stale`, `cache`) are additive. If any existing test (e.g. in `tests/test_cli.py` or `tests/test_mcp_redesign.py`) asserts an exact status dict shape and now fails, update that assertion to allow the new keys (do not remove the new keys). Re-run until green.

- [ ] **Step 6: Commit**

```bash
git add src/clerk/cache.py src/clerk/api.py tests/test_cache.py tests/test_api.py
git commit -m "feat(status): report per-account staleness and a cache summary"
```

---

### Task 4: Optional bounded-growth pruning (off by default)

**Files:**
- Modify: `src/clerk/config.py` (`CacheConfig.prune_enabled`)
- Modify: `src/clerk/api.py` (`sync_folder`)
- Test: `tests/test_config.py`, `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
def test_cache_config_prune_disabled_by_default():
    assert CacheConfig().prune_enabled is False
```

Add to `tests/test_api.py` inside the existing `TestReconcile`-style pattern (new class):

```python
class TestPrune:
    """Pruning is off by default; when enabled, sync drops messages older than window_days."""

    def _client(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.search_uids.return_value = []
        mock_client.fetch_flags.return_value = {}
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)
        return mock_client

    def _store_old(self, cache, uid, days_old):
        from datetime import timedelta

        cache.store_message(
            Message(
                uid=uid, message_id=f"<p{uid}@x>", conv_id=f"pc{uid}", account="test",
                folder="INBOX", **{"from": Address(addr="a@x.com")},
                to=[Address(addr="t@x.com")], subject="s",
                date=datetime.now(UTC) - timedelta(days=days_old),
                headers_fetched_at=datetime.now(UTC),
            )
        )

    def test_prune_disabled_keeps_old_messages(self, api, cache, monkeypatch):
        monkeypatch.setattr(api.config.cache, "reconcile_window", 0)  # isolate prune
        self._store_old(cache, 90, days_old=365)
        self._client(monkeypatch)
        result = api.sync_folder(account="test", folder="INBOX")
        assert result["pruned"] == 0
        assert cache.get_message("<p90@x>") is not None

    def test_prune_enabled_drops_old_messages(self, api, cache, monkeypatch):
        monkeypatch.setattr(api.config.cache, "reconcile_window", 0)  # isolate prune
        monkeypatch.setattr(api.config.cache, "prune_enabled", True)
        monkeypatch.setattr(api.config.cache, "window_days", 30)
        self._store_old(cache, 91, days_old=365)  # older than the 30-day window
        self._client(monkeypatch)
        result = api.sync_folder(account="test", folder="INBOX")
        assert result["pruned"] >= 1
        assert cache.get_message("<p91@x>") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_config.py -k prune_disabled -v` and `pytest tests/test_api.py -k TestPrune -v`
Expected: FAIL (`prune_enabled` missing; `sync_folder` does not prune and has no `pruned` key).

- [ ] **Step 3: Implement**

In `src/clerk/config.py`, add to `CacheConfig` (below `reconcile_window`):

```python
    prune_enabled: bool = Field(
        default=False,
        description="If true, sync deletes cached messages older than window_days",
    )
```

In `src/clerk/api.py`, in `sync_folder`, after the line `self.cache.mark_inbox_synced(account_name)` and before the `return`, add:

```python
        pruned = 0
        if self.config.cache.prune_enabled:
            pruned = self.cache.prune_old_messages(self.config.cache.window_days)
```

and add `"pruned": pruned,` to the returned dict (after `"expunged"`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_config.py -k prune_disabled -v` and `pytest tests/test_api.py -k TestPrune -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clerk/config.py src/clerk/api.py tests/test_config.py tests/test_api.py
git commit -m "feat(sync): optional bounded-growth pruning, off by default"
```

---

### Task 5: Route DraftManager CRUD through Cache._connect

**Files:**
- Modify: `src/clerk/drafts.py`
- Test: `tests/test_drafts.py` (existing tests must keep passing)

- [ ] **Step 1: Confirm the existing behavior is covered**

Run: `pytest tests/test_drafts.py -v`
Expected: PASS (these tests exercise create/get/list/update/delete and are the contract this refactor must preserve).

- [ ] **Step 2: Refactor the four CRUD methods**

In `src/clerk/drafts.py`, the methods `_save`, `get`, `list`, and `delete` each open their own `sqlite3.connect(db_path)` without WAL/busy_timeout. Route them through the shared `Cache._connect` (which sets WAL, busy_timeout, and `row_factory`). For each method:

- In `_save`: replace
  ```python
        db_path = get_data_dir() / "cache.db"
        with sqlite3.connect(db_path) as conn:
  ```
  with
  ```python
        with self.cache._connect() as conn:
  ```
- In `get`: replace
  ```python
        db_path = get_data_dir() / "cache.db"
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
  ```
  with
  ```python
        with self.cache._connect() as conn:
  ```
  (`Cache._connect` already sets `row_factory = sqlite3.Row`.)
- In `list`: replace
  ```python
        db_path = get_data_dir() / "cache.db"
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
  ```
  with
  ```python
        with self.cache._connect() as conn:
  ```
- In `delete`: replace
  ```python
        db_path = get_data_dir() / "cache.db"
        with sqlite3.connect(db_path) as conn:
  ```
  with
  ```python
        with self.cache._connect() as conn:
  ```

Then remove the now-unused imports at the top of `drafts.py`: `import sqlite3` and `get_data_dir` (from the `from .config import get_config, get_data_dir` line, leaving `from .config import get_config`). Confirm with `grep -n "sqlite3\|get_data_dir" src/clerk/drafts.py` that they are no longer referenced.

- [ ] **Step 3: Run the tests to verify behavior is unchanged**

Run: `pytest tests/test_drafts.py -v`
Expected: PASS (same behavior; drafts now share the WAL connection discipline).

- [ ] **Step 4: Full suite + lint + types**

Run: `pytest -q` (expect all pass)
Run: `ruff check src tests` (expect clean; fix any unused-import findings from the removed imports)
Run: `mypy src` (expect exactly 3 errors, all `mcp_server.py`; confirm no new ones)

- [ ] **Step 5: Commit**

```bash
git add src/clerk/drafts.py
git commit -m "refactor(drafts): route CRUD through Cache connection (WAL, busy_timeout)"
```

---

## Self-Review

- **Spec coverage (Workstream 5):** BeautifulSoup `html_to_text` (Task 1); `body_skipped` count in status + cleared on fetch (Tasks 2, 3); staleness signal `last_sync`/`stale` per account in `clerk_status` (Task 3); cache summary in status (Task 3); `prune_old_messages` wired off-by-default (Task 4); `DraftManager` through `Cache` connection (Task 5). Force-fetch of a skipped body already works via the existing lazy `_ensure_body` (body_text None triggers a fetch); Task 2 adds the clear-on-fetch so the skipped count stays honest.
- **Deferred (Plan 6):** UID-keyed user-facing flag/move mutations + atomic move; send-safety reservation; doc honesty (README/CLAUDE.md); test hardening (dead integration suite, `_parse_message` fixtures, send-safety layers, OAuth refresh, CI floor). Later follow-on: CONDSTORE efficient reconciliation.
- **Placeholder scan:** none; every step has concrete code and commands.
- **Type consistency:** `CacheStats.body_skipped_count: int` (Task 2) is set in `get_stats` (Task 2) and read in `get_status` (Task 3). `cache.get_last_sync(account) -> datetime | None` (Task 3) is read in `get_status` (Task 3). `CacheConfig.prune_enabled: bool` (Task 4) is read in `sync_folder` (Task 4); `cache.prune_old_messages(window_days) -> int` already exists. `html_to_text` keeps its `(str) -> str` signature (Task 1). `Cache._connect` is reused by `DraftManager` (Task 5).
- **Test isolation note:** the prune tests set `reconcile_window=0` so the reconciliation phase (which would otherwise re-fetch flags for the old cached message and, with an empty mock `fetch_flags`, delete it as expunged) does not confound the prune assertions.
- **Known minor (documented):** `mark_inbox_synced(account)` (and thus `get_last_sync`/`stale`) is per-account, not per-folder; for INBOX-only sync this is the right granularity. Using `Cache._connect` (a leading-underscore method) from `DraftManager` is an intentional single-connection-discipline choice, not a layering violation, since drafts and the cache share one database file.

---

## Roadmap reminder (remaining Faithful Mirror plans)

- Plan 6: UID-keyed user-facing mutations (flag/move keyed on (account, folder, uid)) + atomic UID MOVE; send-safety reservation (log_send pending->sent/failed); doc honesty (README/CLAUDE.md, priorities advisory); test hardening (repair the dead integration suite, `_parse_message` fixtures, the 3 untested send-safety layers, OAuth refresh, CI cov floor).
- Later follow-on: CONDSTORE/QRESYNC efficient reconciliation delta over the same semantics Plan 4 established.
