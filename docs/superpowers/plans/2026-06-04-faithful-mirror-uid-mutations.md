# Faithful Mirror: UID-keyed Mutations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mutate flags and folders by the stored server UID instead of a per-call `SEARCH HEADER Message-ID`, and make moves atomic via `client.move` (UID MOVE where supported), eliminating a round-trip per mutation, the duplicate-Message-ID ambiguity, and the non-atomic copy+`\Deleted`+plain-`expunge()` that can purge unrelated deleted mail.

**Architecture:** Second slice of Plan 6. The cache now stores each message's `(account, folder, uid)`, so `api`'s mutations can pass the UID straight to IMAP. New `*_by_uid` IMAP methods do `select_folder` + a direct UID flag/move (no search). `api.set_flag`/`move_message`/`archive_message` use the UID path when the message is cached (the normal case after sync), and fall back to the legacy Message-ID-search methods only when the message is not cached. Atomic move uses `imapclient.move` (UID MOVE where the server advertises MOVE, properly-scoped fallback otherwise); the legacy `move_message` is also switched off plain `expunge()` onto `client.move`.

**Tech Stack:** Python 3.11+, imapclient 3.0.1 (`move`, `has_capability`), SQLite, pytest, unittest.mock.

---

## File Structure

- `src/clerk/imap_client.py` (modify): add `add_flags_by_uid`, `remove_flags_by_uid`, `set_flags_by_uid`, `move_message_by_uid`, `find_archive_folder`, `archive_message_by_uid`; switch legacy `move_message` onto `client.move`.
- `src/clerk/api.py` (modify): `set_flag`, `move_message`, `archive_message` use the UID path when cached.
- Tests: `tests/test_fetch_uids.py` (IMAP-level), `tests/test_api.py` (api-level; update existing `TestSetFlag` / `TestMessageActions`).

---

### Task 1: IMAP flag mutations by UID

**Files:**
- Modify: `src/clerk/imap_client.py`
- Test: `tests/test_fetch_uids.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_fetch_uids.py` (the `_client()` helper sets `c._client = MagicMock()`; `MessageFlag` is imported there from Plan 4):

```python
def test_add_flags_by_uid_no_search():
    c = _client()
    c.add_flags_by_uid("INBOX", 5, [MessageFlag.SEEN])
    c._client.add_flags.assert_called_once_with([5], ["\\Seen"])
    c._client.search.assert_not_called()  # UID path, no Message-ID search


def test_remove_flags_by_uid():
    c = _client()
    c.remove_flags_by_uid("INBOX", 5, [MessageFlag.FLAGGED])
    c._client.remove_flags.assert_called_once_with([5], ["\\Flagged"])


def test_set_flags_by_uid():
    c = _client()
    c.set_flags_by_uid("INBOX", 5, [MessageFlag.SEEN])
    c._client.set_flags.assert_called_once_with([5], ["\\Seen"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fetch_uids.py -k by_uid -v`
Expected: FAIL (`AttributeError`: no `add_flags_by_uid`).

- [ ] **Step 3: Implement**

In `src/clerk/imap_client.py`, add these three methods to `ImapClient` (place them just after the existing `remove_flags` method):

```python
    def add_flags_by_uid(
        self, folder: str, uid: int, flags: Sequence[MessageFlag]
    ) -> None:
        """Add flags to a message by UID (no Message-ID search)."""
        self.client.select_folder(folder)
        self.client.add_flags([uid], model_flags_to_imap(flags))

    def remove_flags_by_uid(
        self, folder: str, uid: int, flags: Sequence[MessageFlag]
    ) -> None:
        """Remove flags from a message by UID (no Message-ID search)."""
        self.client.select_folder(folder)
        self.client.remove_flags([uid], model_flags_to_imap(flags))

    def set_flags_by_uid(
        self, folder: str, uid: int, flags: Sequence[MessageFlag]
    ) -> None:
        """Set flags on a message by UID (no Message-ID search)."""
        self.client.select_folder(folder)
        self.client.set_flags([uid], model_flags_to_imap(flags))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fetch_uids.py -k by_uid -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/imap_client.py tests/test_fetch_uids.py
git commit -m "feat(imap): flag mutations by UID (no Message-ID search)"
```

---

### Task 2: IMAP move by UID (atomic) + archive; fix legacy move

**Files:**
- Modify: `src/clerk/imap_client.py`
- Test: `tests/test_fetch_uids.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_fetch_uids.py`:

```python
def test_move_message_by_uid_uses_move_no_search():
    c = _client()
    c.move_message_by_uid("INBOX", 5, "Archive")
    c._client.move.assert_called_once_with([5], "Archive")
    c._client.search.assert_not_called()
    c._client.expunge.assert_not_called()  # client.move handles it atomically


def test_find_archive_folder_prefers_archive():
    c = _client()
    c._client.list_folders.return_value = [
        ((), b"/", "INBOX"),
        ((), b"/", "Archive"),
    ]
    assert c.find_archive_folder() == "Archive"


def test_archive_message_by_uid_moves_to_archive():
    c = _client()
    c._client.list_folders.return_value = [
        ((), b"/", "INBOX"),
        ((), b"/", "[Gmail]/All Mail"),
    ]
    c.archive_message_by_uid("INBOX", 5)
    c._client.move.assert_called_once_with([5], "[Gmail]/All Mail")


def test_legacy_move_message_uses_move_not_plain_expunge():
    c = _client()
    c._client.search.return_value = [9]
    c.move_message("<m9@x>", "INBOX", "Archive")
    c._client.move.assert_called_once_with([9], "Archive")
    c._client.expunge.assert_not_called()  # no more plain expunge
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fetch_uids.py -k "by_uid or archive or legacy_move" -v`
Expected: FAIL (`AttributeError`: no `move_message_by_uid`; legacy `move_message` still calls `expunge`).

- [ ] **Step 3: Implement**

In `src/clerk/imap_client.py`, add these methods to `ImapClient` (place them just after the legacy `move_message`):

```python
    def move_message_by_uid(self, from_folder: str, uid: int, to_folder: str) -> None:
        """Move a message by UID. Uses UID MOVE where the server supports it,
        otherwise imapclient's properly-scoped copy+delete+expunge fallback."""
        self.client.select_folder(from_folder)
        self.client.move([uid], to_folder)

    def find_archive_folder(self) -> str:
        """Resolve the archive folder name across providers (Gmail uses All Mail)."""
        names = [f.name for f in self.list_folders()]
        for name in ["Archive", "[Gmail]/All Mail", "All Mail", "Archives"]:
            if name in names:
                return name
        raise ValueError("Could not find archive folder")

    def archive_message_by_uid(self, from_folder: str, uid: int) -> None:
        """Archive a message by UID (move to the resolved archive folder)."""
        self.move_message_by_uid(from_folder, uid, self.find_archive_folder())
```

Also switch the LEGACY `move_message` (the Message-ID-search fallback) off the non-atomic copy+`\Deleted`+`expunge()` onto `client.move`. Replace its body after the `uid = results[0]` line:

```python
        uid = results[0]

        # Copy to destination
        self.client.copy([uid], to_folder)

        # Mark as deleted in source
        self.client.add_flags([uid], ["\\Deleted"])
        self.client.expunge()
```

with:

```python
        uid = results[0]
        self.client.move([uid], to_folder)
```

(`archive_message`, the legacy folder-discovery+move path, stays as-is; it now benefits from the fixed `move_message`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fetch_uids.py -k "by_uid or archive or legacy_move" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clerk/imap_client.py tests/test_fetch_uids.py
git commit -m "feat(imap): atomic move_message_by_uid + archive; legacy move via client.move"
```

---

### Task 3: `api.set_flag` uses the UID path when cached

**Files:**
- Modify: `src/clerk/api.py` (`set_flag`)
- Test: `tests/test_api.py` (update `TestSetFlag` and `TestMessageActions.test_mark_read`)

- [ ] **Step 1: Update the existing tests to expect the UID path**

In `tests/test_api.py`, the cached-message flag tests must now assert the `*_by_uid` calls (the messages they store are cached with a uid):

- In `TestMessageActions.test_mark_read`: change `mock_client.add_flags.assert_called_once()` to `mock_client.add_flags_by_uid.assert_called_once()`.
- In `TestSetFlag.test_mark_read_calls_set_flag`: change `args, _ = mock_client.add_flags.call_args` to `args, _ = mock_client.add_flags_by_uid.call_args` (the SEEN assertion on `args[2]` is unchanged: `add_flags_by_uid(folder, uid, flags)` puts flags at index 2).
- In `TestSetFlag.test_unflag_removes_flagged`: change `args, _ = mock_client.remove_flags.call_args` to `args, _ = mock_client.remove_flags_by_uid.call_args` (FLAGGED still at `args[2]`).
- `TestSetFlag.test_cache_write_failure_does_not_raise` needs no assertion change (it only checks no exception), and works because the cached `sample_message` has `uid=1`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_api.py -k "TestSetFlag or test_mark_read" -v`
Expected: FAIL (current `set_flag` calls `add_flags`/`remove_flags`, so the new `*_by_uid` mocks were never called).

- [ ] **Step 3: Implement**

In `src/clerk/api.py`, replace the `with get_imap_client(...)` block in `set_flag` (currently selects folder and calls `add_flags`/`remove_flags` by message_id) with a UID-first version:

```python
        msg = self.cache.get_message(message_id)
        folder = msg.folder if msg else "INBOX"

        with get_imap_client(account_name) as client:
            if msg is not None and msg.uid is not None:
                if on:
                    client.add_flags_by_uid(folder, msg.uid, [flag])
                else:
                    client.remove_flags_by_uid(folder, msg.uid, [flag])
            else:
                # Not cached: fall back to a Message-ID search.
                if on:
                    client.add_flags(folder, message_id, [flag])
                else:
                    client.remove_flags(folder, message_id, [flag])
```

(The `msg = self.cache.get_message(message_id)` line already exists just above; keep a single lookup. The cache-update block after the `with` is unchanged.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_api.py -k "TestSetFlag or test_mark_read" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clerk/api.py tests/test_api.py
git commit -m "feat(api): set_flag mutates by UID when the message is cached"
```

---

### Task 4: `api.move_message` and `archive_message` use the UID path when cached

**Files:**
- Modify: `src/clerk/api.py` (`move_message`, `archive_message`)
- Test: `tests/test_api.py` (update `TestMessageActions.test_archive_message`; add move-by-uid test)

- [ ] **Step 1: Write/Update the tests**

In `tests/test_api.py`, update `TestMessageActions.test_archive_message`: change `mock_client.archive_message.assert_called_once()` to `mock_client.archive_message_by_uid.assert_called_once()` (the stored `sample_message` is cached with uid=1).

Add a new test to `TestMessageActions`:

```python
    def test_move_uses_uid_when_cached(self, api, cache, sample_message, monkeypatch):
        cache.store_message(sample_message)
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr("clerk.api.get_imap_client", lambda _: mock_client)

        api.move_message("<msg123@example.com>", "Archive")

        mock_client.move_message_by_uid.assert_called_once_with("INBOX", 1, "Archive")
        mock_client.move_message.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_api.py -k "test_archive_message or test_move_uses_uid" -v`
Expected: FAIL (current code calls `move_message`/`archive_message` by message_id).

- [ ] **Step 3: Implement**

In `src/clerk/api.py`, replace the `with get_imap_client(...)` block in `move_message` (currently `client.move_message(message_id, from_folder, to_folder)`) with:

```python
        msg = self.cache.get_message(message_id)
        with get_imap_client(account_name) as client:
            if msg is not None and msg.uid is not None:
                client.move_message_by_uid(msg.folder, msg.uid, to_folder)
            else:
                client.move_message(message_id, from_folder, to_folder)
```

And replace the `with get_imap_client(...)` block in `archive_message` (currently `client.archive_message(message_id)`) with:

```python
        msg = self.cache.get_message(message_id)
        with get_imap_client(account_name) as client:
            if msg is not None and msg.uid is not None:
                client.archive_message_by_uid(msg.folder, msg.uid)
            else:
                client.archive_message(message_id)
```

(Keep the existing cache-update `try/except` blocks after each `with`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_api.py -k "test_archive_message or test_move_uses_uid" -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite, lint, and types**

Run: `pytest -q` (expect all pass)
Run: `ruff check src tests` (expect clean)
Run: `mypy src` (expect exactly 3 errors, all in `mcp_server.py`; confirm no new ones)

- [ ] **Step 6: Commit**

```bash
git add src/clerk/api.py tests/test_api.py
git commit -m "feat(api): move/archive by UID when cached"
```

---

## Self-Review

- **Spec coverage (Plan 6, UID-mutations slice):** flag/move/archive keyed on `(folder, uid)` instead of a per-call `SEARCH HEADER Message-ID` (Tasks 1-4); atomic move via `client.move` (Task 2; `test_move_message_by_uid_uses_move_no_search`, `test_legacy_move_message_uses_move_not_plain_expunge`); legacy search methods retained as the uncached fallback.
- **Deferred (next plans):** doc honesty (README/CLAUDE.md, priorities advisory); test hardening (dead integration suite, `_parse_message` fixtures, blocked-recipient/FROM-mismatch send-safety-layer tests, OAuth refresh, CI floor). Post-round: CONDSTORE reconciliation; attachments.
- **Placeholder scan:** none; every step has concrete code and commands.
- **Type consistency:** `add_flags_by_uid`/`remove_flags_by_uid`/`set_flags_by_uid(folder, uid, flags)`, `move_message_by_uid(from_folder, uid, to_folder)`, `archive_message_by_uid(from_folder, uid)`, `find_archive_folder() -> str` (Tasks 1-2) are exactly what `api` calls in Tasks 3-4. `model_flags_to_imap` and `Sequence` are already imported in imap_client.py.
- **Fallback invariant:** the UID path is taken only when `msg is not None and msg.uid is not None` (cached, the normal post-sync case); otherwise the legacy Message-ID-search methods run, so behavior for an uncached message is preserved. The legacy `move_message` no longer uses plain `expunge()`, so neither path can purge unrelated `\Deleted` mail.
- **Known minor (documented):** `move_message`'s `from_folder` parameter is now only used on the uncached fallback path (the UID path uses the cached `msg.folder`, which is more accurate); this is intentional and more correct. `archive_message`'s legacy folder-discovery path remains for uncached messages.

---

## Roadmap reminder (remaining Faithful Mirror work)

- Next: doc honesty (README tool list + remove the fictional `clerk attachment` command; CLAUDE.md remove stale `search.py`; mark `priorities` advisory in `clerk://config`).
- Then (final): test hardening (rewrite the dead integration suite against the current API; `_parse_message` fixtures; blocked-recipient + FROM-mismatch send-safety-layer tests; OAuth refresh; CI `--cov-fail-under` floor).
- Post-round follow-on: CONDSTORE/QRESYNC efficient reconciliation; outbound/inbound attachments (capability round).
