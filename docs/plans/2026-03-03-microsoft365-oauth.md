# Microsoft 365 OAuth Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Microsoft 365 OAuth2 support so M365 email accounts (like SIUE) can authenticate via device code flow.

**Architecture:** New `microsoft365` protocol type alongside existing `imap` and `gmail`. Uses MSAL library for device code flow + token caching. Tokens stored in keyring. IMAP/SMTP auth via XOAUTH2 (same mechanism as Gmail). New `src/clerk/microsoft365.py` module, modifications to `config.py`, `imap_client.py`, `smtp_client.py`, and `cli.py`.

**Tech Stack:** `msal` (Microsoft Authentication Library), existing `imapclient`, `aiosmtplib`, `keyring`

---

### Task 1: Add `msal` dependency

**Files:**
- Modify: `pyproject.toml:31-44` (dependencies list)

**Step 1: Add msal to dependencies**

In `pyproject.toml`, add `msal` to the `dependencies` list (after the `google-auth-oauthlib` line):

```toml
    "msal>=1.24.0",
```

**Step 2: Install updated dependencies**

Run: `pip install -e ".[dev]"`
Expected: Success, msal installed

**Step 3: Verify import**

Run: `python -c "import msal; print(msal.__version__)"`
Expected: Prints version >= 1.24.0

**Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat(deps): add msal for Microsoft 365 OAuth support"
```

---

### Task 2: Extend config model for `microsoft365` protocol

**Files:**
- Modify: `src/clerk/config.py:64` (protocol Literal)
- Modify: `src/clerk/config.py:77-88` (validate_protocol_config)
- Add keyring helpers after line 247
- Test: `tests/test_config.py`

**Step 1: Write failing tests for microsoft365 config**

Add to `tests/test_config.py` in `TestAccountConfig`:

```python
    def test_microsoft365_account(self):
        acc = AccountConfig(
            protocol="microsoft365",
            **{"from": FromAddress(address="user@siue.edu")},
        )
        assert acc.protocol == "microsoft365"
        assert acc.imap is None
        assert acc.smtp is None
        assert acc.oauth is None

    def test_microsoft365_no_imap_smtp_required(self):
        """microsoft365 protocol should NOT require imap/smtp config."""
        # Should not raise
        AccountConfig(
            protocol="microsoft365",
            **{"from": FromAddress(address="user@org.edu")},
        )
```

Add new test class for M365 token storage:

```python
class TestM365TokenStorage:
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

    def test_save_and_get_m365_token_cache(self, mock_keyring):
        from clerk.config import get_m365_token_cache, save_m365_token_cache

        cache_data = '{"AccessToken": {"key": "value"}}'
        save_m365_token_cache("test-account", cache_data)

        result = get_m365_token_cache("test-account")
        assert result == cache_data

    def test_get_m365_token_cache_not_found(self, mock_keyring):
        from clerk.config import get_m365_token_cache

        result = get_m365_token_cache("nonexistent")
        assert result is None

    def test_delete_m365_token_cache(self, mock_keyring):
        from clerk.config import delete_m365_token_cache, get_m365_token_cache, save_m365_token_cache

        save_m365_token_cache("test-account", '{"token": "test"}')
        assert get_m365_token_cache("test-account") is not None

        delete_m365_token_cache("test-account")
        assert get_m365_token_cache("test-account") is None

    def test_delete_m365_token_cache_not_found(self, mock_keyring):
        from clerk.config import delete_m365_token_cache

        # Should not raise
        delete_m365_token_cache("nonexistent")
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v -k "microsoft365 or m365"`
Expected: FAIL — `microsoft365` not a valid protocol value, functions not defined

**Step 3: Implement config changes**

In `src/clerk/config.py`:

1. Change line 64 from:
```python
    protocol: Literal["imap", "gmail"] = "imap"
```
to:
```python
    protocol: Literal["imap", "gmail", "microsoft365"] = "imap"
```

2. Add `microsoft365` validation to `validate_protocol_config` (after the `elif self.protocol == "gmail"` block at line 87):
```python
        # microsoft365 only requires 'from' (which is always required)
        # No additional config blocks needed
```

3. Add keyring helpers after `delete_oauth_token()` (after line 247):
```python
def get_m365_token_cache(account_name: str) -> str | None:
    """Retrieve M365 MSAL token cache from keyring."""
    try:
        return keyring.get_password("clerk-m365", account_name)
    except Exception:
        return None


def save_m365_token_cache(account_name: str, cache_data: str) -> None:
    """Save M365 MSAL token cache to keyring."""
    keyring.set_password("clerk-m365", account_name, cache_data)


def delete_m365_token_cache(account_name: str) -> None:
    """Delete M365 MSAL token cache from keyring."""
    with contextlib.suppress(keyring.errors.PasswordDeleteError):
        keyring.delete_password("clerk-m365", account_name)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v -k "microsoft365 or m365"`
Expected: All PASS

**Step 5: Run full config test suite**

Run: `pytest tests/test_config.py -v`
Expected: All PASS (no regressions)

**Step 6: Commit**

```bash
git add src/clerk/config.py tests/test_config.py
git commit -m "feat(config): add microsoft365 protocol type and M365 keyring helpers"
```

---

### Task 3: Create `microsoft365.py` module

**Files:**
- Create: `src/clerk/microsoft365.py`
- Test: `tests/test_microsoft365.py`

**Step 1: Write tests for microsoft365 module**

Create `tests/test_microsoft365.py`:

```python
"""Tests for Microsoft 365 OAuth module."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestM365Constants:
    def test_scopes_include_imap(self):
        from clerk.microsoft365 import M365_SCOPES
        assert "https://outlook.office365.com/IMAP.AccessAsUser.All" in M365_SCOPES

    def test_scopes_include_smtp(self):
        from clerk.microsoft365 import M365_SCOPES
        assert "https://outlook.office365.com/SMTP.Send" in M365_SCOPES

    def test_scopes_include_offline_access(self):
        from clerk.microsoft365 import M365_SCOPES
        assert "offline_access" in M365_SCOPES

    def test_authority_is_common(self):
        from clerk.microsoft365 import M365_AUTHORITY
        assert "common" in M365_AUTHORITY

    def test_client_id_is_set(self):
        from clerk.microsoft365 import M365_CLIENT_ID
        assert M365_CLIENT_ID  # non-empty


class TestGetM365AccessToken:
    @patch("clerk.microsoft365.get_m365_token_cache")
    @patch("clerk.microsoft365.save_m365_token_cache")
    @patch("clerk.microsoft365.msal.PublicClientApplication")
    def test_acquire_token_silent_success(self, mock_app_cls, mock_save, mock_get_cache):
        """Test returning token when silent acquisition succeeds."""
        from clerk.microsoft365 import get_m365_access_token

        # Set up cached token data
        mock_get_cache.return_value = '{"cached": true}'

        # Set up MSAL app
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@siue.edu"}]
        mock_app.acquire_token_silent.return_value = {
            "access_token": "fresh_token_123",
        }

        result = get_m365_access_token("test-account")

        assert result == "fresh_token_123"
        mock_app.acquire_token_silent.assert_called_once()
        mock_save.assert_called_once()  # cache updated

    @patch("clerk.microsoft365.get_m365_token_cache")
    @patch("clerk.microsoft365.msal.PublicClientApplication")
    def test_no_cache_no_accounts_raises(self, mock_app_cls, mock_get_cache):
        """Test error when no cached tokens and no accounts."""
        from clerk.microsoft365 import get_m365_access_token

        mock_get_cache.return_value = None
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = []

        with pytest.raises(ValueError, match="No valid credentials"):
            get_m365_access_token("test-account")

    @patch("clerk.microsoft365.get_m365_token_cache")
    @patch("clerk.microsoft365.msal.PublicClientApplication")
    def test_silent_acquire_fails_raises(self, mock_app_cls, mock_get_cache):
        """Test error when silent token acquisition fails."""
        from clerk.microsoft365 import get_m365_access_token

        mock_get_cache.return_value = '{"cached": true}'
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@siue.edu"}]
        mock_app.acquire_token_silent.return_value = None

        with pytest.raises(ValueError, match="No valid credentials"):
            get_m365_access_token("test-account")


class TestRunM365DeviceCodeFlow:
    @patch("clerk.microsoft365.save_m365_token_cache")
    @patch("clerk.microsoft365.msal.PublicClientApplication")
    def test_successful_flow(self, mock_app_cls, mock_save):
        """Test successful device code flow."""
        from clerk.microsoft365 import run_m365_device_code_flow

        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app

        # Mock device code initiation
        mock_app.initiate_device_flow.return_value = {
            "user_code": "ABCD-EFGH",
            "message": "Go to https://microsoft.com/devicelogin and enter code ABCD-EFGH",
        }

        # Mock token acquisition
        mock_app.acquire_token_by_device_flow.return_value = {
            "access_token": "new_token",
        }

        # Mock cache serialization
        mock_cache = MagicMock()
        mock_app.token_cache = mock_cache
        mock_cache.serialize.return_value = '{"serialized": true}'

        run_m365_device_code_flow("test-account")

        mock_app.initiate_device_flow.assert_called_once()
        mock_app.acquire_token_by_device_flow.assert_called_once()
        mock_save.assert_called_once_with("test-account", '{"serialized": true}')

    @patch("clerk.microsoft365.msal.PublicClientApplication")
    def test_flow_initiation_failure(self, mock_app_cls):
        """Test error when device flow initiation fails."""
        from clerk.microsoft365 import run_m365_device_code_flow

        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.initiate_device_flow.return_value = {
            "error": "authorization_pending",
            "error_description": "Something went wrong",
        }

        with pytest.raises(ValueError, match="device code flow"):
            run_m365_device_code_flow("test-account")

    @patch("clerk.microsoft365.msal.PublicClientApplication")
    def test_token_acquisition_failure(self, mock_app_cls):
        """Test error when token acquisition fails."""
        from clerk.microsoft365 import run_m365_device_code_flow

        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.initiate_device_flow.return_value = {
            "user_code": "ABCD-EFGH",
            "message": "Go to device login",
        }
        mock_app.acquire_token_by_device_flow.return_value = {
            "error": "authorization_declined",
            "error_description": "User declined",
        }

        with pytest.raises(ValueError, match="User declined"):
            run_m365_device_code_flow("test-account")


class TestRevokeM365Credentials:
    @patch("clerk.microsoft365.delete_m365_token_cache")
    def test_revoke(self, mock_delete):
        """Test revoking M365 credentials."""
        from clerk.microsoft365 import revoke_m365_credentials

        revoke_m365_credentials("test-account")
        mock_delete.assert_called_once_with("test-account")
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_microsoft365.py -v`
Expected: FAIL — module `clerk.microsoft365` not found

**Step 3: Implement `src/clerk/microsoft365.py`**

```python
"""Microsoft 365 OAuth module using MSAL device code flow."""

import msal

from .config import delete_m365_token_cache, get_m365_token_cache, save_m365_token_cache

# Shared multi-tenant Azure AD app registration
# Registered as a public client app with IMAP/SMTP delegated permissions
M365_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"  # Thunderbird's client ID

M365_AUTHORITY = "https://login.microsoftonline.com/common"

M365_SCOPES = [
    "https://outlook.office365.com/IMAP.AccessAsUser.All",
    "https://outlook.office365.com/SMTP.Send",
    "offline_access",
]


def _build_app(account_name: str) -> msal.PublicClientApplication:
    """Build an MSAL app with optional cached token data."""
    cache = msal.SerializableTokenCache()

    cached_data = get_m365_token_cache(account_name)
    if cached_data:
        cache.deserialize(cached_data)

    return msal.PublicClientApplication(
        M365_CLIENT_ID,
        authority=M365_AUTHORITY,
        token_cache=cache,
    )


def get_m365_access_token(account_name: str) -> str:
    """Get a valid M365 access token, refreshing silently if needed.

    Args:
        account_name: Name of the account

    Returns:
        Valid access token string

    Raises:
        ValueError: If no valid credentials and re-auth is needed
    """
    app = _build_app(account_name)
    accounts = app.get_accounts()

    if not accounts:
        raise ValueError(
            f"No valid credentials for account '{account_name}'. "
            "Run 'clerk accounts auth' to re-authenticate."
        )

    # Try silent token acquisition (uses refresh token)
    result = app.acquire_token_silent(M365_SCOPES, account=accounts[0])

    if not result or "access_token" not in result:
        raise ValueError(
            f"No valid credentials for account '{account_name}'. "
            "Run 'clerk accounts auth' to re-authenticate."
        )

    # Persist updated cache (refresh token may have rotated)
    if app.token_cache.has_state_changed:
        save_m365_token_cache(account_name, app.token_cache.serialize())

    return result["access_token"]


def run_m365_device_code_flow(account_name: str) -> None:
    """Run device code flow for Microsoft 365 authentication.

    Prints instructions for the user to authenticate in their browser.

    Args:
        account_name: Name of the account (for storing tokens)

    Raises:
        ValueError: If the flow fails
    """
    app = _build_app(account_name)

    flow = app.initiate_device_flow(scopes=M365_SCOPES)

    if "error" in flow:
        raise ValueError(
            f"Failed to initiate device code flow: {flow.get('error_description', flow['error'])}"
        )

    # Print instructions for the user
    print(flow["message"])

    # Block until user completes authentication (or timeout)
    result = app.acquire_token_by_device_flow(flow)

    if "error" in result:
        raise ValueError(
            f"Authentication failed: {result.get('error_description', result['error'])}"
        )

    # Save token cache to keyring
    save_m365_token_cache(account_name, app.token_cache.serialize())


def revoke_m365_credentials(account_name: str) -> None:
    """Delete stored M365 credentials for an account."""
    delete_m365_token_cache(account_name)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_microsoft365.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/clerk/microsoft365.py tests/test_microsoft365.py
git commit -m "feat(microsoft365): add MSAL device code flow module"
```

---

### Task 4: Add M365 IMAP connection

**Files:**
- Modify: `src/clerk/imap_client.py:196-236` (connect method + new _connect_microsoft365)
- Test: existing tests + new test

**Step 1: Write failing test**

Add to `tests/test_config.py` or create a focused test. Since imap_client tests typically mock IMAPClient, add a test at the end of an appropriate test file. Add to a new file `tests/test_imap_m365.py`:

```python
"""Tests for Microsoft 365 IMAP connection."""

from unittest.mock import MagicMock, patch

from clerk.config import AccountConfig, FromAddress
from clerk.imap_client import ImapClient


class TestImapM365Connection:
    def _make_m365_config(self) -> AccountConfig:
        return AccountConfig(
            protocol="microsoft365",
            **{"from": FromAddress(address="user@siue.edu", name="Test User")},
        )

    @patch("clerk.imap_client.IMAPClient")
    @patch("clerk.imap_client.get_m365_access_token")
    def test_connect_microsoft365(self, mock_get_token, mock_imap_cls):
        """Test connecting to M365 via XOAUTH2."""
        mock_get_token.return_value = "m365_access_token"
        mock_client = MagicMock()
        mock_imap_cls.return_value = mock_client

        config = self._make_m365_config()
        client = ImapClient("siue", config)
        client.connect()

        # Verify connection to outlook.office365.com
        mock_imap_cls.assert_called_once_with(
            "outlook.office365.com", port=993, ssl=True
        )

        # Verify XOAUTH2 login
        mock_client.oauth2_login.assert_called_once_with(
            "user@siue.edu", "m365_access_token"
        )

    @patch("clerk.imap_client.IMAPClient")
    @patch("clerk.imap_client.get_m365_access_token")
    def test_connect_microsoft365_token_error(self, mock_get_token, mock_imap_cls):
        """Test error handling when token acquisition fails."""
        mock_get_token.side_effect = ValueError("No valid credentials")

        config = self._make_m365_config()
        client = ImapClient("siue", config)

        with __import__("pytest").raises(ValueError, match="No valid credentials"):
            client.connect()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_imap_m365.py -v`
Expected: FAIL — `_connect_microsoft365` not defined, import error

**Step 3: Implement M365 IMAP connection**

In `src/clerk/imap_client.py`:

1. Update `connect()` method (around line 196) to add the `microsoft365` dispatch:

Replace:
```python
    def connect(self) -> None:
        """Connect to the IMAP server."""
        if self._client is not None:
            return

        if self.config.protocol == "gmail":
            self._connect_gmail()
        else:
            self._connect_imap()
```

With:
```python
    def connect(self) -> None:
        """Connect to the IMAP server."""
        if self._client is not None:
            return

        if self.config.protocol == "gmail":
            self._connect_gmail()
        elif self.config.protocol == "microsoft365":
            self._connect_microsoft365()
        else:
            self._connect_imap()
```

2. Add `_connect_microsoft365()` method after `_connect_gmail()` (after line 236):

```python
    def _connect_microsoft365(self) -> None:
        """Connect to Microsoft 365 using OAuth2 XOAUTH2 authentication."""
        from .microsoft365 import get_m365_access_token

        access_token = get_m365_access_token(self.account_name)

        self._client = IMAPClient("outlook.office365.com", port=993, ssl=True)

        email = self.config.from_.address
        self._client.oauth2_login(email, access_token)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_imap_m365.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/clerk/imap_client.py tests/test_imap_m365.py
git commit -m "feat(imap): add Microsoft 365 XOAUTH2 connection"
```

---

### Task 5: Add M365 SMTP sending

**Files:**
- Modify: `src/clerk/smtp_client.py:109-131` (_send_async dispatch + new _send_microsoft365)
- Test: `tests/test_smtp_m365.py`

**Step 1: Write failing test**

Create `tests/test_smtp_m365.py`:

```python
"""Tests for Microsoft 365 SMTP sending."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clerk.config import AccountConfig, FromAddress
from clerk.models import Address, Draft
from clerk.smtp_client import SmtpClient


class TestSmtpM365:
    def _make_m365_config(self) -> AccountConfig:
        return AccountConfig(
            protocol="microsoft365",
            **{"from": FromAddress(address="user@siue.edu", name="Test User")},
        )

    def _make_draft(self) -> Draft:
        return Draft(
            account="siue",
            to=[Address(addr="recipient@example.com", name="Recipient")],
            subject="Test Subject",
            body_text="Test body",
        )

    @patch("clerk.smtp_client.aiosmtplib.SMTP")
    @patch("clerk.smtp_client.get_m365_access_token")
    @pytest.mark.asyncio
    async def test_send_microsoft365(self, mock_get_token, mock_smtp_cls):
        """Test sending via M365 SMTP with XOAUTH2."""
        mock_get_token.return_value = "m365_token"

        mock_smtp = AsyncMock()
        mock_smtp_cls.return_value = mock_smtp

        config = self._make_m365_config()
        client = SmtpClient("siue", config)
        draft = self._make_draft()

        result = await client._send_async(draft)

        assert result.success
        mock_smtp_cls.assert_called_once_with(
            hostname="smtp.office365.com", port=587, start_tls=True
        )
        mock_smtp.connect.assert_called_once()
        mock_smtp.ehlo.assert_called_once()
        mock_smtp.starttls.assert_called_once()

    @patch("clerk.smtp_client.aiosmtplib.SMTP")
    @patch("clerk.smtp_client.get_m365_access_token")
    @pytest.mark.asyncio
    async def test_send_microsoft365_auth_error(self, mock_get_token, mock_smtp_cls):
        """Test handling of M365 SMTP auth failure."""
        import aiosmtplib

        mock_get_token.return_value = "bad_token"
        mock_smtp = AsyncMock()
        mock_smtp_cls.return_value = mock_smtp
        mock_smtp.auth.side_effect = aiosmtplib.SMTPAuthenticationError(
            535, b"Authentication failed"
        )

        config = self._make_m365_config()
        client = SmtpClient("siue", config)
        draft = self._make_draft()

        result = await client._send_async(draft)

        assert not result.success
        assert "Authentication failed" in result.error
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_smtp_m365.py -v`
Expected: FAIL — `_send_microsoft365` not defined

**Step 3: Implement M365 SMTP sending**

In `src/clerk/smtp_client.py`:

1. Update `_send_async()` dispatch (around line 119):

Replace:
```python
            if self.config.protocol == "gmail":
                await self._send_gmail(msg)
            else:
                await self._send_imap(msg)
```

With:
```python
            if self.config.protocol == "gmail":
                await self._send_gmail(msg)
            elif self.config.protocol == "microsoft365":
                await self._send_microsoft365(msg)
            else:
                await self._send_imap(msg)
```

2. Add `_send_microsoft365()` after `_send_gmail()` (after line 177):

```python
    async def _send_microsoft365(self, msg: MIMEMultipart) -> None:
        """Send via Microsoft 365 SMTP with XOAUTH2 authentication."""
        from .microsoft365 import get_m365_access_token
        from .oauth import get_oauth2_string

        access_token = get_m365_access_token(self.account_name)
        email_addr = self.config.from_.address
        oauth2_string = get_oauth2_string(email_addr, access_token)

        smtp = aiosmtplib.SMTP(hostname="smtp.office365.com", port=587, start_tls=True)
        await smtp.connect()
        await smtp.ehlo()
        await smtp.starttls()
        await smtp.auth("XOAUTH2", oauth2_string)
        await smtp.send_message(msg)
        await smtp.quit()
```

Note: We reuse `get_oauth2_string()` from `oauth.py` since XOAUTH2 format is the same for both Gmail and M365.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_smtp_m365.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/clerk/smtp_client.py tests/test_smtp_m365.py
git commit -m "feat(smtp): add Microsoft 365 XOAUTH2 sending"
```

---

### Task 6: Add CLI setup and auth commands

**Files:**
- Modify: `src/clerk/cli.py` (accounts_add, _setup_microsoft365_account, accounts_test, accounts_remove)

**Step 1: Add `_setup_microsoft365_account()` function**

Add after `_setup_gmail_account()` (after line 966 in cli.py):

```python
def _setup_microsoft365_account(name: str, email: str) -> AccountConfig:
    """Set up a Microsoft 365 account with device code flow."""
    console.print("[bold]Microsoft 365 OAuth Setup[/bold]")
    console.print(
        "You'll authenticate using your browser.\n"
        "No additional setup is needed — just sign in with your Microsoft account.\n"
    )

    # Display name
    display_name = typer.prompt("Display name (optional)", default="")

    # Create account config
    account_config = AccountConfig(
        protocol="microsoft365",
        **{"from": FromAddress(address=email, name=display_name)},
    )

    # Run device code flow
    if typer.confirm("\nAuthenticate now?", default=True):
        try:
            from .microsoft365 import run_m365_device_code_flow

            console.print()
            run_m365_device_code_flow(name)
            console.print("\n[green]Authentication successful![/green]")
        except Exception as e:
            console.print(f"\n[yellow]Authentication failed: {e}[/yellow]")
            console.print("You can try again later with 'clerk accounts auth'.")

    return account_config
```

**Step 2: Update `accounts_add` to accept `microsoft365` protocol**

In `accounts_add()`, change the protocol validation (line 852):

Replace:
```python
    if protocol not in ("imap", "gmail"):
        exit_with_code(ExitCode.INVALID_INPUT, f"Unknown protocol: {protocol}. Use 'imap' or 'gmail'")
```

With:
```python
    if protocol not in ("imap", "gmail", "microsoft365"):
        exit_with_code(ExitCode.INVALID_INPUT, f"Unknown protocol: {protocol}. Use 'imap', 'gmail', or 'microsoft365'")
```

And update the dispatch (around line 866):

Replace:
```python
    if protocol == "gmail":
        account_config = _setup_gmail_account(name, email)
    else:
        account_config = _setup_imap_account(name, email)
```

With:
```python
    if protocol == "gmail":
        account_config = _setup_gmail_account(name, email)
    elif protocol == "microsoft365":
        account_config = _setup_microsoft365_account(name, email)
    else:
        account_config = _setup_imap_account(name, email)
```

**Step 3: Add `accounts auth` subcommand**

Add after `accounts_remove` (after line 1068):

```python
@accounts_app.command(name="auth")
def accounts_auth(
    name: Annotated[str, typer.Argument(help="Account name to authenticate")],
) -> None:
    """Re-authenticate an account (run OAuth flow again)."""
    ensure_dirs()
    config = get_config()

    if name not in config.accounts:
        exit_with_code(ExitCode.NOT_FOUND, f"Account '{name}' not found")

    account_config = config.accounts[name]

    if account_config.protocol == "gmail":
        from .oauth import run_oauth_flow

        if not account_config.oauth:
            exit_with_code(ExitCode.CONFIG_ERROR, "Gmail account missing OAuth configuration")

        console.print("[dim]Opening browser for Google authentication...[/dim]")
        try:
            run_oauth_flow(account_config.oauth.client_id_file, name)
            console.print("[green]Authentication successful![/green]")
        except Exception as e:
            exit_with_code(ExitCode.CONNECTION_ERROR, f"Authentication failed: {e}")

    elif account_config.protocol == "microsoft365":
        from .microsoft365 import run_m365_device_code_flow

        console.print("[bold]Microsoft 365 Re-authentication[/bold]\n")
        try:
            run_m365_device_code_flow(name)
            console.print("\n[green]Authentication successful![/green]")
        except Exception as e:
            exit_with_code(ExitCode.CONNECTION_ERROR, f"Authentication failed: {e}")

    else:
        exit_with_code(
            ExitCode.INVALID_INPUT,
            f"Account '{name}' uses password authentication. "
            "Use 'clerk accounts set-password' to update the password."
        )
```

**Step 4: Update `accounts_test` for microsoft365**

In `accounts_test()`, update the SMTP test section (around line 994):

Replace:
```python
    # Test SMTP (only for IMAP protocol, Gmail uses same OAuth)
    if account_config.protocol == "imap":
```

With:
```python
    # Test SMTP (only for IMAP protocol, Gmail/M365 use same OAuth)
    if account_config.protocol == "imap":
```

And after line 1023 (`console.print("[green]SMTP: Uses same OAuth credentials[/green]")`), the existing else branch already covers gmail. It will now also cover microsoft365 since both are "not imap". No change needed here — the else branch already prints the right message.

**Step 5: Update `accounts_remove` for microsoft365**

In `accounts_remove()` (around line 1052):

Replace:
```python
    # Delete credentials
    if account_config.protocol == "gmail":
        delete_oauth_token(name)
    else:
        delete_password(name)
```

With:
```python
    # Delete credentials
    if account_config.protocol == "gmail":
        delete_oauth_token(name)
    elif account_config.protocol == "microsoft365":
        from .config import delete_m365_token_cache
        delete_m365_token_cache(name)
    else:
        delete_password(name)
```

**Step 6: Add necessary imports to cli.py**

Add `FromAddress` to the existing import from `.config` at the top of cli.py (it may already be imported — check first).

**Step 7: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

**Step 8: Commit**

```bash
git add src/clerk/cli.py
git commit -m "feat(cli): add microsoft365 account setup, auth command, and test/remove support"
```

---

### Task 7: Type checking and linting

**Files:**
- All modified files

**Step 1: Run mypy**

Run: `mypy src`
Expected: 0 errors (fix any type issues)

**Step 2: Run ruff**

Run: `ruff check src tests`
Expected: 0 errors (fix any lint issues)

**Step 3: Run full test suite with coverage**

Run: `pytest --cov=clerk --cov-report=term-missing tests/`
Expected: All pass, good coverage on new code

**Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve type and lint issues in M365 implementation"
```

---

### Task 8: Update SIUE config entry

**Files:**
- Modify: `~/.config/clerk/config.yaml` (the user's live config)

**Step 1: Update the siue account entry**

The entry we added earlier used `protocol: imap` with basic auth. Update it to:

```yaml
  siue:
    protocol: microsoft365
    from:
      address: atowell@siue.edu
      name: Alex Towell
```

Remove the `imap:` and `smtp:` blocks from the siue entry.

**Step 2: Authenticate**

Run: `clerk accounts auth siue`
Expected: Device code flow prints URL + code, user authenticates in browser

**Step 3: Test connectivity**

Run: `clerk accounts test siue`
Expected: IMAP and SMTP show green/connected
