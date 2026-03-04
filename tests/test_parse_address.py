"""Tests for address parsing, especially malformed headers."""

from clerk.imap_client import parse_address, parse_address_list


class TestParseAddress:
    def test_normal_address(self):
        result = parse_address(("John Doe", "john@example.com"))
        assert result is not None
        assert result.addr == "john@example.com"
        assert result.name == "John Doe"

    def test_address_only(self):
        result = parse_address(("", "john@example.com"))
        assert result is not None
        assert result.addr == "john@example.com"
        assert result.name == ""

    def test_none_input(self):
        assert parse_address(None) is None

    def test_empty_addr(self):
        assert parse_address(("Name", "")) is None

    def test_bare_name_no_at_sign(self):
        """M365 sometimes sends just a display name (e.g., 'Towell') with no email."""
        result = parse_address(("", "Towell"))
        assert result is not None
        assert result.addr == ""
        assert result.name == "Towell"

    def test_bare_name_in_name_field(self):
        result = parse_address(("Towell", "Towell"))
        assert result is not None
        assert result.addr == ""
        assert result.name == "Towell"


class TestParseAddressList:
    def test_malformed_address_in_list(self):
        """Malformed addresses should be included with name preserved."""
        results = parse_address_list("Towell, bob@example.com")
        assert len(results) == 2
        assert results[0].addr == ""
        assert results[0].name == "Towell"
        assert results[1].addr == "bob@example.com"
