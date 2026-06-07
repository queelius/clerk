# Faithful Mirror: Field Fixes (real-data correctness) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two trust-breaking bugs found by dogfooding the live server against a real M365 inbox: (1) a lone-surrogate character in one message aborts the entire sync at SQLite store time (Plan 3's dead-letter only covers parse, not store), blocking all mail newer than the poison message; (2) `parse_address_list` splits a structured address header on commas, corrupting the ubiquitous Outlook `"Last, First" <addr>` recipients and losing their email addresses.

**Architecture:** Follow-on to the "Faithful Mirror" round, driven by real-inbox findings. Three changes: (1) sanitize lone surrogate code points at the store boundary (`cache.store_message` + `cache.update_body`, the two places that bind parsed text to SQLite, covering both the eager-sync and lazy-body paths); (2) wrap per-message `store_message` calls in `api.sync_folder` so a store failure dead-letters that UID and the sync continues instead of aborting (defense-in-depth: no single message can ever block sync); (3) replace `header.split(",")` in `parse_address_list` with `email.utils.getaddresses`, which is RFC-5322 aware (respects quoted commas).

**Tech Stack:** Python 3.11+ (`email.utils.getaddresses`), SQLite, Pydantic v2, pytest, unittest.mock.

---

## File Structure

- `src/clerk/cache.py` (modify): add `_safe_text` helper; sanitize in `store_message` and `update_body`.
- `src/clerk/api.py` (modify): wrap `store_message` in `sync_folder`'s dead-letter-retry and paging loops.
- `src/clerk/imap_client.py` (modify): rewrite `parse_address_list` using `email.utils.getaddresses`.
- Tests: `tests/test_cache.py`, `tests/test_api.py`, `tests/test_parse_message.py`.

---

### Task 1: Sanitize lone surrogates at the store boundary

**Files:**
- Modify: `src/clerk/cache.py` (`_safe_text`, `store_message`, `update_body`)
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cache.py` (reuse the `_msg` helper, which accepts `subject=`, `body_text=`, `body_html=`):

```python
def test_store_message_survives_lone_surrogate(tmp_path):
    # A lone surrogate (from malformed header/charset decoding) is not UTF-8
    # encodable; an unsanitized value crashes the SQLite bind and aborts sync.
    cache = Cache(db_path=tmp_path / "c.db")
    msg = _msg(
        50, "<surr@x>",
        subject="bad \ud800 subject",
        body_text="body \udfff text",
    )
    cache.store_message(msg)  # must not raise
    got = cache.get_message("<surr@x>")
    assert got is not None
    assert "\ud800" not in (got.subject or "")
    assert "\udfff" not in (got.body_text or "")
    assert "subject" in got.subject  # surrounding text preserved


def test_update_body_survives_lone_surrogate(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    cache.store_message(_msg(51, "<surr2@x>", body_text="placeholder"))
    cache.update_body("<surr2@x>", "recovered \ud83d body", None)  # lone high surrogate
    got = cache.get_message("<surr2@x>")
    assert got is not None
    assert "\ud83d" not in (got.body_text or "")
    assert "recovered" in got.body_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache.py -k "lone_surrogate" -v`
Expected: FAIL with `UnicodeEncodeError: 'utf-8' codec can't encode character ... surrogates not allowed` (this reproduces the production crash).

- [ ] **Step 3: Implement**

In `src/clerk/cache.py`, add a module-level helper near the top (after the imports / `SCHEMA_VERSION`):

```python
def _safe_text(value: str | None) -> str | None:
    """Make a string safe to bind to SQLite (UTF-8 encodable).

    Some malformed email headers/bodies decode to strings containing lone
    surrogate code points, which cannot be UTF-8 encoded. SQLite would raise
    UnicodeEncodeError and abort the whole sync. Replace those code points so a
    single bad message can never block storage.
    """
    if value is None:
        return None
    return value.encode("utf-8", "replace").decode("utf-8")
```

In `store_message`, insert a sanitize block immediately after the `if msg.uid is None: raise ...` guard and BEFORE the `root_message_id = compute_root_id(...)` line (so the derived `root_message_id`/`thread_subject` are computed from cleaned values):

```python
        # Drop lone surrogate code points that malformed mail can carry; an
        # unsanitized value would crash the SQLite bind and abort the sync.
        msg.message_id = _safe_text(msg.message_id)
        msg.subject = _safe_text(msg.subject)
        msg.body_text = _safe_text(msg.body_text)
        msg.body_html = _safe_text(msg.body_html)
        msg.in_reply_to = _safe_text(msg.in_reply_to)
        msg.from_.addr = _safe_text(msg.from_.addr)
        msg.from_.name = _safe_text(msg.from_.name)
        msg.references = [_safe_text(r) for r in msg.references]
        for _addr in (*msg.to, *msg.cc, *msg.reply_to):
            _addr.addr = _safe_text(_addr.addr)
            _addr.name = _safe_text(_addr.name)
```

(Note: `_safe_text` returns the input unchanged for normal text, so this is a no-op for clean messages. `msg.message_id`/`from_.addr`/`from_.name` are typed non-optional `str`, so `_safe_text` returns `str` for them; mypy is satisfied because `_safe_text(str) -> str | None` and the fields accept `str`. If mypy complains about assigning `str | None` to a non-optional field, wrap those three as `_safe_text(x) or ""`.)

In `update_body`, sanitize the body params. Change the `conn.execute(...)` bind tuple from:

```python
                (body_text, body_html, datetime.now(UTC).isoformat(), message_id),
```

to:

```python
                (_safe_text(body_text), _safe_text(body_html), datetime.now(UTC).isoformat(), message_id),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cache.py -k "lone_surrogate" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clerk/cache.py tests/test_cache.py
git commit -m "fix(cache): sanitize lone surrogates at store so one bad message cannot abort sync"
```

---

### Task 2: Dead-letter store failures in `sync_folder`

**Files:**
- Modify: `src/clerk/api.py` (`sync_folder`)
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing test**

Add to the existing `TestSyncFolder` class in `tests/test_api.py` (it has the `_client` and `_msg` helpers; `_client(monkeypatch, new_uids, fetch_result)` mocks `search_uids`/`fetch_uids`):

```python
    def test_store_failure_is_deadlettered_not_fatal(self, api, cache, monkeypatch):
        # One message's store raises; the sync must dead-letter it and keep going,
        # not abort the whole sync (the production surrogate-crash scenario).
        good1, bad, good2 = self._msg(10), self._msg(11), self._msg(12)
        self._client(monkeypatch, [10, 11, 12], ([good1, bad, good2], []))

        real_store = cache.store_message

        def flaky_store(msg):
            if msg.uid == 11:
                raise RuntimeError("surrogates not allowed")
            return real_store(msg)

        monkeypatch.setattr(cache, "store_message", flaky_store)

        result = api.sync_folder(account="test", folder="INBOX")

        assert result["synced"] == 2  # the two good messages stored
        assert 11 in cache.get_deadletter_uids("test", "INBOX")  # bad one dead-lettered
        assert cache.get_sync_state("test", "INBOX")["last_uid"] == 12  # watermark advanced
        assert cache.get_message("<m10@x>") is not None
        assert cache.get_message("<m12@x>") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py -k test_store_failure_is_deadlettered -v`
Expected: FAIL (the `RuntimeError` propagates out of `sync_folder`, so the call raises instead of returning, and uid 11 is not dead-lettered).

- [ ] **Step 3: Implement**

In `src/clerk/api.py` `sync_folder`, wrap the two `store_message` calls.

In PHASE 1 (the dead-letter retry loop), change:

```python
                for msg in msgs:
                    if msg.uid is None:
                        continue
                    self._prepare_body_for_storage(msg)
                    self.cache.store_message(msg)
                    self.cache.clear_deadletter(account_name, folder, msg.uid)
                    stored.add(msg.uid)
                    synced += 1
```

to:

```python
                for msg in msgs:
                    if msg.uid is None:
                        continue
                    self._prepare_body_for_storage(msg)
                    try:
                        self.cache.store_message(msg)
                    except Exception as e:
                        print(
                            f"Warning: store failed for uid {msg.uid}: {e}",
                            file=sys.stderr,
                        )
                        continue  # not added to `stored`; re-recorded as dead-letter below
                    self.cache.clear_deadletter(account_name, folder, msg.uid)
                    stored.add(msg.uid)
                    synced += 1
```

In PHASE 2 (the paging loop), change:

```python
                for msg in msgs:
                    self._prepare_body_for_storage(msg)
                    self.cache.store_message(msg)
                    synced += 1
                for uid in failed:
                    self.cache.record_deadletter(account_name, folder, uid)
```

to:

```python
                for msg in msgs:
                    self._prepare_body_for_storage(msg)
                    try:
                        self.cache.store_message(msg)
                        synced += 1
                    except Exception as e:
                        print(
                            f"Warning: store failed for uid {msg.uid}: {e}",
                            file=sys.stderr,
                        )
                        if msg.uid is not None:
                            self.cache.record_deadletter(account_name, folder, msg.uid)
                for uid in failed:
                    self.cache.record_deadletter(account_name, folder, uid)
```

(`sys` is already imported in `api.py`. The per-chunk `set_sync_state` after the loop is unchanged, so the watermark still advances past a dead-lettered message; combined with Task 1, the surrogate message is actually stored cleanly and never reaches this path, but any other store failure now dead-letters instead of aborting.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api.py -k test_store_failure_is_deadlettered -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clerk/api.py tests/test_api.py
git commit -m "fix(sync): dead-letter store failures so one bad message cannot abort the sync"
```

---

### Task 3: Parse address headers with `email.utils.getaddresses`

**Files:**
- Modify: `src/clerk/imap_client.py` (`parse_address_list`)
- Test: `tests/test_parse_message.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_parse_message.py` (it already imports `parse_address_list`):

```python
def test_parse_address_list_quoted_last_first_names():
    # Outlook "Last, First" form: the comma is INSIDE a quoted display name and
    # must not split the address, and the real email must be preserved.
    header = '"Towell, Alexander" <atowell@siue.edu>, "Fujinoki, Hiroshi" <hfujino@siue.edu>'
    addrs = parse_address_list(header)
    assert [(a.name, a.addr) for a in addrs] == [
        ("Towell, Alexander", "atowell@siue.edu"),
        ("Fujinoki, Hiroshi", "hfujino@siue.edu"),
    ]


def test_parse_address_list_plain_and_angle():
    assert [(a.name, a.addr) for a in parse_address_list("alice@example.com")] == [
        ("", "alice@example.com")
    ]
    assert [(a.name, a.addr) for a in parse_address_list("Alice <alice@example.com>")] == [
        ("Alice", "alice@example.com")
    ]


def test_parse_address_list_multiple_plain():
    addrs = parse_address_list("a@x.com, b@y.com")
    assert [a.addr for a in addrs] == ["a@x.com", "b@y.com"]
```

(The existing `test_parse_address_list_bare_display_name` asserts a bare name like `"Just A Name"` is preserved as `name`; it must keep passing.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_parse_message.py -k "quoted_last_first or plain_and_angle or multiple_plain" -v`
Expected: FAIL on `quoted_last_first` (current `split(",")` splits the quoted name and loses the address).

- [ ] **Step 3: Implement**

In `src/clerk/imap_client.py`, replace the entire `parse_address_list` function with:

```python
def parse_address_list(header: str | None) -> list[Address]:
    """Parse an address header into Address objects.

    Uses email.utils.getaddresses, which is RFC 5322 aware: it does NOT split on
    commas inside quoted display names (the common Outlook "Last, First" form),
    so the display name and the real email address are both preserved.
    """
    if not header:
        return []
    addresses: list[Address] = []
    for raw_name, raw_addr in email.utils.getaddresses([header]):
        name = decode_header_value(raw_name)
        addr = raw_addr.strip()
        if addr and "@" in addr:
            addresses.append(Address(addr=addr, name=name))
        elif name or addr:
            # Bare display name with no real email (a malformed M365 quirk):
            # keep whatever text we have as the name.
            addresses.append(Address(addr="", name=name or addr))
    return addresses
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_parse_message.py -v`
Expected: PASS (the new tests AND the existing `test_parse_address_list_bare_display_name`). Also run `pytest tests/test_parse_address.py -v` to confirm no regression in the older address tests; if `getaddresses` changes a previously-asserted edge behavior, update that assertion to the (more correct) new behavior, keeping the assertion's intent.

- [ ] **Step 5: Run the full suite, lint, types**

Run: `pytest -q` (expect all pass)
Run: `ruff check src tests` (expect clean)
Run: `mypy src` (expect exactly 3 errors, all in `mcp_server.py`; confirm no new ones; if Task 1's `_safe_text` assignments trip mypy on the non-optional `message_id`/`from_.addr`/`from_.name`, wrap those three as `_safe_text(...) or ""`)

- [ ] **Step 6: Commit**

```bash
git add src/clerk/imap_client.py tests/test_parse_message.py
git commit -m "fix(imap): parse address headers with getaddresses (preserve Last, First names + emails)"
```

---

## Self-Review

- **Spec coverage:** #6 surrogate sync-crash fixed at the store boundary (Task 1, covers eager `store_message` + lazy `update_body`) and made non-fatal in general (Task 2, store failures dead-letter instead of aborting); #7 "Last, First" address corruption fixed via `getaddresses` (Task 3).
- **Reproduction-first:** each task's first test reproduces the actual production failure (lone-surrogate store crash; a store raising mid-sync; the quoted-comma address split) before the fix.
- **Placeholder scan:** none; all code is concrete. The one conditional (`_safe_text(...) or ""` for non-optional fields) is an explicit, documented mypy contingency.
- **Type consistency:** `_safe_text(str | None) -> str | None` is used in `store_message`/`update_body`; `parse_address_list(str | None) -> list[Address]` keeps its signature; `getaddresses` returns `list[tuple[str, str]]`.
- **No-op-on-clean invariant:** `_safe_text` returns normal text unchanged (UTF-8 round-trip), so clean messages and all existing tests are unaffected; the in-place sanitize in `store_message` only alters genuinely-malformed input.
- **Defense in depth:** with Task 1 the surrogate message is stored cleanly (not lost); Task 2 ensures any *other* unforeseen store failure dead-letters rather than blocking the whole sync. The per-chunk watermark still advances, so recovery is automatic on the next sync.

---

## Roadmap (remaining dogfooding findings, later focused plans)

- **Surface:** draft management tools (`clerk_drafts` list / `clerk_draft_delete` / `clerk_draft_update`, API methods already exist) and `clerk_fetch_attachment` (wire the existing `ImapClient.fetch_attachment` to write bytes to a path the agent can Read).
- **UX/robustness:** infer account from the cached message in `clerk_flag`/`clerk_move`; make `clerk_auth`/`clerk_status` test a real login (no false "valid"); show the real From address in the send preview; normalize IMAP-vs-SMTP auth errors with actionable hints (e.g. M365 Authenticated-SMTP disabled).
- **Docs:** update the stale `clerk` skill to the real tool surface (the doc the agent actually loads, so it matters most).
- **Investigate:** siue SMTP `Authentication failed` (tenant Authenticated-SMTP policy vs XOAUTH2 scope); Gmail token rejected-server-side recovery.
