# Faithful Mirror: Test Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cover the riskiest currently-untested code (`_parse_message`, the blocked-recipient and FROM-mismatch send-safety layers, the Gmail OAuth refresh path), repair the dead integration suite so it targets the real API (and remove the attachment integration test for a feature that does not exist), and add a coverage floor so coverage cannot silently regress.

**Architecture:** Final slice of Plan 6 of the "Faithful Mirror" round. Tasks 1-3 are pure unit tests (no Docker) that run in CI and pin behavior the audit flagged as untested. Task 4 rewrites the integration suite (currently 100% broken: it calls `list_inbox`/`download_attachment`/`send_draft(skip_confirmation=...)`/`requires_confirmation`, none of which exist) against the current API; it stays Docker-gated (skips cleanly without Greenmail) and drops `test_attachments.py` because attachment fetch is unsupported. Task 5 adds a `--cov-fail-under` floor via coverage config.

**Tech Stack:** Python 3.11+, pytest, pytest-cov, unittest.mock, Greenmail (integration only).

---

## File Structure

- `tests/test_parse_message.py` (create): direct unit tests for `ImapClient._parse_message`.
- `tests/test_api.py` (modify): add blocked-recipient + FROM-mismatch send-safety tests.
- `tests/test_oauth.py` (modify): add the refresh-success and refresh-failure paths.
- `tests/integration/conftest.py` (modify): drop the stale `get_data_dir` monkeypatch.
- `tests/integration/test_inbox.py`, `tests/integration/test_send.py` (rewrite): against the current API.
- `tests/integration/test_attachments.py` (delete): attachments unsupported.
- `pyproject.toml` (modify): coverage config + floor.

---

### Task 1: Unit tests for `_parse_message`

**Files:**
- Create: `tests/test_parse_message.py`

`ImapClient._parse_message(uid, data, folder, has_body, fetch_time)` reads `data[b"ENVELOPE"]` (an object with `.date`, `.subject`, `.from_`, `.message_id`), `data[b"FLAGS"]`, and the raw MIME from `data[b"BODY[]"]` (when `has_body`) or `data[b"INTERNALDATE"]` for the date fallback. It returns a `Message`.

- [ ] **Step 1: Write the tests**

Create `tests/test_parse_message.py`:

```python
"""Direct unit tests for ImapClient._parse_message (the riskiest parsing code)."""
from datetime import UTC, datetime

from clerk.config import AccountConfig, ImapConfig, SmtpConfig
from clerk.imap_client import ImapClient, parse_address_list


def _client() -> ImapClient:
    cfg = AccountConfig(
        protocol="imap",
        imap=ImapConfig(host="h", username="u"),
        smtp=SmtpConfig(host="h", username="u"),
        **{"from": {"address": "u@example.com"}},
    )
    return ImapClient("acct", cfg)


class _Addr:
    def __init__(self, mailbox=None, host=None, name=None):
        self.mailbox = mailbox
        self.host = host
        self.name = name


class _Env:
    def __init__(self, subject=b"Subj", from_=None, message_id=b"<m@x>", date=None):
        self.subject = subject
        self.from_ = from_
        self.message_id = message_id
        self.date = date


_NOW = datetime(2026, 1, 2, tzinfo=UTC)


def _parse(raw, *, env=None, flags=(), has_body=True, internaldate=None):
    c = _client()
    data = {b"ENVELOPE": env or _Env(), b"FLAGS": flags}
    if internaldate is not None:
        data[b"INTERNALDATE"] = internaldate
    data[b"BODY[]" if has_body else b"BODY[HEADER]"] = raw
    return c._parse_message(uid=1, data=data, folder="INBOX", has_body=has_body, fetch_time=_NOW)


def test_parse_html_only_body():
    raw = b"From: a@b.com\r\nSubject: H\r\nContent-Type: text/html\r\n\r\n<p>hello <b>world</b></p>"
    msg = _parse(raw, env=_Env(date=datetime(2026, 1, 1)))
    assert msg is not None
    assert msg.body_text is None  # HTML-only; html_to_text conversion happens in api, not here
    assert "world" in (msg.body_html or "")


def test_parse_multipart_with_attachment():
    raw = (
        b"From: a@b.com\r\nSubject: M\r\n"
        b"Content-Type: multipart/mixed; boundary=BB\r\n\r\n"
        b"--BB\r\nContent-Type: text/plain\r\n\r\nbody text here\r\n"
        b"--BB\r\nContent-Type: application/pdf\r\n"
        b'Content-Disposition: attachment; filename="doc.pdf"\r\n\r\nPDFDATA\r\n'
        b"--BB--\r\n"
    )
    msg = _parse(raw, env=_Env(date=datetime(2026, 1, 1)))
    assert msg is not None
    assert "body text here" in (msg.body_text or "")
    assert [a.filename for a in msg.attachments] == ["doc.pdf"]
    assert msg.attachments[0].content_type == "application/pdf"


def test_parse_missing_date_falls_back_to_internaldate():
    raw = b"From: a@b.com\r\nSubject: D\r\n\r\nbody"
    fallback = datetime(2025, 12, 31, tzinfo=UTC)
    msg = _parse(raw, env=_Env(date=None), internaldate=fallback)
    assert msg is not None
    assert msg.date == fallback


def test_parse_decodes_non_utf8_charset():
    # iso-8859-1 encoded "cafe" with an accented e (0xe9)
    raw = (
        b"From: a@b.com\r\nSubject: C\r\n"
        b"Content-Type: text/plain; charset=iso-8859-1\r\n\r\ncaf\xe9"
    )
    msg = _parse(raw, env=_Env(date=datetime(2026, 1, 1)))
    assert msg is not None
    assert "café" in (msg.body_text or "")


def test_parse_from_envelope_address():
    env = _Env(from_=[_Addr(mailbox=b"alice", host=b"example.com", name=b"Alice")],
               date=datetime(2026, 1, 1))
    raw = b"From: alice@example.com\r\nSubject: F\r\n\r\nbody"
    msg = _parse(raw, env=env)
    assert msg is not None
    assert msg.from_.addr == "alice@example.com"
    assert msg.from_.name == "Alice"


def test_parse_address_list_bare_display_name():
    # A To header with a bare display name and no email address.
    addrs = parse_address_list("Just A Name")
    assert len(addrs) == 1
    assert addrs[0].addr == ""
    assert "Just A Name" in addrs[0].name
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_parse_message.py -v`
Expected: PASS (6 tests). If any reveals a real `_parse_message` bug, STOP and report it (do not paper over it); otherwise proceed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_parse_message.py
git commit -m "test(imap): unit-cover _parse_message (HTML-only, multipart, charset, dates)"
```

---

### Task 2: Send-safety layer tests (blocked recipients + FROM mismatch)

**Files:**
- Modify: `tests/test_api.py`

`check_send_allowed(draft, account_name)` rejects when a recipient (to/cc/bcc, case-insensitively) is in `config.send.blocked_recipients`, or when `draft.account != account_name`.

- [ ] **Step 1: Write the tests**

Add a new class to `tests/test_api.py` (the `api` fixture and `Address` are at module scope):

```python
class TestSendSafetyLayers:
    """check_send_allowed: blocked recipients (to/cc/bcc, case-insensitive) and FROM match."""

    def _draft(self, to=None, cc=None, bcc=None, account="test"):
        from clerk.models import Draft

        return Draft(
            draft_id="d1",
            account=account,
            to=to or [Address(addr="ok@example.com")],
            cc=cc or [],
            bcc=bcc or [],
            subject="s",
            body_text="b",
        )

    def test_blocked_recipient_in_to(self, api, monkeypatch):
        monkeypatch.setattr(api.config.send, "blocked_recipients", ["bad@example.com"])
        draft = self._draft(to=[Address(addr="bad@example.com")])
        allowed, error = api.check_send_allowed(draft, "test")
        assert allowed is False
        assert "blocked" in (error or "").lower()

    def test_blocked_recipient_case_insensitive(self, api, monkeypatch):
        monkeypatch.setattr(api.config.send, "blocked_recipients", ["BAD@EXAMPLE.COM"])
        draft = self._draft(to=[Address(addr="bad@example.com")])
        allowed, _ = api.check_send_allowed(draft, "test")
        assert allowed is False

    def test_blocked_recipient_in_cc_and_bcc(self, api, monkeypatch):
        monkeypatch.setattr(api.config.send, "blocked_recipients", ["bad@example.com"])
        cc_draft = self._draft(cc=[Address(addr="bad@example.com")])
        bcc_draft = self._draft(bcc=[Address(addr="bad@example.com")])
        assert api.check_send_allowed(cc_draft, "test")[0] is False
        assert api.check_send_allowed(bcc_draft, "test")[0] is False

    def test_account_mismatch_rejected(self, api):
        draft = self._draft(account="other")
        allowed, error = api.check_send_allowed(draft, "test")
        assert allowed is False
        assert "other" in (error or "")

    def test_clean_draft_allowed(self, api):
        allowed, error = api.check_send_allowed(self._draft(), "test")
        assert allowed is True
        assert error is None
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_api.py -k TestSendSafetyLayers -v`
Expected: PASS (5 tests).

- [ ] **Step 3: Commit**

```bash
git add tests/test_api.py
git commit -m "test(send): cover blocked-recipient and FROM-mismatch safety layers"
```

---

### Task 3: Gmail OAuth refresh path tests

**Files:**
- Modify: `tests/test_oauth.py`

`get_gmail_credentials`: on expired creds with a refresh token it calls `credentials.refresh(Request())` and re-saves; if refresh raises and no `client_id_file` is provided it raises `ValueError` (it must NOT spawn a browser flow).

- [ ] **Step 1: Write the tests**

Add to the `TestGetGmailCredentials` class in `tests/test_oauth.py` (it already imports `json`, `MagicMock`, `patch`, `pytest`):

```python
    @patch("clerk.oauth.save_oauth_token")
    @patch("clerk.oauth.get_oauth_token")
    def test_expired_credentials_are_refreshed(self, mock_get_token, mock_save):
        from clerk.oauth import get_gmail_credentials

        mock_get_token.return_value = json.dumps({
            "token": "t", "refresh_token": "r",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "c", "client_secret": "s",
            "scopes": ["https://mail.google.com/"],
        })
        with patch("clerk.oauth._load_credentials") as mock_load:
            creds = MagicMock()
            creds.valid = False
            creds.expired = True
            creds.refresh_token = "r"
            mock_load.return_value = creds

            result = get_gmail_credentials("test-account")

            creds.refresh.assert_called_once()
            assert result == creds
            mock_save.assert_called_once()  # refreshed creds re-saved

    @patch("clerk.oauth.run_oauth_flow")
    @patch("clerk.oauth.get_oauth_token")
    def test_refresh_failure_without_client_file_raises_not_browser(
        self, mock_get_token, mock_flow
    ):
        from clerk.oauth import get_gmail_credentials

        mock_get_token.return_value = json.dumps({
            "token": "t", "refresh_token": "r",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "c", "client_secret": "s",
            "scopes": ["https://mail.google.com/"],
        })
        with patch("clerk.oauth._load_credentials") as mock_load:
            creds = MagicMock()
            creds.valid = False
            creds.expired = True
            creds.refresh_token = "r"
            creds.refresh.side_effect = RuntimeError("token revoked")
            mock_load.return_value = creds

            with pytest.raises(ValueError, match="No valid credentials"):
                get_gmail_credentials("test-account")  # no client_id_file

            mock_flow.assert_not_called()  # must not spawn a browser flow
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_oauth.py -k "refreshed or refresh_failure" -v`
Expected: PASS (2 tests).

- [ ] **Step 3: Commit**

```bash
git add tests/test_oauth.py
git commit -m "test(oauth): cover Gmail refresh success and refresh-failure-no-browser"
```

---

### Task 4: Repair the integration suite against the current API

**Files:**
- Modify: `tests/integration/conftest.py`
- Rewrite: `tests/integration/test_inbox.py`, `tests/integration/test_send.py`
- Delete: `tests/integration/test_attachments.py`

- [ ] **Step 1: Fix the conftest**

In `tests/integration/conftest.py`, the `test_draft_manager` fixture still does `monkeypatch.setattr("clerk.drafts.get_data_dir", lambda: tmp_path)` but `drafts.py` no longer imports `get_data_dir` (Plan 5 routed drafts through the cache). Remove that line. The fixture becomes:

```python
@pytest.fixture
def test_draft_manager(tmp_path, test_cache, monkeypatch):
    """Create a test draft manager."""
    monkeypatch.setattr("clerk.drafts.get_cache", lambda: test_cache)
    return DraftManager()
```

- [ ] **Step 2: Rewrite `test_inbox.py`**

Replace the entire contents of `tests/integration/test_inbox.py` with:

```python
"""Integration: sync from Greenmail into the cache and read via SQL."""


class TestInboxSync:
    def test_sync_populates_cache(self, api_with_greenmail, greenmail_server, populated_mailbox):
        api = api_with_greenmail
        result = api.sync_folder(account="test", folder="INBOX")
        assert result["synced"] >= 4
        rows = api.cache.execute_readonly_sql(
            "SELECT subject FROM messages WHERE account = 'test'"
        )
        subjects = [r["subject"] for r in rows]
        assert any("Test Email 1" in s for s in subjects)

    def test_synced_body_is_full_text_searchable(
        self, api_with_greenmail, greenmail_server, populated_mailbox
    ):
        api = api_with_greenmail
        api.sync_folder(account="test", folder="INBOX")
        rows = api.cache.execute_readonly_sql(
            "SELECT m.subject FROM messages_fts f "
            "JOIN messages m ON m.rowid = f.rowid "
            "WHERE messages_fts MATCH 'content'"
        )
        assert len(rows) >= 1  # eager-body sync indexed the bodies
```

- [ ] **Step 3: Rewrite `test_send.py`**

Replace the entire contents of `tests/integration/test_send.py` with:

```python
"""Integration: send a draft through Greenmail's SMTP and audit it."""
from datetime import UTC, datetime, timedelta


class TestSend:
    def test_send_draft_succeeds(self, api_with_greenmail, greenmail_server):
        api = api_with_greenmail
        draft = api.create_draft(
            to=[greenmail_server["email"]],
            subject="Hello from clerk",
            body="Hi there.",
        )
        result = api.send_draft(draft.draft_id)
        assert result.success, result.error

    def test_sent_message_is_recorded_in_audit_log(self, api_with_greenmail, greenmail_server):
        api = api_with_greenmail
        draft = api.create_draft(
            to=[greenmail_server["email"]],
            subject="Audited",
            body="x",
        )
        api.send_draft(draft.draft_id)
        count = api.cache.count_sends_since(
            "test", datetime.now(UTC) - timedelta(hours=1)
        )
        assert count >= 1

    def test_draft_deleted_after_send(self, api_with_greenmail, greenmail_server):
        api = api_with_greenmail
        draft = api.create_draft(
            to=[greenmail_server["email"]], subject="Gone after send", body="x"
        )
        api.send_draft(draft.draft_id)
        assert api.get_draft(draft.draft_id) is None
```

- [ ] **Step 4: Delete the attachment integration test**

```bash
git rm tests/integration/test_attachments.py
```

Attachment fetch/download is not a supported feature, so a test for it is misleading. (If attachments are added in a future capability round, integration coverage comes with them.)

- [ ] **Step 5: Verify collection + clean skip without Docker**

Run: `pytest tests/integration/ -v`
Expected: all tests SKIP cleanly with the "Greenmail server not available" message (no Docker here), and there are NO collection errors or `AttributeError`s. Then run the FULL unit suite `pytest -q` and confirm it is unaffected (the integration tests still skip).

- [ ] **Step 6: Commit**

```bash
git add tests/integration/
git commit -m "test(integration): rewrite against the current API; drop attachment test"
```

---

### Task 5: Coverage floor

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Measure current coverage**

Run: `pytest --cov=clerk --cov-report=term-missing -q 2>&1 | tail -5`
Note the TOTAL coverage percentage (call it `P`).

- [ ] **Step 2: Add a coverage config with a floor a few points below P**

In `pyproject.toml`, add (after the existing `[tool.pytest.ini_options]` block):

```toml
[tool.coverage.run]
source = ["clerk"]
branch = true

[tool.coverage.report]
show_missing = true
# Floor set a few points below the measured total so honest refactors do not
# trip it, but a real coverage regression does. Run: pytest --cov=clerk
fail_under = <FLOOR>
```

Replace `<FLOOR>` with an integer a few points below the measured `P` (for example, if `P` is 78, use `75`). Do NOT add `--cov` to the default `addopts` (keep plain `pytest` fast for the dev loop); the floor is enforced when coverage is run with `pytest --cov=clerk`.

- [ ] **Step 3: Verify the floor passes**

Run: `pytest --cov=clerk -q 2>&1 | tail -5`
Expected: the run passes and reports coverage at or above the floor (no "Coverage failure" line).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "test: add a coverage floor (pytest --cov=clerk)"
```

---

## Self-Review

- **Spec coverage (Plan 6, test-hardening slice):** `_parse_message` fixtures incl. HTML-only, multipart+attachment, missing-date, non-UTF8 charset, envelope-address, bare-display-name (Task 1); blocked-recipient (to/cc/bcc, case-insensitive) + FROM-mismatch send-safety layers (Task 2); Gmail OAuth refresh success + refresh-failure-no-browser (Task 3); the dead integration suite rewritten to the current API with the attachment test removed (Task 4); a coverage floor (Task 5).
- **No placeholders:** all test code is concrete. Task 5's `<FLOOR>` is an explicit measure-then-set step (the only value that must be computed at implementation time), with a worked example.
- **Type/name consistency:** tests call the real current API: `_parse_message(uid, data, folder, has_body, fetch_time)`, `check_send_allowed(draft, account_name)`, `get_gmail_credentials(account_name, client_id_file=None)`, `sync_folder`, `create_draft`, `send_draft`, `cache.execute_readonly_sql`, `cache.count_sends_since`, `get_draft`.
- **Integration honesty:** the rewritten suite is Docker-gated (skips via `greenmail_server`'s `wait_for_port` without Docker) and the conftest's stale `get_data_dir` monkeypatch (which would `AttributeError` if the fixture ran) is removed; `test_attachments.py` is deleted rather than left testing a nonexistent feature.
- **Known follow-up (out of scope, noted):** the audit's "OAuth should fail closed in connect (do not pass client_id_file in the IMAP/SMTP connect path so a refresh failure cannot spawn a browser)" is a behavior change, not a test; Task 3 pins the no-client-file-raises behavior but the connect-path hardening is a post-round follow-up.

---

## Roadmap reminder

This is the last plan of the Faithful Mirror trust-and-correctness round. Post-round follow-ons (separate, optional): CONDSTORE/QRESYNC efficient reconciliation over Plan 4's semantics; outbound + inbound attachments (a capability round); OAuth connect-path fail-closed hardening.
