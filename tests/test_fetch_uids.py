"""search_uids returns all matching UIDs ascending, with no 200 cap."""
from datetime import datetime
from unittest.mock import MagicMock

from clerk.config import AccountConfig, ImapConfig, SmtpConfig
from clerk.imap_client import ImapClient
from clerk.models import MessageFlag


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


def test_add_flags_by_uid_no_search():
    c = _client()
    c.add_flags_by_uid("INBOX", 5, [MessageFlag.SEEN])
    c._client.add_flags.assert_called_once_with([5], ["\\Seen"])
    c._client.search.assert_not_called()


def test_remove_flags_by_uid():
    c = _client()
    c.remove_flags_by_uid("INBOX", 5, [MessageFlag.FLAGGED])
    c._client.remove_flags.assert_called_once_with([5], ["\\Flagged"])


def test_set_flags_by_uid():
    c = _client()
    c.set_flags_by_uid("INBOX", 5, [MessageFlag.SEEN])
    c._client.set_flags.assert_called_once_with([5], ["\\Seen"])


def test_move_message_by_uid_uses_move_no_search():
    c = _client()
    c.move_message_by_uid("INBOX", 5, "Archive")
    c._client.move.assert_called_once_with([5], "Archive")
    c._client.search.assert_not_called()
    c._client.expunge.assert_not_called()


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
    c._client.expunge.assert_not_called()
