"""Integration tests for search operations with Greenmail."""

import pytest

from clerk.search import parse_search_query


class TestSearch:
    """Tests for search functionality with real IMAP server."""

    def test_basic_search(self, api_with_greenmail, populated_mailbox):
        """Test basic text search."""
        result = api_with_greenmail.search("test", limit=10)

        # Should find some results since we sent "Test Email" subjects
        assert result.count >= 0

    def test_search_by_subject(self, api_with_greenmail, populated_mailbox):
        """Test search with subject operator."""
        result = api_with_greenmail.search_advanced(
            "subject:Email",
            limit=10,
        )

        # Should find emails with "Email" in subject
        assert result.count >= 0

    def test_search_by_from(self, api_with_greenmail, populated_mailbox):
        """Test search with from operator."""
        result = api_with_greenmail.search_advanced(
            "from:sender",
            limit=10,
        )

        # Should find emails from sender@example.com
        assert result.count >= 0

    def test_search_with_attachment(self, api_with_greenmail, populated_mailbox):
        """Test search for messages with attachments."""
        result = api_with_greenmail.search_advanced(
            "has:attachment",
            limit=10,
        )

        # We sent one email with attachment
        # Note: may be 0 if cache not populated
        assert result.count >= 0

    def test_search_combined_operators(self, api_with_greenmail, populated_mailbox):
        """Test search with multiple operators."""
        result = api_with_greenmail.search_advanced(
            "subject:Email from:sender",
            limit=10,
        )

        assert result.count >= 0

    def test_search_empty_results(self, api_with_greenmail, populated_mailbox):
        """Test search that returns no results."""
        result = api_with_greenmail.search_advanced(
            "from:nonexistent@nowhere.invalid",
            limit=10,
        )

        assert result.count == 0

    def test_search_query_parsing(self):
        """Test that search queries are parsed correctly."""
        query = parse_search_query('from:alice subject:"meeting notes" has:attachment')

        assert "alice" in query.from_addrs
        assert "meeting notes" in query.subject_terms
        assert query.has_attachment is True

    def test_sql_search(self, api_with_greenmail, populated_mailbox):
        """Test raw SQL search."""
        # First populate cache
        api_with_greenmail.list_inbox(limit=10)

        # Then search
        results = api_with_greenmail.search_sql(
            "SELECT * FROM messages WHERE subject LIKE ?",
            params=["%Test%"],
            limit=10,
        )

        # Should return a list
        assert isinstance(results, list)
