# Faithful Mirror: Send-Safety Reservation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the audit-log/rate-limit decoupling: reserve a `send_log` row (status `pending`) BEFORE sending so a send always counts against the rate limit, then finalize it to `sent` or `failed`; the rate limiter counts `pending` + `sent`, so a failed audit-log write can no longer silently raise the effective send ceiling, and a failed send no longer counts.

**Architecture:** First slice of Plan 6 of the "Faithful Mirror" round (spec Workstream 6 / Plan 1's audit finding). Today `api.send_draft_async` logs the send AFTER SMTP succeeds, best-effort; if that `log_send` fails, the send is not counted and the ceiling rises. The fix: `reserve_send` inserts a `pending` row before SMTP (so it counts immediately), `finalize_send` flips it to `sent` (with the message id) on success or `failed` on failure, and `count_sends_since` counts only `pending` + `sent`. The `send_log.status` column already exists (added in Plan 1). The existing `log_send` (inserts a `sent` row directly) is kept for tests/backfill. This plan does NOT change the user-facing flag/move mutations (UID-keying is the next plan) or any docs/tests beyond send-safety.

**Tech Stack:** Python 3.11+, SQLite, asyncio, pytest, unittest.mock.

---

## File Structure

- `src/clerk/cache.py` (modify): add `reserve_send` + `finalize_send`; filter `count_sends_since` by status.
- `src/clerk/api.py` (modify): `send_draft_async` reserves before SMTP and finalizes after.
- Tests: `tests/test_cache.py`, `tests/test_api.py`.

---

### Task 1: Reservation helpers in the cache; count only pending + sent

**Files:**
- Modify: `src/clerk/cache.py` (`count_sends_since`; add `reserve_send`, `finalize_send`)
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cache.py`:

```python
def test_reserve_send_counts_toward_limit(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    from datetime import timedelta

    hour_ago = datetime.now(UTC) - timedelta(hours=1)
    assert cache.count_sends_since("acct", hour_ago) == 0
    cache.reserve_send("acct", [Address(addr="x@y.com")], [], [], "subj")
    # a pending reservation counts (so a send is counted even before finalize)
    assert cache.count_sends_since("acct", hour_ago) == 1


def test_finalize_failed_not_counted(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    from datetime import timedelta

    hour_ago = datetime.now(UTC) - timedelta(hours=1)
    send_id = cache.reserve_send("acct", [Address(addr="x@y.com")], [], [], "subj")
    cache.finalize_send(send_id, "failed", None)
    # a failed send does not count against the limit
    assert cache.count_sends_since("acct", hour_ago) == 0


def test_finalize_sent_counts_and_records_message_id(tmp_path):
    cache = Cache(db_path=tmp_path / "c.db")
    from datetime import timedelta

    hour_ago = datetime.now(UTC) - timedelta(hours=1)
    send_id = cache.reserve_send("acct", [Address(addr="x@y.com")], [], [], "subj")
    cache.finalize_send(send_id, "sent", "<m@x>")
    assert cache.count_sends_since("acct", hour_ago) == 1
    with cache._connect() as conn:
        row = conn.execute(
            "SELECT status, message_id FROM send_log WHERE id = ?", (send_id,)
        ).fetchone()
    assert row["status"] == "sent"
    assert row["message_id"] == "<m@x>"
```

(`Address`, `datetime`, `UTC` are imported at the top of `tests/test_cache.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache.py -k "reserve_send or finalize" -v`
Expected: FAIL (`AttributeError`: no `reserve_send`/`finalize_send`).

- [ ] **Step 3: Implement**

In `src/clerk/cache.py`, change `count_sends_since` to count only pending + sent rows. Replace its SELECT:

```python
                "SELECT COUNT(*) FROM send_log WHERE account = ? AND timestamp >= ?",
                (account, since.isoformat()),
```

with:

```python
                "SELECT COUNT(*) FROM send_log "
                "WHERE account = ? AND timestamp >= ? AND status IN ('pending', 'sent')",
                (account, since.isoformat()),
```

Add these two methods to the `Cache` class, directly after `count_sends_since`:

```python
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
            return int(cur.lastrowid)

    def finalize_send(
        self, send_id: int, status: str, message_id: str | None
    ) -> None:
        """Mark a reserved send row as 'sent' (with its message id) or 'failed'."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE send_log SET status = ?, message_id = ? WHERE id = ?",
                (status, message_id, send_id),
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cache.py -k "reserve_send or finalize" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the cache suite + the existing rate-limit tests**

Run: `pytest tests/test_cache.py tests/test_api.py -k "send or rate or reserve or finalize or count" -q`
Expected: PASS. (The existing `TestSendPolicyPersistent` tests use `log_send`, which inserts a `sent` row by default, so they still count correctly under the new status filter.)

- [ ] **Step 6: Commit**

```bash
git add src/clerk/cache.py tests/test_cache.py
git commit -m "feat(cache): pre-send reservation (pending/sent/failed) for the rate limiter"
```

---

### Task 2: `send_draft_async` reserves before SMTP, finalizes after

**Files:**
- Modify: `src/clerk/api.py` (`send_draft_async`)
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Add a new test class to `tests/test_api.py` (the `api`, `cache` fixtures and `MagicMock`, `datetime`, `UTC` are at module scope):

```python
class TestSendReservation:
    """Sends are reserved before SMTP so the rate limit cannot be bypassed by a
    finalize/audit-log failure, and failed sends do not count."""

    def _fake_smtp(self, monkeypatch, success):
        from clerk.models import SendResult

        class FakeSmtp:
            def __init__(self, name, config):
                pass

            async def send_async(self, draft):
                return SendResult(
                    success=success,
                    message_id="<sent@x>" if success else None,
                    error=None if success else "smtp boom",
                )

        monkeypatch.setattr("clerk.api.SmtpClient", FakeSmtp)

    def _count(self, cache):
        from datetime import timedelta

        return cache.count_sends_since("test", datetime.now(UTC) - timedelta(hours=1))

    def test_successful_send_counts_as_sent(self, api, cache, monkeypatch):
        self._fake_smtp(monkeypatch, success=True)
        d = api.create_draft(to=["x@example.com"], subject="s", body="b")
        result = api.send_draft(d.draft_id)
        assert result.success
        assert self._count(cache) == 1

    def test_failed_send_not_counted(self, api, cache, monkeypatch):
        self._fake_smtp(monkeypatch, success=False)
        d = api.create_draft(to=["x@example.com"], subject="s", body="b")
        result = api.send_draft(d.draft_id)
        assert not result.success
        assert self._count(cache) == 0

    def test_send_counted_even_if_finalize_fails(self, api, cache, monkeypatch):
        self._fake_smtp(monkeypatch, success=True)
        # finalize raises after a successful SMTP send; the pending reservation
        # must still count (the send happened) and the call must not blow up.
        monkeypatch.setattr(
            cache, "finalize_send", MagicMock(side_effect=RuntimeError("disk full"))
        )
        d = api.create_draft(to=["x@example.com"], subject="s", body="b")
        result = api.send_draft(d.draft_id)
        assert result.success
        assert self._count(cache) == 1  # the pending reservation counts
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_api.py -k TestSendReservation -v`
Expected: FAIL (current code uses best-effort `log_send` after send; a finalize/log failure currently means the send is not counted).

- [ ] **Step 3: Implement**

In `src/clerk/api.py`, in `send_draft_async`, replace the block that runs from the SMTP send through the return (currently constructs `SmtpClient`, awaits `send_async`, and on success calls `log_send` then `drafts.delete`). The current block is:

```python
        client = SmtpClient(name, account_config)
        result = await client.send_async(draft)

        if result.success:
            # Audit log is best-effort: a disk-full error here should not
            # swallow a successful send. Log to stderr and continue.
            try:
                self.cache.log_send(
                    account=name,
                    to=draft.to,
                    cc=draft.cc,
                    bcc=draft.bcc,
                    subject=draft.subject,
                    message_id=result.message_id,
                )
            except Exception as e:
                print(
                    f"Warning: audit log write failed after send: {e}",
                    file=sys.stderr,
                )

            try:
                self.drafts.delete(draft_id)
            except Exception as e:
                print(
                    f"Warning: draft delete failed after send: {e}",
                    file=sys.stderr,
                )

        return result
```

Replace it with:

```python
        # Reserve a send_log slot BEFORE sending so the send counts against the
        # rate limit immediately. A finalize failure afterwards cannot un-count
        # it (the pending row already counts); a failed send is finalized to
        # 'failed' and so does not count.
        send_id = self.cache.reserve_send(
            account=name,
            to=draft.to,
            cc=draft.cc,
            bcc=draft.bcc,
            subject=draft.subject,
        )

        client = SmtpClient(name, account_config)
        try:
            result = await client.send_async(draft)
        except Exception:
            self.cache.finalize_send(send_id, "failed", None)
            raise

        if result.success:
            try:
                self.cache.finalize_send(send_id, "sent", result.message_id)
            except Exception as e:
                print(
                    f"Warning: send_log finalize failed after send: {e}",
                    file=sys.stderr,
                )

            try:
                self.drafts.delete(draft_id)
            except Exception as e:
                print(
                    f"Warning: draft delete failed after send: {e}",
                    file=sys.stderr,
                )
        else:
            self.cache.finalize_send(send_id, "failed", None)

        return result
```

(The `check_send_allowed` call earlier in `send_draft_async` is unchanged: it runs before the reservation, so the new pending row does not count against its own check.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_api.py -k TestSendReservation -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite, lint, and types**

Run: `pytest -q` (expect all pass)
Run: `ruff check src tests` (expect clean)
Run: `mypy src` (expect exactly 3 errors, all in `mcp_server.py`; confirm no new ones)

- [ ] **Step 6: Commit**

```bash
git add src/clerk/api.py tests/test_api.py
git commit -m "feat(send): reserve before SMTP, finalize after (rate-limit cannot be bypassed)"
```

---

## Self-Review

- **Spec coverage (Plan 6, send-safety slice):** pre-send reservation closes the audit-log/rate-limit decoupling (Tasks 1, 2; `test_send_counted_even_if_finalize_fails`); failed sends do not count (`test_failed_send_not_counted`, `test_finalize_failed_not_counted`); successful sends are recorded with their message id (`test_finalize_sent_counts_and_records_message_id`, `test_successful_send_counts_as_sent`).
- **Deferred (next plans):** UID-keyed user-facing flag/move mutations + atomic UID MOVE; doc honesty (README/CLAUDE.md, priorities advisory); test hardening (dead integration suite, `_parse_message` fixtures, blocked-recipient / FROM-mismatch send-safety-layer tests, OAuth refresh, CI floor).
- **Placeholder scan:** none; every step has concrete code and commands.
- **Type consistency:** `reserve_send(account, to, cc, bcc, subject) -> int` and `finalize_send(send_id, status, message_id) -> None` (Task 1) are exactly what `send_draft_async` calls (Task 2). `count_sends_since` keeps its signature; only its WHERE clause changes. `cur.lastrowid` is read inside the `with self._connect()` block (valid: the row is inserted before the context manager commits/closes).
- **Behavioral invariants:** (a) reservation happens AFTER `check_send_allowed`, so a send's own pending row never blocks itself; (b) a successful SMTP send with a failing finalize stays `pending` and therefore still counts (the bug this plan fixes); (c) a failed SMTP send (or an exception) finalizes to `failed` and does not count; (d) `log_send` is retained (inserts a `sent` row) so existing rate-limit tests and any backfill path keep working.
- **Known minor (documented):** a successful send whose finalize fails remains `pending` in the audit log (counts toward the limit, which is the safe direction). It is not re-attempted; a future maintenance pass could sweep stale `pending` rows, but for the paranoid-send thesis erring toward counting is correct.

---

## Roadmap reminder (remaining Faithful Mirror work)

- Next: UID-keyed user-facing mutations (flag/move keyed on (account, folder, uid), reusing Plan 4's UID-keyed cache helpers) + atomic UID MOVE / UID EXPUNGE.
- Then: doc honesty (README tool list + remove the fictional attachment command; CLAUDE.md remove stale search.py; mark `priorities` advisory in clerk://config).
- Then (final): test hardening (rewrite the dead integration suite against the current API; `_parse_message` fixtures; blocked-recipient + FROM-mismatch send-safety-layer tests; OAuth refresh; CI `--cov-fail-under` floor).
- Post-round follow-on: CONDSTORE/QRESYNC efficient reconciliation; outbound/inbound attachments (capability round).
