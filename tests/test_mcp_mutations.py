"""Tests for MCP mutation tools (move, flag, mark_unread)."""

from unittest.mock import MagicMock, patch


class TestClerkMove:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_move_success(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_move

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api
        result = clerk_move(message_id="<msg1>", to_folder="Archive")
        assert result["status"] == "success"
        assert result["message_id"] == "<msg1>"
        assert result["folder"] == "Archive"
        mock_api.move_message.assert_called_once_with(
            "<msg1>", "Archive", from_folder="INBOX", account=None
        )

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_move_with_from_folder(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_move

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api
        result = clerk_move(
            message_id="<msg1>", to_folder="Trash", from_folder="Archive"
        )
        assert result["status"] == "success"
        mock_api.move_message.assert_called_once_with(
            "<msg1>", "Trash", from_folder="Archive", account=None
        )

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_move_with_account(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_move

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api
        result = clerk_move(
            message_id="<msg1>", to_folder="Archive", account="work"
        )
        assert result["status"] == "success"
        mock_api.move_message.assert_called_once_with(
            "<msg1>", "Archive", from_folder="INBOX", account="work"
        )

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_move_error(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_move

        mock_api = MagicMock()
        mock_api.move_message.side_effect = Exception("IMAP error")
        mock_get_api.return_value = mock_api
        result = clerk_move(message_id="<msg1>", to_folder="Archive")
        assert "error" in result
        assert "IMAP error" in result["error"]


class TestClerkFlag:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_flag_message(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_flag

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api
        result = clerk_flag(message_id="<msg1>")
        assert result["status"] == "success"
        assert result["flagged"] is True
        assert result["message_id"] == "<msg1>"
        mock_api.flag_message.assert_called_once_with("<msg1>", account=None)

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_unflag_message(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_flag

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api
        result = clerk_flag(message_id="<msg1>", unflag=True)
        assert result["status"] == "success"
        assert result["flagged"] is False
        mock_api.unflag_message.assert_called_once_with("<msg1>", account=None)

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_flag_with_account(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_flag

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api
        result = clerk_flag(message_id="<msg1>", account="work")
        assert result["status"] == "success"
        mock_api.flag_message.assert_called_once_with("<msg1>", account="work")

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_flag_error(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_flag

        mock_api = MagicMock()
        mock_api.flag_message.side_effect = Exception("IMAP error")
        mock_get_api.return_value = mock_api
        result = clerk_flag(message_id="<msg1>")
        assert "error" in result
        assert "IMAP error" in result["error"]

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_unflag_error(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_flag

        mock_api = MagicMock()
        mock_api.unflag_message.side_effect = Exception("IMAP error")
        mock_get_api.return_value = mock_api
        result = clerk_flag(message_id="<msg1>", unflag=True)
        assert "error" in result


class TestClerkMarkUnread:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_mark_unread(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_mark_unread

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api
        result = clerk_mark_unread(message_id="<msg1>")
        assert result["status"] == "success"
        assert result["message_id"] == "<msg1>"
        mock_api.mark_unread.assert_called_once_with("<msg1>", account=None)

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_mark_unread_with_account(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_mark_unread

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api
        result = clerk_mark_unread(message_id="<msg1>", account="work")
        assert result["status"] == "success"
        mock_api.mark_unread.assert_called_once_with("<msg1>", account="work")

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_mark_unread_error(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_mark_unread

        mock_api = MagicMock()
        mock_api.mark_unread.side_effect = Exception("IMAP error")
        mock_get_api.return_value = mock_api
        result = clerk_mark_unread(message_id="<msg1>")
        assert "error" in result
        assert "IMAP error" in result["error"]
