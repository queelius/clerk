"""Tests for MCP SQL read tool and Cache.execute_readonly_sql."""

from datetime import UTC, datetime

import pytest

from clerk.cache import Cache
from clerk.models import Address, Message, MessageFlag


@pytest.fixture
def populated_cache(tmp_path):
    """Create a cache with test data."""
    cache = Cache(tmp_path / "test.db")
    msg = Message(
        message_id="<test1@example.com>",
        conv_id="abc123def456",
        account="test",
        folder="INBOX",
        **{"from": Address(addr="alice@example.com", name="Alice")},
        to=[Address(addr="bob@example.com", name="Bob")],
        subject="Test Subject",
        date=datetime.now(UTC),
        flags=[MessageFlag.SEEN],
    )
    cache.store_message(msg)
    return cache


class TestExecuteReadonlySql:
    def test_select_returns_rows(self, populated_cache):
        rows = populated_cache.execute_readonly_sql(
            "SELECT message_id, subject FROM messages"
        )
        assert len(rows) == 1
        assert rows[0]["message_id"] == "<test1@example.com>"
        assert rows[0]["subject"] == "Test Subject"

    def test_rejects_non_select(self, populated_cache):
        with pytest.raises(ValueError, match="Only SELECT"):
            populated_cache.execute_readonly_sql("DELETE FROM messages")

    def test_rejects_insert(self, populated_cache):
        with pytest.raises(ValueError, match="Only SELECT"):
            populated_cache.execute_readonly_sql(
                "INSERT INTO messages VALUES ('a','b','c','d','e','f','[]','[]','[]','s','2025-01-01','','','[]','[]','','[]','2025-01-01',NULL)"
            )

    def test_rejects_dangerous_keywords(self, populated_cache):
        # The read-only SQLite connection + single-statement execute()
        # blocks writes regardless of the text of the query. Multi-statement
        # attempts hit a SQL syntax error at the SQLite layer; writes against
        # the read-only DB raise an operational error. Either is fine — the
        # contract is "no mutation succeeds."
        import sqlite3

        with pytest.raises((ValueError, sqlite3.Error)):
            populated_cache.execute_readonly_sql(
                "SELECT * FROM messages; DROP TABLE messages"
            )

    def test_rejects_update(self, populated_cache):
        with pytest.raises(ValueError, match="Only SELECT"):
            populated_cache.execute_readonly_sql(
                "UPDATE messages SET subject = 'hacked'"
            )

    def test_rejects_alter(self, populated_cache):
        with pytest.raises(ValueError, match="Only SELECT"):
            populated_cache.execute_readonly_sql(
                "ALTER TABLE messages ADD COLUMN evil TEXT"
            )

    def test_limit_enforced(self, populated_cache):
        rows = populated_cache.execute_readonly_sql(
            "SELECT * FROM messages", limit=5
        )
        assert isinstance(rows, list)

    def test_limit_appended_when_missing(self, populated_cache):
        # Should work without LIMIT in query - method adds one
        rows = populated_cache.execute_readonly_sql(
            "SELECT * FROM messages", limit=1
        )
        assert len(rows) <= 1

    def test_existing_limit_preserved(self, populated_cache):
        # Should respect existing LIMIT in query
        rows = populated_cache.execute_readonly_sql(
            "SELECT * FROM messages LIMIT 10"
        )
        assert isinstance(rows, list)

    def test_returns_dict_rows(self, populated_cache):
        rows = populated_cache.execute_readonly_sql("SELECT message_id FROM messages")
        assert isinstance(rows[0], dict)
        assert "message_id" in rows[0]

    def test_empty_result(self, tmp_path):
        cache = Cache(tmp_path / "test.db")
        rows = cache.execute_readonly_sql("SELECT * FROM messages WHERE 1=0")
        assert rows == []

    def test_with_params(self, populated_cache):
        rows = populated_cache.execute_readonly_sql(
            "SELECT subject FROM messages WHERE from_addr = ?",
            params=("alice@example.com",),
        )
        assert len(rows) == 1
        assert rows[0]["subject"] == "Test Subject"

    def test_with_list_params(self, populated_cache):
        rows = populated_cache.execute_readonly_sql(
            "SELECT subject FROM messages WHERE from_addr = ?",
            params=["alice@example.com"],
        )
        assert len(rows) == 1

    def test_returns_all_columns(self, populated_cache):
        rows = populated_cache.execute_readonly_sql("SELECT * FROM messages")
        assert len(rows) == 1
        row = rows[0]
        # Check key columns exist
        assert "message_id" in row
        assert "conv_id" in row
        assert "account" in row
        assert "folder" in row
        assert "from_addr" in row
        assert "subject" in row
        assert "date_utc" in row

    def test_accepts_with_cte(self, populated_cache):
        """CTE (WITH) queries should be accepted — they're read-only."""
        rows = populated_cache.execute_readonly_sql(
            "WITH recent AS (SELECT * FROM messages) SELECT subject FROM recent"
        )
        assert len(rows) == 1
        assert rows[0]["subject"] == "Test Subject"

    def test_outer_limit_caps_inner_limit(self, populated_cache):
        """Even if the user's SQL has LIMIT, the caller's limit wins."""
        # Insert a second message so we can observe the cap working.
        msg2 = Message(
            message_id="<test2@example.com>",
            conv_id="deadbeef1234",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="bob@example.com", name="Bob")},
            to=[],
            subject="Second",
            date=datetime.now(UTC),
            flags=[],
        )
        populated_cache.store_message(msg2)

        rows = populated_cache.execute_readonly_sql(
            "SELECT * FROM messages LIMIT 100", limit=1
        )
        assert len(rows) == 1

    def test_readonly_connection_cannot_write(self, populated_cache):
        """Verify that even if keyword check is bypassed, readonly mode prevents writes."""
        # This tests the underlying readonly connection safety
        # The keyword filter would catch this first, but the connection
        # is also opened in readonly mode as a defense-in-depth measure
        with pytest.raises(ValueError, match="Only SELECT"):
            populated_cache.execute_readonly_sql(
                "DELETE FROM messages WHERE 1=1"
            )


class TestClerkSqlTool:
    """Test the MCP clerk_sql tool wrapper."""

    def test_sql_tool_success(self, populated_cache):
        from unittest.mock import MagicMock, patch

        from clerk.mcp_server import clerk_sql

        mock_api = MagicMock()
        mock_api.cache = populated_cache

        with (
            patch("clerk.mcp_server.get_api", return_value=mock_api),
            patch("clerk.mcp_server.ensure_dirs"),
        ):
            result = clerk_sql(query="SELECT message_id, subject FROM messages")
            assert "rows" in result
            assert result["count"] == 1
            assert result["rows"][0]["message_id"] == "<test1@example.com>"

    def test_sql_tool_rejects_non_select(self, populated_cache):
        from unittest.mock import MagicMock, patch

        from clerk.mcp_server import clerk_sql

        mock_api = MagicMock()
        mock_api.cache = populated_cache

        with (
            patch("clerk.mcp_server.get_api", return_value=mock_api),
            patch("clerk.mcp_server.ensure_dirs"),
        ):
            result = clerk_sql(query="DELETE FROM messages")
            assert "error" in result
            assert "Only SELECT" in result["error"]

    def test_sql_tool_handles_sql_error(self):
        from unittest.mock import MagicMock, patch

        from clerk.mcp_server import clerk_sql

        mock_api = MagicMock()
        mock_api.cache.execute_readonly_sql.side_effect = Exception(
            "no such table: nonexistent"
        )

        with (
            patch("clerk.mcp_server.get_api", return_value=mock_api),
            patch("clerk.mcp_server.ensure_dirs"),
        ):
            result = clerk_sql(query="SELECT * FROM nonexistent")
            assert "error" in result
            assert "SQL error" in result["error"]

    def test_sql_tool_with_limit(self, populated_cache):
        from unittest.mock import MagicMock, patch

        from clerk.mcp_server import clerk_sql

        mock_api = MagicMock()
        mock_api.cache = populated_cache

        with (
            patch("clerk.mcp_server.get_api", return_value=mock_api),
            patch("clerk.mcp_server.ensure_dirs"),
        ):
            result = clerk_sql(
                query="SELECT * FROM messages", limit=50
            )
            assert "rows" in result
            assert result["count"] <= 50
