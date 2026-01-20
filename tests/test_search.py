"""Tests for the search query parser."""

from datetime import datetime, timedelta, timezone

import pytest

from clerk.search import (
    SearchQuery,
    Token,
    TokenType,
    build_fts_query,
    build_where_clauses,
    parse_date,
    parse_search_query,
    tokenize,
)


class TestTokenize:
    """Tests for the tokenize function."""

    def test_simple_word(self):
        tokens = tokenize("hello")
        assert len(tokens) == 2  # word + EOF
        assert tokens[0].type == TokenType.WORD
        assert tokens[0].value == "hello"

    def test_multiple_words(self):
        tokens = tokenize("hello world")
        assert len(tokens) == 3  # 2 words + EOF
        assert tokens[0].value == "hello"
        assert tokens[1].value == "world"

    def test_quoted_phrase(self):
        tokens = tokenize('"hello world"')
        assert len(tokens) == 2  # quoted + EOF
        assert tokens[0].type == TokenType.QUOTED
        assert tokens[0].value == "hello world"

    def test_operator_simple(self):
        tokens = tokenize("from:alice")
        assert len(tokens) == 2
        assert tokens[0].type == TokenType.OPERATOR
        assert tokens[0].operator == "from"
        assert tokens[0].value == "alice"

    def test_operator_with_quoted_value(self):
        tokens = tokenize('subject:"meeting notes"')
        assert len(tokens) == 2
        assert tokens[0].type == TokenType.OPERATOR
        assert tokens[0].operator == "subject"
        assert tokens[0].value == "meeting notes"

    def test_mixed_query(self):
        tokens = tokenize("from:alice important meeting")
        assert len(tokens) == 4  # operator + 2 words + EOF
        assert tokens[0].type == TokenType.OPERATOR
        assert tokens[0].operator == "from"
        assert tokens[1].type == TokenType.WORD
        assert tokens[1].value == "important"
        assert tokens[2].type == TokenType.WORD
        assert tokens[2].value == "meeting"

    def test_unknown_operator(self):
        tokens = tokenize("unknown:value")
        assert len(tokens) == 2
        assert tokens[0].type == TokenType.WORD
        assert tokens[0].value == "unknown:value"

    def test_operator_aliases(self):
        # f: is alias for from:
        tokens = tokenize("f:bob")
        assert tokens[0].operator == "from"
        assert tokens[0].value == "bob"

        # s: is alias for subject:
        tokens = tokenize("s:test")
        assert tokens[0].operator == "subject"

        # since: is alias for after:
        tokens = tokenize("since:2025-01-01")
        assert tokens[0].operator == "after"

    def test_unclosed_quote(self):
        tokens = tokenize('"unclosed')
        assert len(tokens) == 2
        assert tokens[0].type == TokenType.QUOTED
        assert tokens[0].value == "unclosed"

    def test_empty_string(self):
        tokens = tokenize("")
        assert len(tokens) == 1
        assert tokens[0].type == TokenType.EOF

    def test_whitespace_only(self):
        tokens = tokenize("   ")
        assert len(tokens) == 1
        assert tokens[0].type == TokenType.EOF


class TestParseDate:
    """Tests for date parsing."""

    def test_iso_format(self):
        result = parse_date("2025-01-15")
        assert result is not None
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15

    def test_slash_format(self):
        result = parse_date("2025/01/15")
        assert result is not None
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15

    def test_today(self):
        result = parse_date("today")
        assert result is not None
        now = datetime.now(timezone.utc)
        assert result.year == now.year
        assert result.month == now.month
        assert result.day == now.day

    def test_yesterday(self):
        result = parse_date("yesterday")
        assert result is not None
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        assert result.year == yesterday.year
        assert result.month == yesterday.month
        assert result.day == yesterday.day

    def test_relative_days(self):
        result = parse_date("7d")
        assert result is not None
        expected = datetime.now(timezone.utc) - timedelta(days=7)
        # Allow 1 second tolerance
        assert abs((result - expected).total_seconds()) < 1

    def test_relative_weeks(self):
        result = parse_date("2w")
        assert result is not None
        expected = datetime.now(timezone.utc) - timedelta(weeks=2)
        assert abs((result - expected).total_seconds()) < 1

    def test_relative_months(self):
        result = parse_date("1m")
        assert result is not None
        expected = datetime.now(timezone.utc) - timedelta(days=30)
        assert abs((result - expected).total_seconds()) < 1

    def test_invalid_date(self):
        result = parse_date("not-a-date")
        assert result is None


class TestParseSearchQuery:
    """Tests for the main parse_search_query function."""

    def test_free_text(self):
        query = parse_search_query("hello world")
        assert query.text_terms == ["hello", "world"]
        assert query.original_query == "hello world"

    def test_from_operator(self):
        query = parse_search_query("from:alice@example.com")
        assert query.from_addrs == ["alice@example.com"]

    def test_to_operator(self):
        query = parse_search_query("to:bob@example.com")
        assert query.to_addrs == ["bob@example.com"]

    def test_subject_operator(self):
        query = parse_search_query('subject:"quarterly report"')
        assert query.subject_terms == ["quarterly report"]

    def test_body_operator(self):
        query = parse_search_query("body:important")
        assert query.body_terms == ["important"]

    def test_has_attachment(self):
        query = parse_search_query("has:attachment")
        assert query.has_attachment is True

        query2 = parse_search_query("has:attachments")
        assert query2.has_attachment is True

    def test_is_unread(self):
        query = parse_search_query("is:unread")
        assert query.is_unread is True
        assert query.is_read is False

    def test_is_read(self):
        query = parse_search_query("is:read")
        assert query.is_read is True
        assert query.is_unread is False

    def test_is_flagged(self):
        query = parse_search_query("is:flagged")
        assert query.is_flagged is True

        query2 = parse_search_query("is:starred")
        assert query2.is_flagged is True

    def test_after_date(self):
        query = parse_search_query("after:2025-01-01")
        assert query.after_date is not None
        assert query.after_date.year == 2025
        assert query.after_date.month == 1

    def test_before_date(self):
        query = parse_search_query("before:2025-12-31")
        assert query.before_date is not None
        assert query.before_date.year == 2025
        assert query.before_date.month == 12

    def test_date_on(self):
        query = parse_search_query("date:2025-06-15")
        assert query.on_date is not None
        assert query.on_date.day == 15

    def test_complex_query(self):
        query = parse_search_query(
            "from:alice to:bob is:unread has:attachment after:2025-01-01 important"
        )
        assert query.from_addrs == ["alice"]
        assert query.to_addrs == ["bob"]
        assert query.is_unread is True
        assert query.has_attachment is True
        assert query.after_date is not None
        assert query.text_terms == ["important"]

    def test_empty_query(self):
        query = parse_search_query("")
        assert query.is_empty() is True

    def test_multiple_from(self):
        query = parse_search_query("from:alice from:bob")
        assert query.from_addrs == ["alice", "bob"]


class TestBuildFtsQuery:
    """Tests for FTS query building."""

    def test_simple_text(self):
        query = SearchQuery(text_terms=["hello", "world"])
        fts = build_fts_query(query)
        assert '"hello"' in fts
        assert '"world"' in fts
        assert "AND" in fts

    def test_from_search(self):
        query = SearchQuery(from_addrs=["alice"])
        fts = build_fts_query(query)
        assert "from_addr" in fts
        assert "from_name" in fts
        assert "alice" in fts

    def test_subject_search(self):
        query = SearchQuery(subject_terms=["meeting"])
        fts = build_fts_query(query)
        assert "subject:" in fts
        assert "meeting" in fts

    def test_body_search(self):
        query = SearchQuery(body_terms=["important"])
        fts = build_fts_query(query)
        assert "body_text:" in fts
        assert "important" in fts

    def test_empty_query(self):
        query = SearchQuery()
        fts = build_fts_query(query)
        assert fts == "*"

    def test_quote_escaping(self):
        query = SearchQuery(text_terms=['say "hello"'])
        fts = build_fts_query(query)
        assert '""' in fts  # Escaped quote


class TestBuildWhereClauses:
    """Tests for SQL WHERE clause building."""

    def test_to_address(self):
        query = SearchQuery(to_addrs=["bob@example.com"])
        clauses, params = build_where_clauses(query)
        assert len(clauses) == 1
        assert "to_json LIKE" in clauses[0]
        assert "bob@example.com" in params[0]

    def test_has_attachment(self):
        query = SearchQuery(has_attachment=True)
        clauses, params = build_where_clauses(query)
        assert "attachments_json != '[]'" in clauses[0]

    def test_no_attachment(self):
        query = SearchQuery(has_attachment=False)
        clauses, params = build_where_clauses(query)
        assert "attachments_json = '[]'" in clauses[0]

    def test_is_read(self):
        query = SearchQuery(is_read=True)
        clauses, params = build_where_clauses(query)
        assert "flags LIKE '%\"seen\"%'" in clauses[0]

    def test_is_unread(self):
        query = SearchQuery(is_unread=True)
        clauses, params = build_where_clauses(query)
        assert "flags NOT LIKE '%\"seen\"%'" in clauses[0]

    def test_is_flagged(self):
        query = SearchQuery(is_flagged=True)
        clauses, params = build_where_clauses(query)
        assert "flags LIKE '%\"flagged\"%'" in clauses[0]

    def test_after_date(self):
        query = SearchQuery(after_date=datetime(2025, 1, 1, tzinfo=timezone.utc))
        clauses, params = build_where_clauses(query)
        assert "date_utc >=" in clauses[0]
        assert "2025-01-01" in params[0]

    def test_before_date(self):
        query = SearchQuery(before_date=datetime(2025, 12, 31, tzinfo=timezone.utc))
        clauses, params = build_where_clauses(query)
        assert "date_utc <" in clauses[0]
        assert "2025-12-31" in params[0]

    def test_on_date(self):
        query = SearchQuery(on_date=datetime(2025, 6, 15, tzinfo=timezone.utc))
        clauses, params = build_where_clauses(query)
        assert "date_utc >=" in clauses[0]
        assert "date_utc <=" in clauses[0]
        assert len(params) == 2

    def test_empty_query(self):
        query = SearchQuery()
        clauses, params = build_where_clauses(query)
        assert len(clauses) == 0
        assert len(params) == 0


class TestSearchQueryIsEmpty:
    """Tests for SearchQuery.is_empty method."""

    def test_empty(self):
        query = SearchQuery()
        assert query.is_empty() is True

    def test_with_text(self):
        query = SearchQuery(text_terms=["hello"])
        assert query.is_empty() is False

    def test_with_from(self):
        query = SearchQuery(from_addrs=["alice"])
        assert query.is_empty() is False

    def test_with_flag(self):
        query = SearchQuery(is_unread=True)
        assert query.is_empty() is False

    def test_with_date(self):
        query = SearchQuery(after_date=datetime.now(timezone.utc))
        assert query.is_empty() is False
