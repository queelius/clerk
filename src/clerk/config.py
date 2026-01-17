"""Configuration loading and validation for clerk."""

import os
import subprocess
from pathlib import Path
from typing import Literal

import keyring
import yaml
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


def get_config_dir() -> Path:
    """Get the configuration directory."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        return Path(xdg_config) / "clerk"
    return Path.home() / ".config" / "clerk"


def get_data_dir() -> Path:
    """Get the data directory for cache, logs, etc."""
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "clerk"
    return Path.home() / ".local" / "share" / "clerk"


class FromAddress(BaseModel):
    """Sender address configuration."""

    address: EmailStr
    name: str = ""


class ImapConfig(BaseModel):
    """IMAP server configuration."""

    host: str
    port: int = 993
    username: str
    ssl: bool = True


class SmtpConfig(BaseModel):
    """SMTP server configuration."""

    host: str
    port: int = 587
    username: str
    starttls: bool = True


class OAuthConfig(BaseModel):
    """OAuth configuration for Gmail/Google Workspace."""

    client_id_file: Path


class AccountConfig(BaseModel):
    """Configuration for a single email account."""

    protocol: Literal["imap", "gmail"] = "imap"

    imap: ImapConfig | None = None
    smtp: SmtpConfig | None = None
    oauth: OAuthConfig | None = None
    from_: FromAddress = Field(alias="from")

    # Credential retrieval options
    password_cmd: str | None = None
    password_file: Path | None = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_protocol_config(self) -> "AccountConfig":
        """Ensure required config is present for the protocol."""
        if self.protocol == "imap":
            if not self.imap:
                raise ValueError("IMAP protocol requires 'imap' configuration")
            if not self.smtp:
                raise ValueError("IMAP protocol requires 'smtp' configuration")
        elif self.protocol == "gmail":
            if not self.oauth:
                raise ValueError("Gmail protocol requires 'oauth' configuration")
        return self

    def get_password(self, account_name: str) -> str:
        """Retrieve password using configured method.

        Priority:
        1. System keyring
        2. password_cmd (shell command)
        3. password_file
        """
        # Try keyring first
        try:
            password = keyring.get_password("clerk", account_name)
            if password:
                return password
        except Exception:
            pass

        # Try password command
        if self.password_cmd:
            result = subprocess.run(
                self.password_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            raise ValueError(f"Password command failed: {result.stderr}")

        # Try password file
        if self.password_file:
            path = Path(self.password_file).expanduser()
            if not path.exists():
                raise ValueError(f"Password file not found: {path}")
            # Check permissions (should be 600)
            mode = path.stat().st_mode & 0o777
            if mode != 0o600:
                raise ValueError(
                    f"Password file {path} has insecure permissions {oct(mode)}, should be 0600"
                )
            return path.read_text().strip()

        raise ValueError(
            f"No password configured for account '{account_name}'. "
            "Set via keyring, password_cmd, or password_file."
        )


class CacheConfig(BaseModel):
    """Cache configuration."""

    window_days: int = Field(default=7, ge=1, le=365)
    inbox_freshness_min: int = Field(default=5, ge=1)
    body_freshness_min: int = Field(default=60, ge=1)


class SendConfig(BaseModel):
    """Send safety configuration."""

    require_confirmation: bool = True
    rate_limit: int = Field(default=20, ge=1, description="Max sends per hour")
    blocked_recipients: list[EmailStr] = Field(default_factory=list)


class ClerkConfig(BaseModel):
    """Root configuration for clerk."""

    default_account: str = ""
    accounts: dict[str, AccountConfig] = Field(default_factory=dict)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    send: SendConfig = Field(default_factory=SendConfig)

    @model_validator(mode="after")
    def validate_default_account(self) -> "ClerkConfig":
        """Ensure default account exists if specified."""
        if self.default_account and self.default_account not in self.accounts:
            raise ValueError(
                f"Default account '{self.default_account}' not found in accounts"
            )
        # If no default but we have accounts, use the first one
        if not self.default_account and self.accounts:
            self.default_account = next(iter(self.accounts))
        return self

    def get_account(self, name: str | None = None) -> tuple[str, AccountConfig]:
        """Get an account configuration by name or default."""
        if name is None:
            name = self.default_account
        if not name:
            raise ValueError("No account specified and no default account configured")
        if name not in self.accounts:
            raise ValueError(f"Account '{name}' not found")
        return name, self.accounts[name]


_config: ClerkConfig | None = None


def load_config(config_path: Path | None = None) -> ClerkConfig:
    """Load configuration from YAML file."""
    global _config

    if config_path is None:
        config_path = get_config_dir() / "config.yaml"

    if not config_path.exists():
        # Return empty config - user needs to set up accounts
        _config = ClerkConfig()
        return _config

    with open(config_path) as f:
        data = yaml.safe_load(f) or {}

    _config = ClerkConfig.model_validate(data)
    return _config


def get_config() -> ClerkConfig:
    """Get the current configuration, loading if necessary."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def save_password(account_name: str, password: str) -> None:
    """Save password to system keyring."""
    keyring.set_password("clerk", account_name, password)


def delete_password(account_name: str) -> None:
    """Delete password from system keyring."""
    try:
        keyring.delete_password("clerk", account_name)
    except keyring.errors.PasswordDeleteError:
        pass  # Password didn't exist


def get_oauth_token(account_name: str) -> str | None:
    """Retrieve OAuth token from keyring."""
    try:
        return keyring.get_password("clerk-oauth", account_name)
    except Exception:
        return None


def save_oauth_token(account_name: str, token_json: str) -> None:
    """Save OAuth token to keyring.

    Args:
        account_name: The account identifier
        token_json: JSON-serialized credentials (from google.oauth2.credentials)
    """
    keyring.set_password("clerk-oauth", account_name, token_json)


def delete_oauth_token(account_name: str) -> None:
    """Delete OAuth token from keyring."""
    try:
        keyring.delete_password("clerk-oauth", account_name)
    except keyring.errors.PasswordDeleteError:
        pass  # Token didn't exist


def save_config(config: ClerkConfig, config_path: Path | None = None) -> None:
    """Save configuration to YAML file.

    Args:
        config: The configuration to save
        config_path: Path to save to (defaults to standard config location)
    """
    if config_path is None:
        config_path = get_config_dir() / "config.yaml"

    # Ensure parent directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert to dict, handling the 'from' field alias
    data = config.model_dump(by_alias=True, exclude_none=True)

    with open(config_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def ensure_dirs() -> None:
    """Ensure configuration and data directories exist."""
    get_config_dir().mkdir(parents=True, exist_ok=True)
    get_data_dir().mkdir(parents=True, exist_ok=True)
    # Ensure oauth tokens directory exists
    (get_data_dir() / "oauth_tokens").mkdir(exist_ok=True)
