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
