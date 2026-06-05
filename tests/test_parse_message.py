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
    assert msg.body_text is None
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
    addrs = parse_address_list("Just A Name")
    assert len(addrs) == 1
    assert addrs[0].addr == ""
    assert "Just A Name" in addrs[0].name
