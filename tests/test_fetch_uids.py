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
