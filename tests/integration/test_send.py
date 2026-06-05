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
