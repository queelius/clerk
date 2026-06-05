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
        assert len(rows) >= 1
