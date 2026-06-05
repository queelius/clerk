"""search_uids returns all matching UIDs ascending, with no 200 cap."""
from datetime import datetime
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
