"""Tests for the redesigned MCP server (10 tools + 3 resources)."""

import json
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from clerk.cache import Cache
from clerk.config import (
    AccountConfig,
    ClerkConfig,
    FromAddress,
    ImapConfig,
    PrioritiesConfig,
    SmtpConfig,
)
from clerk.models import Address, Message, MessageFlag


@pytest.fixture
def populated_cache(tmp_path):
    """Create a cache with test messages."""
    cache = Cache(tmp_path / "test.db")
    msg1 = Message(
        message_id="<msg1@example.com>",
        conv_id="conv001",
        account="test",
        folder="INBOX",
        **{"from": Address(addr="alice@example.com", name="Alice")},
        to=[Address(addr="test@example.com", name="Test")],
        subject="Hello",
        date=datetime(2026, 3, 1, tzinfo=UTC),
        body_text="Hello body",
        flags=[],
    )
    msg2 = Message(
        message_id="<msg2@example.com>",
        conv_id="conv001",
        account="test",
        folder="INBOX",
        **{"from": Address(addr="test@example.com", name="Test")},
        to=[Address(addr="alice@example.com", name="Alice")],
        subject="Re: Hello",
        date=datetime(2026, 3, 2, tzinfo=UTC),
        body_text="Reply body",
        flags=[MessageFlag.SEEN],
        in_reply_to="<msg1@example.com>",
        references=["<msg1@example.com>"],
    )
    cache.store_message(msg1)
    cache.store_message(msg2)
    return cache


@pytest.fixture
def mock_config():
    return ClerkConfig(
        accounts={
            "test": AccountConfig(
                protocol="imap",
                imap=ImapConfig(host="localhost", username="test"),
                smtp=SmtpConfig(host="localhost", username="test"),
                **{"from": FromAddress(address="test@example.com", name="Test User")},
            ),
        },
        default_account="test",
        priorities=PrioritiesConfig(
            senders=["alice@example.com", "@siue.edu"],
            topics=["IDOT", "scanner"],
        ),
    )


# --- clerk_read ---


class TestClerkRead:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_read_fetches_body(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_read
        from clerk.models import Address, Message, MessageFlag

        mock_msg = Message(
            message_id="<msg1>",
            conv_id="abc123",
            account="test",
            folder="INBOX",
            **{"from": Address(addr="alice@example.com", name="Alice")},
            to=[Address(addr="bob@example.com", name="Bob")],
            subject="Test Subject",
            date=datetime.now(UTC),
            body_text="Hello, this is the body.",
            flags=[MessageFlag.SEEN],
        )
        mock_api = MagicMock()
        mock_api.get_message.return_value = mock_msg
        mock_get_api.return_value = mock_api

        result = clerk_read(message_id="<msg1>")

        mock_api.get_message.assert_called_once_with("<msg1>")
        assert result["body_text"] == "Hello, this is the body."
        assert result["subject"] == "Test Subject"
        assert result["conv_id"] == "abc123"

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_read_not_found(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_read

        mock_api = MagicMock()
        mock_api.get_message.return_value = None
        mock_get_api.return_value = mock_api

        result = clerk_read(message_id="<nonexistent>")
        assert "error" in result
        assert "not found" in result["error"].lower()


# --- Confirmation tokens ---

class TestConfirmationTokens:
    """Tests for the two-step send confirmation flow internals."""

    def test_generate_and_validate_token(self):
        from clerk.mcp_server import (
            _confirmation_tokens,
            _generate_confirmation_token,
            _validate_confirmation_token,
        )

        _confirmation_tokens.clear()

        draft_id = "draft_test123"
        token = _generate_confirmation_token(draft_id)

        assert token is not None
        assert len(token) == 32  # 16 bytes hex = 32 chars
        assert draft_id in _confirmation_tokens

        # Validate the token
        valid, error = _validate_confirmation_token(draft_id, token)
        assert valid is True
        assert error is None

        # Token should be consumed (one-time use)
        assert draft_id not in _confirmation_tokens

    def test_invalid_token(self):
        from clerk.mcp_server import (
            _confirmation_tokens,
            _generate_confirmation_token,
            _validate_confirmation_token,
        )

        _confirmation_tokens.clear()

        draft_id = "draft_test456"
        _generate_confirmation_token(draft_id)

        valid, error = _validate_confirmation_token(draft_id, "wrong_token")
        assert valid is False
        assert "Invalid confirmation token" in error

    def test_no_token_exists(self):
        from clerk.mcp_server import _confirmation_tokens, _validate_confirmation_token

        _confirmation_tokens.clear()

        valid, error = _validate_confirmation_token("nonexistent", "any_token")
        assert valid is False
        assert "No confirmation token found" in error

    def test_expired_token(self):
        from clerk.mcp_server import (
            _confirmation_tokens,
            _validate_confirmation_token,
        )

        _confirmation_tokens.clear()

        draft_id = "draft_expired"
        token = "test_token"
        # Set expiry in the past
        _confirmation_tokens[draft_id] = (token, time.time() - 1)

        valid, error = _validate_confirmation_token(draft_id, token)
        assert valid is False
        assert "expired" in error.lower()

    def test_cleanup_expired_tokens(self):
        from clerk.mcp_server import _cleanup_expired_tokens, _confirmation_tokens

        _confirmation_tokens.clear()

        # Add some tokens - one expired, one valid
        _confirmation_tokens["expired1"] = ("token1", time.time() - 100)
        _confirmation_tokens["expired2"] = ("token2", time.time() - 10)
        _confirmation_tokens["valid1"] = ("token3", time.time() + 300)

        _cleanup_expired_tokens()

        assert "expired1" not in _confirmation_tokens
        assert "expired2" not in _confirmation_tokens
        assert "valid1" in _confirmation_tokens


# --- clerk_sql ---

class TestClerkSql:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_select_returns_rows(self, _dirs, mock_get_api, populated_cache):
        from clerk.mcp_server import clerk_sql

        mock_api = MagicMock()
        mock_api.cache = populated_cache
        mock_get_api.return_value = mock_api

        result = clerk_sql(query="SELECT message_id, subject FROM messages ORDER BY date_utc")
        assert result["count"] == 2
        assert result["rows"][0]["subject"] == "Hello"

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_rejects_non_select(self, _dirs, mock_get_api, populated_cache):
        from clerk.mcp_server import clerk_sql

        mock_api = MagicMock()
        mock_api.cache = populated_cache
        mock_get_api.return_value = mock_api

        result = clerk_sql(query="DELETE FROM messages")
        assert "error" in result


# --- clerk_sync ---

class TestClerkSync:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_sync_calls_api(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_sync

        mock_api = MagicMock()
        mock_api.sync_folder.return_value = {"synced": 5, "account": "test", "folder": "INBOX"}
        mock_get_api.return_value = mock_api

        result = clerk_sync(account="test")
        assert result["synced"] == 5
        mock_api.sync_folder.assert_called_once_with(account="test", folder="INBOX", full=False)

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_sync_full(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_sync

        mock_api = MagicMock()
        mock_api.sync_folder.return_value = {"synced": 100, "account": "test", "folder": "INBOX"}
        mock_get_api.return_value = mock_api

        clerk_sync(account="test", full=True)
        mock_api.sync_folder.assert_called_once_with(account="test", folder="INBOX", full=True)


# --- clerk_reply ---

class TestClerkReplyRouting:
    """Test that clerk_reply routes through api.create_reply()."""

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_reply_calls_api_create_reply(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_reply
        from clerk.models import Address, Draft

        mock_api = MagicMock()
        mock_api.create_reply.return_value = Draft(
            draft_id="d1",
            account="test",
            to=[Address(addr="alice@example.com", name="Alice")],
            cc=[],
            subject="Re: Test",
            body_text="reply body",
        )
        mock_get_api.return_value = mock_api

        result = clerk_reply(message_id="<msg1>", body="reply body")

        mock_api.create_reply.assert_called_once_with(
            message_id="<msg1>",
            body="reply body",
            reply_all=False,
            account=None,
        )
        assert result["draft_id"] == "d1"
        assert "preview" not in result  # no redundant preview

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_reply_with_reply_all(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_reply
        from clerk.models import Address, Draft

        mock_api = MagicMock()
        mock_api.create_reply.return_value = Draft(
            draft_id="d1",
            account="test",
            to=[Address(addr="alice@example.com", name="Alice")],
            cc=[Address(addr="bob@example.com", name="Bob")],
            subject="Re: Test",
            body_text="reply body",
        )
        mock_get_api.return_value = mock_api

        clerk_reply(message_id="<msg1>", body="reply body", reply_all=True)

        mock_api.create_reply.assert_called_once_with(
            message_id="<msg1>",
            body="reply body",
            reply_all=True,
            account=None,
        )

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_reply_message_not_found(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_reply

        mock_api = MagicMock()
        mock_api.create_reply.side_effect = ValueError("Message not found: <msg1>")
        mock_get_api.return_value = mock_api

        result = clerk_reply(message_id="<msg1>", body="reply body")
        assert "error" in result
        assert "not found" in result["error"].lower()


# --- clerk_draft ---

class TestClerkDraftListParams:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_draft_with_list_params(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_draft
        from clerk.models import Address, Draft

        mock_api = MagicMock()
        mock_api.create_draft.return_value = Draft(
            draft_id="d1",
            account="test",
            to=[Address(addr="alice@example.com", name="")],
            cc=[Address(addr="bob@example.com", name="")],
            subject="Test",
            body_text="body",
        )
        mock_get_api.return_value = mock_api

        result = clerk_draft(
            to=["alice@example.com"],
            subject="Test",
            body="body",
            cc=["bob@example.com"],
        )

        assert result["draft_id"] == "d1"
        mock_api.create_draft.assert_called_once_with(
            to=["alice@example.com"],
            subject="Test",
            body="body",
            cc=["bob@example.com"],
            account=None,
        )


# --- clerk_send ---

class TestClerkSend:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    @pytest.mark.asyncio
    async def test_send_step1_returns_token(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_send

        mock_draft = MagicMock()
        mock_draft.account = "test"
        mock_draft.to = [Address(addr="bob@example.com", name="Bob")]
        mock_draft.cc = []
        mock_draft.subject = "Test"
        mock_draft.body_text = "Test body"

        mock_api = MagicMock()
        mock_api.get_draft.return_value = mock_draft
        mock_get_api.return_value = mock_api

        with (
            patch("clerk.mcp_server.check_send_allowed", return_value=(True, None)),
            patch("clerk.mcp_server.format_draft_preview", return_value="Preview text"),
        ):
            result = await clerk_send(draft_id="draft_1")

        assert result["status"] == "pending_confirmation"
        assert "token" in result


# --- clerk_move ---

class TestClerkMoveRedesign:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_move_success(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_move

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api

        result = clerk_move(message_id="<msg1>", to_folder="Archive")
        assert result["status"] == "success"
        mock_api.move_message.assert_called_once()


# --- clerk_flag ---

class TestClerkFlagRedesign:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_flag_action(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_flag

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api

        result = clerk_flag(message_id="<msg1>", action="flag")
        assert result["status"] == "success"
        mock_api.flag_message.assert_called_once()

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_read_action(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_flag

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api

        result = clerk_flag(message_id="<msg1>", action="read")
        assert result["status"] == "success"
        mock_api.mark_read.assert_called_once()

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_invalid_action(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_flag

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api

        result = clerk_flag(message_id="<msg1>", action="invalid")
        assert "error" in result


# --- clerk_status ---

class TestClerkStatusRedesign:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_status(self, _dirs, mock_get_api):
        from clerk.mcp_server import clerk_status

        mock_api = MagicMock()
        mock_api.get_status.return_value = {"version": "0.6.0", "accounts": {}}
        mock_get_api.return_value = mock_api

        result = clerk_status()
        assert "version" in result


# --- clerk_auth ---


class TestClerkAuth:
    @patch("clerk.mcp_server.get_config")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_auth_unknown_account(self, mock_dirs, mock_get_config):
        from clerk.mcp_server import clerk_auth

        mock_config = MagicMock()
        mock_config.accounts = {"siue": MagicMock()}
        mock_get_config.return_value = mock_config

        result = clerk_auth(account="nonexistent")
        assert "error" in result
        assert "not found" in result["error"].lower()

    @patch("clerk.mcp_server.get_config")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_auth_m365_step1_returns_device_code(self, mock_dirs, mock_get_config):
        from clerk.mcp_server import clerk_auth

        mock_acct = MagicMock()
        mock_acct.protocol = "microsoft365"
        mock_config = MagicMock()
        mock_config.accounts = {"siue": mock_acct}
        mock_get_config.return_value = mock_config

        with patch("clerk.mcp_server._auth_m365") as mock_m365:
            mock_m365.return_value = {
                "status": "awaiting_user",
                "protocol": "microsoft365",
                "url": "https://microsoft.com/devicelogin",
                "user_code": "ABCD1234",
                "message": "Go to URL and enter code",
            }
            result = clerk_auth(account="siue")
            assert result["status"] == "awaiting_user"
            assert "user_code" in result

    @patch("clerk.mcp_server.get_config")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_auth_gmail_silent_refresh(self, mock_dirs, mock_get_config):
        from clerk.mcp_server import clerk_auth

        mock_acct = MagicMock()
        mock_acct.protocol = "gmail"
        mock_config = MagicMock()
        mock_config.accounts = {"gmail": mock_acct}
        mock_get_config.return_value = mock_config

        with patch("clerk.mcp_server._auth_gmail") as mock_gmail:
            mock_gmail.return_value = {
                "status": "success",
                "protocol": "gmail",
                "message": "Token refreshed successfully.",
            }
            result = clerk_auth(account="gmail")
            assert result["status"] == "success"

    @patch("clerk.mcp_server.get_config")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_auth_imap_no_password_asks_for_one(self, mock_dirs, mock_get_config):
        from clerk.mcp_server import clerk_auth

        mock_acct = MagicMock()
        mock_acct.protocol = "imap"
        mock_config = MagicMock()
        mock_config.accounts = {"work": mock_acct}
        mock_get_config.return_value = mock_config

        result = clerk_auth(account="work")
        assert result["status"] == "needs_password"

    @patch("clerk.mcp_server.get_config")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_auth_imap_with_password_saves_and_tests(self, mock_dirs, mock_get_config):
        from clerk.mcp_server import clerk_auth

        mock_acct = MagicMock()
        mock_acct.protocol = "imap"
        mock_config = MagicMock()
        mock_config.accounts = {"work": mock_acct}
        mock_get_config.return_value = mock_config

        with patch("clerk.mcp_server._auth_imap") as mock_imap:
            mock_imap.return_value = {
                "status": "success",
                "protocol": "imap",
                "message": "Password updated and connection verified (5 folders).",
            }
            result = clerk_auth(account="work", password="newpass123")
            assert result["status"] == "success"


class TestAuthM365Flow:
    def test_m365_step1_initiates_flow(self):
        from clerk.mcp_server import _auth_m365

        with patch("clerk.mcp_server._pending_device_flows", {}):
            with patch("clerk.microsoft365._build_app") as mock_build:
                mock_app = MagicMock()
                mock_app.initiate_device_flow.return_value = {
                    "verification_uri": "https://microsoft.com/devicelogin",
                    "user_code": "ABCD1234",
                    "message": "Go to https://microsoft.com/devicelogin and enter code ABCD1234",
                }
                mock_build.return_value = mock_app

                result = _auth_m365("siue", confirm=False)
                assert result["status"] == "awaiting_user"
                assert result["user_code"] == "ABCD1234"
                assert result["url"] == "https://microsoft.com/devicelogin"

    def test_m365_step2_without_step1_errors(self):
        from clerk.mcp_server import _auth_m365, _pending_device_flows

        # Clear any leftover flows
        _pending_device_flows.clear()

        result = _auth_m365("siue", confirm=True)
        assert "error" in result
        assert "No pending auth flow" in result["error"]

    def test_m365_step2_completes_successfully(self):
        from clerk.mcp_server import _auth_m365, _pending_device_flows

        mock_app = MagicMock()
        mock_app.acquire_token_by_device_flow.return_value = {"access_token": "tok"}
        mock_app.token_cache.serialize.return_value = "{}"
        _pending_device_flows["siue"] = (mock_app, {"device_code": "xyz"})

        with patch("clerk.microsoft365.save_m365_token_cache"):
            result = _auth_m365("siue", confirm=True)

        assert result["status"] == "success"
        assert "siue" not in _pending_device_flows  # cleaned up


# --- Resources ---

class TestResources:
    @patch("clerk.mcp_server.get_config")
    def test_schema_resource(self, mock_get_config):
        from clerk.mcp_server import resource_schema

        result = resource_schema()
        assert "messages" in result
        assert "messages_fts" in result
        assert "SELECT" in result  # Example queries

    @patch("clerk.mcp_server.get_config")
    def test_config_resource(self, mock_get_config, mock_config):
        from clerk.mcp_server import resource_config

        mock_get_config.return_value = mock_config
        result = resource_config()
        data = json.loads(result)
        assert "accounts" in data
        assert "priorities" in data
        assert data["default_account"] == "test"

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.get_config")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_folders_resource(self, mock_dirs, mock_get_config, mock_get_api, mock_config):
        from clerk.mcp_server import resource_folders
        from clerk.models import FolderInfo

        mock_get_config.return_value = mock_config
        mock_api = MagicMock()
        mock_api.list_folders.return_value = [
            FolderInfo(name="INBOX"), FolderInfo(name="Sent"),
        ]
        mock_api.cache.get_meta.return_value = None  # no cache
        mock_get_api.return_value = mock_api

        result = resource_folders()
        data = json.loads(result)
        assert "test" in data
        assert "INBOX" in data["test"]
        assert "Sent" in data["test"]


# --- clerk_sync all-accounts mode ---

class TestClerkSyncAll:
    @patch("clerk.mcp_server.get_config")
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_sync_all_accounts(self, mock_dirs, mock_get_api, mock_get_config):
        from clerk.mcp_server import clerk_sync

        mock_api = MagicMock()
        mock_api.sync_folder.side_effect = [
            {"synced": 5, "account": "siue", "folder": "INBOX"},
            {"synced": 12, "account": "gmail", "folder": "INBOX"},
        ]
        mock_get_api.return_value = mock_api

        mock_config = MagicMock()
        mock_config.accounts = {"siue": MagicMock(), "gmail": MagicMock()}
        mock_get_config.return_value = mock_config

        result = clerk_sync()

        assert result["total_synced"] == 17
        assert result["accounts"]["siue"]["synced"] == 5
        assert result["accounts"]["gmail"]["synced"] == 12
        assert mock_api.sync_folder.call_count == 2

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_sync_single_account(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_sync

        mock_api = MagicMock()
        mock_api.sync_folder.return_value = {"synced": 5, "account": "siue", "folder": "INBOX"}
        mock_get_api.return_value = mock_api

        result = clerk_sync(account="siue")

        assert result["synced"] == 5
        mock_api.sync_folder.assert_called_once_with(account="siue", folder="INBOX", full=False)


# --- resource_folders caching ---

class TestResourceFoldersCaching:
    @patch("clerk.mcp_server.get_config")
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_caches_folder_list(self, mock_dirs, mock_get_api, mock_get_config):
        from clerk.mcp_server import resource_folders

        mock_api = MagicMock()
        mock_folder = MagicMock()
        mock_folder.name = "INBOX"
        mock_api.list_folders.return_value = [mock_folder]
        mock_api.cache.get_meta.return_value = None  # no cache yet
        mock_get_api.return_value = mock_api

        mock_config = MagicMock()
        mock_config.accounts = {"test": MagicMock()}
        mock_get_config.return_value = mock_config

        # First call hits IMAP
        result = resource_folders()
        assert '"INBOX"' in result
        mock_api.list_folders.assert_called_once()
        mock_api.cache.set_meta.assert_called()  # caches result

    @patch("clerk.mcp_server.get_config")
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_uses_cache_within_ttl(self, mock_dirs, mock_get_api, mock_get_config):
        import json as json_mod
        from datetime import UTC, datetime

        from clerk.mcp_server import resource_folders

        mock_api = MagicMock()
        # Return cached data
        mock_api.cache.get_meta.side_effect = lambda k: {
            "folders_test": json_mod.dumps(["INBOX", "Sent"]),
            "folders_test_at": datetime.now(UTC).isoformat(),
        }.get(k)
        mock_get_api.return_value = mock_api

        mock_config = MagicMock()
        mock_config.accounts = {"test": MagicMock()}
        mock_get_config.return_value = mock_config

        result = resource_folders()
        assert "INBOX" in result
        mock_api.list_folders.assert_not_called()  # did NOT hit IMAP
