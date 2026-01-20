"""Advanced search query parser for clerk.

Supports operators like:
- from:alice
- to:bob
- subject:meeting
- body:quarterly
- has:attachment
- is:unread
- is:read
- is:flagged
- after:2025-01-01
- before:2025-01-31
- date:2025-01-15

Free text is matched against subject, body, from_name, and from_addr.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TokenType(Enum):
    """Token types for the search lexer."""

    OPERATOR = "operator"  # from:, to:, subject:, etc.
    QUOTED = "quoted"  # "quoted phrase"
    WORD = "word"  # regular word
    EOF = "eof"


@dataclass
class Token:
    """A single token from the search query."""

    type: TokenType
    value: str
    operator: str | None = None  # For OPERATOR tokens, the operator name


@dataclass
class SearchQuery:
    """Parsed search query with structured operators."""

    # Free text terms (matched against subject, body, from)
    text_terms: list[str] = field(default_factory=list)

    # Operator constraints
    from_addrs: list[str] = field(default_factory=list)
    to_addrs: list[str] = field(default_factory=list)
    subject_terms: list[str] = field(default_factory=list)
    body_terms: list[str] = field(default_factory=list)

    # Boolean flags
    has_attachment: bool | None = None
    is_unread: bool | None = None
    is_read: bool | None = None
    is_flagged: bool | None = None

    # Date constraints
    after_date: datetime | None = None
    before_date: datetime | None = None
    on_date: datetime | None = None

    # Original query string
    original_query: str = ""

    def is_empty(self) -> bool:
        """Check if the query has no constraints."""
        return (
            not self.text_terms
            and not self.from_addrs
            and not self.to_addrs
            and not self.subject_terms
            and not self.body_terms
            and self.has_attachment is None
            and self.is_unread is None
            and self.is_read is None
            and self.is_flagged is None
            and self.after_date is None
            and self.before_date is None
            and self.on_date is None
        )


# Known operators and their aliases
OPERATORS = {
    "from": "from",
    "f": "from",
    "to": "to",
    "t": "to",
    "subject": "subject",
    "subj": "subject",
    "s": "subject",
    "body": "body",
    "b": "body",
    "has": "has",
    "is": "is",
    "after": "after",
    "since": "after",
    "before": "before",
    "until": "before",
    "date": "date",
    "on": "date",
}


def tokenize(query: str) -> list[Token]:
    """Tokenize a search query string.

    Handles:
    - Quoted phrases: "hello world"
    - Operators: from:alice, subject:"meeting notes"
    - Regular words: hello world
    """
    tokens: list[Token] = []
    pos = 0
    query_len = len(query)

    while pos < query_len:
        # Skip whitespace
        while pos < query_len and query[pos].isspace():
            pos += 1

        if pos >= query_len:
            break

        # Check for quoted string (standalone, not part of operator)
        if query[pos] == '"':
            end = query.find('"', pos + 1)
            if end == -1:
                # Unclosed quote - take rest of string
                value = query[pos + 1 :]
                pos = query_len
            else:
                value = query[pos + 1 : end]
                pos = end + 1
            tokens.append(Token(type=TokenType.QUOTED, value=value))
            continue

        # Check for operator pattern: word:value or word:"quoted value"
        # First, find the word part (up to : or space)
        start = pos
        while pos < query_len and query[pos] not in ": \t\n":
            pos += 1

        if pos < query_len and query[pos] == ":":
            # This might be an operator
            op_name = query[start:pos].lower()
            pos += 1  # Skip the colon

            if op_name in OPERATORS:
                # Read the operator value
                if pos < query_len and query[pos] == '"':
                    # Quoted value: operator:"value"
                    pos += 1  # Skip opening quote
                    end = query.find('"', pos)
                    if end == -1:
                        op_value = query[pos:]
                        pos = query_len
                    else:
                        op_value = query[pos:end]
                        pos = end + 1
                else:
                    # Unquoted value: operator:value
                    value_start = pos
                    while pos < query_len and not query[pos].isspace():
                        pos += 1
                    op_value = query[value_start:pos]

                tokens.append(
                    Token(
                        type=TokenType.OPERATOR,
                        value=op_value,
                        operator=OPERATORS[op_name],
                    )
                )
            else:
                # Not a known operator - treat as word (including the colon part)
                # Continue reading until whitespace
                while pos < query_len and not query[pos].isspace():
                    pos += 1
                tokens.append(Token(type=TokenType.WORD, value=query[start:pos]))
        else:
            # Regular word (no colon found)
            tokens.append(Token(type=TokenType.WORD, value=query[start:pos]))

    tokens.append(Token(type=TokenType.EOF, value=""))
    return tokens


def parse_date(date_str: str) -> datetime | None:
    """Parse a date string in various formats.

    Supports:
    - YYYY-MM-DD
    - YYYY/MM/DD
    - MM/DD/YYYY
    - DD-MM-YYYY
    - today, yesterday
    - Relative: 7d (7 days ago), 1w (1 week ago), 1m (1 month ago)
    """
    date_str = date_str.strip().lower()

    # Handle special keywords
    from datetime import timedelta, timezone

    now = datetime.now(timezone.utc)

    if date_str == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    if date_str == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    # Handle relative dates: 7d, 1w, 1m
    relative_match = re.match(r"^(\d+)([dwm])$", date_str)
    if relative_match:
        num = int(relative_match.group(1))
        unit = relative_match.group(2)

        if unit == "d":
            return now - timedelta(days=num)
        elif unit == "w":
            return now - timedelta(weeks=num)
        elif unit == "m":
            return now - timedelta(days=num * 30)  # Approximate

    # Try various date formats
    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y%m%d",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def parse_search_query(query: str) -> SearchQuery:
    """Parse a search query string into a SearchQuery object.

    Examples:
        parse_search_query("from:alice subject:meeting")
        parse_search_query("is:unread has:attachment")
        parse_search_query("quarterly report after:2025-01-01")
    """
    tokens = tokenize(query)
    result = SearchQuery(original_query=query)

    for token in tokens:
        if token.type == TokenType.EOF:
            break

        if token.type == TokenType.WORD or token.type == TokenType.QUOTED:
            # Free text search term
            if token.value:
                result.text_terms.append(token.value)

        elif token.type == TokenType.OPERATOR:
            op = token.operator
            value = token.value

            if op == "from":
                result.from_addrs.append(value)

            elif op == "to":
                result.to_addrs.append(value)

            elif op == "subject":
                result.subject_terms.append(value)

            elif op == "body":
                result.body_terms.append(value)

            elif op == "has":
                if value.lower() in ("attachment", "attachments", "attach"):
                    result.has_attachment = True

            elif op == "is":
                val_lower = value.lower()
                if val_lower == "unread":
                    result.is_unread = True
                    result.is_read = False
                elif val_lower == "read":
                    result.is_read = True
                    result.is_unread = False
                elif val_lower in ("flagged", "starred", "important"):
                    result.is_flagged = True
                elif val_lower == "unflagged":
                    result.is_flagged = False

            elif op == "after":
                result.after_date = parse_date(value)

            elif op == "before":
                result.before_date = parse_date(value)

            elif op == "date":
                result.on_date = parse_date(value)

    return result


def build_fts_query(query: SearchQuery) -> str:
    """Build an FTS5 query string from a SearchQuery.

    This creates a query for the messages_fts table which indexes:
    - message_id, subject, body_text, from_name, from_addr
    """
    parts: list[str] = []

    # Free text terms search across all FTS columns
    for term in query.text_terms:
        # Escape special FTS5 characters and quote
        escaped = term.replace('"', '""')
        parts.append(f'"{escaped}"')

    # From address/name search
    for addr in query.from_addrs:
        escaped = addr.replace('"', '""')
        parts.append(f'from_addr:"{escaped}" OR from_name:"{escaped}"')

    # Subject search
    for term in query.subject_terms:
        escaped = term.replace('"', '""')
        parts.append(f'subject:"{escaped}"')

    # Body search
    for term in query.body_terms:
        escaped = term.replace('"', '""')
        parts.append(f'body_text:"{escaped}"')

    if not parts:
        return "*"  # Match all

    return " AND ".join(f"({p})" for p in parts) if len(parts) > 1 else parts[0]


def build_where_clauses(
    query: SearchQuery,
) -> tuple[list[str], list[Any]]:
    """Build SQL WHERE clauses and parameters from a SearchQuery.

    Returns (clauses, params) for use in SQL queries.
    """
    clauses: list[str] = []
    params: list[Any] = []

    # To address search (requires JSON parsing)
    for addr in query.to_addrs:
        # Search in the JSON array of to addresses
        clauses.append("to_json LIKE ?")
        params.append(f"%{addr}%")

    # Has attachment
    if query.has_attachment is True:
        clauses.append("attachments_json != '[]'")
    elif query.has_attachment is False:
        clauses.append("attachments_json = '[]'")

    # Read/unread status
    if query.is_read is True:
        clauses.append("flags LIKE '%\"seen\"%'")
    elif query.is_unread is True:
        clauses.append("flags NOT LIKE '%\"seen\"%'")

    # Flagged status
    if query.is_flagged is True:
        clauses.append("flags LIKE '%\"flagged\"%'")
    elif query.is_flagged is False:
        clauses.append("flags NOT LIKE '%\"flagged\"%'")

    # Date constraints
    if query.after_date:
        clauses.append("date_utc >= ?")
        params.append(query.after_date.isoformat())

    if query.before_date:
        clauses.append("date_utc < ?")
        params.append(query.before_date.isoformat())

    if query.on_date:
        # Match entire day
        start = query.on_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = query.on_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        clauses.append("date_utc >= ? AND date_utc <= ?")
        params.append(start.isoformat())
        params.append(end.isoformat())

    return clauses, params
