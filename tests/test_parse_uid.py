"""The parsed Message must carry the server UID it was fetched under."""
from datetime import datetime

from clerk.config import AccountConfig, ImapConfig, SmtpConfig
from clerk.imap_client import ImapClient


def _client() -> ImapClient:
    cfg = AccountConfig(
        protocol="imap",
        imap=ImapConfig(host="h", username="u"),
        smtp=SmtpConfig(host="h", username="u"),
        **{"from": {"address": "u@example.com"}},
    )
    return ImapClient("acct", cfg)


class _Env:
    # Minimal stand-in for an imapclient ENVELOPE object.
    def __init__(self):
        self.date = datetime(2026, 1, 2)
        self.subject = b"Hello"
        self.from_ = None
        self.message_id = b"<m1@example.com>"


def test_parse_message_sets_uid():
    client = _client()
    data = {
        b"ENVELOPE": _Env(),
        b"FLAGS": (b"\\Seen",),
        b"BODY[HEADER]": b"To: a@b.com\r\nSubject: Hello\r\n\r\n",
    }
    msg = client._parse_message(
        uid=777, data=data, folder="INBOX", has_body=False, fetch_time=datetime(2026, 1, 2)
    )
    assert msg is not None
    assert msg.uid == 777
