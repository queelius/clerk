"""Tests for clerk configuration."""

import tempfile
from pathlib import Path

import pytest
import yaml

from clerk.config import (
    AccountConfig,
    CacheConfig,
    ClerkConfig,
    FromAddress,
    ImapConfig,
    OAuthConfig,
    SendConfig,
    SmtpConfig,
    load_config,
)


class TestFromAddress:
    def test_create(self):
        addr = FromAddress(address="user@example.com", name="User Name")
        assert addr.address == "user@example.com"
        assert addr.name == "User Name"

    def test_default_name(self):
        addr = FromAddress(address="user@example.com")
        assert addr.name == ""


class TestImapConfig:
    def test_create_with_defaults(self):
        imap = ImapConfig(host="imap.example.com", username="user@example.com")
        assert imap.port == 993
        assert imap.ssl is True

    def test_create_custom_port(self):
        imap = ImapConfig(host="imap.example.com", username="user", port=143, ssl=False)
        assert imap.port == 143
        assert imap.ssl is False


class TestSmtpConfig:
    def test_create_with_defaults(self):
        smtp = SmtpConfig(host="smtp.example.com", username="user@example.com")
        assert smtp.port == 587
        assert smtp.starttls is True


class TestAccountConfig:
    def test_imap_account(self):
        acc = AccountConfig(
            protocol="imap",
            imap=ImapConfig(host="imap.ex.com", username="user@ex.com"),
            smtp=SmtpConfig(host="smtp.ex.com", username="user@ex.com"),
            **{"from": FromAddress(address="user@ex.com")},
        )
        assert acc.protocol == "imap"
        assert acc.imap is not None
        assert acc.smtp is not None

    def test_imap_requires_smtp(self):
        with pytest.raises(ValueError, match="smtp"):
            AccountConfig(
                protocol="imap",
                imap=ImapConfig(host="imap.ex.com", username="user"),
                **{"from": FromAddress(address="user@ex.com")},
            )

    def test_gmail_requires_oauth(self):
        with pytest.raises(ValueError, match="oauth"):
            AccountConfig(
                protocol="gmail",
                **{"from": FromAddress(address="user@gmail.com")},
            )


class TestCacheConfig:
    def test_defaults(self):
        cache = CacheConfig()
        assert cache.window_days == 7
        assert cache.inbox_freshness_min == 5
        assert cache.body_freshness_min == 60

    def test_custom_values(self):
        cache = CacheConfig(window_days=14, inbox_freshness_min=10)
        assert cache.window_days == 14
        assert cache.inbox_freshness_min == 10

    def test_validation(self):
        with pytest.raises(ValueError):
            CacheConfig(window_days=0)  # Must be >= 1

        with pytest.raises(ValueError):
            CacheConfig(window_days=400)  # Must be <= 365


class TestSendConfig:
    def test_defaults(self):
        send = SendConfig()
        assert send.require_confirmation is True
        assert send.rate_limit == 20
        assert send.blocked_recipients == []

    def test_blocked_recipients(self):
        send = SendConfig(blocked_recipients=["spam@example.com", "test@example.com"])
        assert len(send.blocked_recipients) == 2


class TestClerkConfig:
    def test_empty_config(self):
        config = ClerkConfig()
        assert config.default_account == ""
        assert config.accounts == {}

    def test_with_accounts(self):
        config = ClerkConfig(
            default_account="personal",
            accounts={
                "personal": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.ex.com", username="user@ex.com"),
                    smtp=SmtpConfig(host="smtp.ex.com", username="user@ex.com"),
                    **{"from": FromAddress(address="user@ex.com")},
                ),
            },
        )
        assert config.default_account == "personal"
        assert "personal" in config.accounts

    def test_default_account_validation(self):
        with pytest.raises(ValueError, match="not found"):
            ClerkConfig(
                default_account="nonexistent",
                accounts={
                    "personal": AccountConfig(
                        protocol="imap",
                        imap=ImapConfig(host="imap.ex.com", username="user"),
                        smtp=SmtpConfig(host="smtp.ex.com", username="user"),
                        **{"from": FromAddress(address="user@ex.com")},
                    ),
                },
            )

    def test_auto_default_account(self):
        """If no default specified but accounts exist, use first one."""
        config = ClerkConfig(
            accounts={
                "work": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.ex.com", username="user"),
                    smtp=SmtpConfig(host="smtp.ex.com", username="user"),
                    **{"from": FromAddress(address="user@work.com")},
                ),
            },
        )
        assert config.default_account == "work"

    def test_get_account(self):
        config = ClerkConfig(
            default_account="personal",
            accounts={
                "personal": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.ex.com", username="user"),
                    smtp=SmtpConfig(host="smtp.ex.com", username="user"),
                    **{"from": FromAddress(address="user@ex.com")},
                ),
                "work": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.work.com", username="user"),
                    smtp=SmtpConfig(host="smtp.work.com", username="user"),
                    **{"from": FromAddress(address="user@work.com")},
                ),
            },
        )

        # Get by name
        name, acc = config.get_account("work")
        assert name == "work"
        assert acc.from_.address == "user@work.com"

        # Get default
        name, acc = config.get_account(None)
        assert name == "personal"

    def test_get_account_not_found(self):
        config = ClerkConfig(
            accounts={
                "personal": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.ex.com", username="user"),
                    smtp=SmtpConfig(host="smtp.ex.com", username="user"),
                    **{"from": FromAddress(address="user@ex.com")},
                ),
            },
        )

        with pytest.raises(ValueError, match="not found"):
            config.get_account("nonexistent")


class TestLoadConfig:
    def test_load_nonexistent_file(self, tmp_path):
        """Loading a nonexistent config returns empty config."""
        config_path = tmp_path / "nonexistent.yaml"
        config = load_config(config_path)
        assert config.accounts == {}

    def test_load_valid_config(self, tmp_path):
        """Load a valid YAML config file."""
        config_path = tmp_path / "config.yaml"
        config_data = {
            "default_account": "personal",
            "accounts": {
                "personal": {
                    "protocol": "imap",
                    "imap": {
                        "host": "imap.fastmail.com",
                        "port": 993,
                        "username": "user@fastmail.com",
                    },
                    "smtp": {
                        "host": "smtp.fastmail.com",
                        "port": 587,
                        "username": "user@fastmail.com",
                    },
                    "from": {
                        "address": "user@fastmail.com",
                        "name": "User Name",
                    },
                },
            },
            "cache": {
                "window_days": 14,
            },
            "send": {
                "rate_limit": 10,
            },
        }

        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        config = load_config(config_path)

        assert config.default_account == "personal"
        assert "personal" in config.accounts
        assert config.cache.window_days == 14
        assert config.send.rate_limit == 10


class TestSaveConfig:
    def test_save_config_creates_file(self, tmp_path):
        """Test that save_config creates a new config file."""
        from clerk.config import save_config

        config = ClerkConfig(
            default_account="test",
            accounts={
                "test": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.example.com", username="user"),
                    smtp=SmtpConfig(host="smtp.example.com", username="user"),
                    **{"from": FromAddress(address="user@example.com", name="Test User")},
                ),
            },
        )

        config_path = tmp_path / "config.yaml"
        save_config(config, config_path)

        assert config_path.exists()

        # Reload and verify
        loaded = load_config(config_path)
        assert loaded.default_account == "test"
        assert "test" in loaded.accounts
        assert loaded.accounts["test"].from_.address == "user@example.com"

    def test_save_config_creates_parent_dirs(self, tmp_path):
        """Test that save_config creates parent directories."""
        from clerk.config import save_config

        config = ClerkConfig()
        config_path = tmp_path / "nested" / "dir" / "config.yaml"

        save_config(config, config_path)

        assert config_path.exists()

    def test_save_config_preserves_structure(self, tmp_path):
        """Test that config round-trips correctly."""
        from clerk.config import save_config

        original = ClerkConfig(
            default_account="work",
            accounts={
                "work": AccountConfig(
                    protocol="imap",
                    imap=ImapConfig(host="imap.work.com", port=993, username="worker"),
                    smtp=SmtpConfig(host="smtp.work.com", port=587, username="worker"),
                    **{"from": FromAddress(address="me@work.com", name="Worker")},
                ),
            },
            cache=CacheConfig(window_days=14),
            send=SendConfig(rate_limit=50),
        )

        config_path = tmp_path / "config.yaml"
        save_config(original, config_path)
        loaded = load_config(config_path)

        assert loaded.default_account == original.default_account
        assert loaded.cache.window_days == 14
        assert loaded.send.rate_limit == 50


class TestOAuthTokenStorage:
    @pytest.fixture
    def mock_keyring(self, monkeypatch):
        """Mock keyring for testing."""
        storage = {}

        def mock_get_password(service, username):
            return storage.get(f"{service}:{username}")

        def mock_set_password(service, username, password):
            storage[f"{service}:{username}"] = password

        def mock_delete_password(service, username):
            key = f"{service}:{username}"
            if key not in storage:
                import keyring.errors

                raise keyring.errors.PasswordDeleteError("Not found")
            del storage[key]

        import keyring

        monkeypatch.setattr(keyring, "get_password", mock_get_password)
        monkeypatch.setattr(keyring, "set_password", mock_set_password)
        monkeypatch.setattr(keyring, "delete_password", mock_delete_password)

        return storage

    def test_save_and_get_oauth_token(self, mock_keyring):
        """Test saving and retrieving OAuth tokens."""
        from clerk.config import get_oauth_token, save_oauth_token

        token_json = '{"token": "abc123", "refresh_token": "xyz789"}'
        save_oauth_token("test-account", token_json)

        result = get_oauth_token("test-account")
        assert result == token_json

    def test_get_oauth_token_not_found(self, mock_keyring):
        """Test getting non-existent token returns None."""
        from clerk.config import get_oauth_token

        result = get_oauth_token("nonexistent")
        assert result is None

    def test_delete_oauth_token(self, mock_keyring):
        """Test deleting OAuth token."""
        from clerk.config import delete_oauth_token, get_oauth_token, save_oauth_token

        save_oauth_token("test-account", '{"token": "test"}')
        assert get_oauth_token("test-account") is not None

        delete_oauth_token("test-account")
        assert get_oauth_token("test-account") is None

    def test_delete_oauth_token_not_found(self, mock_keyring):
        """Test deleting non-existent token doesn't raise."""
        from clerk.config import delete_oauth_token

        # Should not raise
        delete_oauth_token("nonexistent")


class TestPasswordStorage:
    @pytest.fixture
    def mock_keyring(self, monkeypatch):
        """Mock keyring for testing."""
        storage = {}

        def mock_get_password(service, username):
            return storage.get(f"{service}:{username}")

        def mock_set_password(service, username, password):
            storage[f"{service}:{username}"] = password

        def mock_delete_password(service, username):
            key = f"{service}:{username}"
            if key not in storage:
                import keyring.errors

                raise keyring.errors.PasswordDeleteError("Not found")
            del storage[key]

        import keyring

        monkeypatch.setattr(keyring, "get_password", mock_get_password)
        monkeypatch.setattr(keyring, "set_password", mock_set_password)
        monkeypatch.setattr(keyring, "delete_password", mock_delete_password)

        return storage

    def test_delete_password(self, mock_keyring):
        """Test deleting password from keyring."""
        from clerk.config import delete_password, save_password

        save_password("test-account", "secret123")
        assert mock_keyring["clerk:test-account"] == "secret123"

        delete_password("test-account")
        assert "clerk:test-account" not in mock_keyring

    def test_delete_password_not_found(self, mock_keyring):
        """Test deleting non-existent password doesn't raise."""
        from clerk.config import delete_password

        # Should not raise
        delete_password("nonexistent")
