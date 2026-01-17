"""OAuth module for Gmail authentication."""

import base64
import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .config import delete_oauth_token, get_oauth_token, save_oauth_token

# Gmail API scopes needed for IMAP/SMTP access
GMAIL_SCOPES = [
    "https://mail.google.com/",  # Full IMAP/SMTP access
]


def run_oauth_flow(client_id_file: Path, account_name: str) -> Credentials:
    """Run browser-based OAuth flow for Google authentication.

    Args:
        client_id_file: Path to the Google OAuth client_id.json file
        account_name: Name of the account (for storing tokens)

    Returns:
        Authenticated Credentials object
    """
    if not client_id_file.exists():
        raise FileNotFoundError(
            f"OAuth client ID file not found: {client_id_file}\n"
            "Download it from Google Cloud Console > APIs & Services > Credentials"
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_id_file),
        scopes=GMAIL_SCOPES,
    )

    # Run local server for OAuth callback
    credentials = flow.run_local_server(
        port=0,  # Let the OS pick an available port
        prompt="consent",
        success_message="Authentication successful! You can close this window.",
    )

    # Save credentials to keyring
    _save_credentials(account_name, credentials)

    return credentials


def get_gmail_credentials(account_name: str, client_id_file: Path | None = None) -> Credentials:
    """Get Gmail credentials, refreshing if needed.

    Args:
        account_name: Name of the account
        client_id_file: Path to client ID file (needed for refresh)

    Returns:
        Valid Credentials object

    Raises:
        ValueError: If no credentials found and no client_id_file provided
    """
    # Try to load from keyring
    token_json = get_oauth_token(account_name)

    if token_json:
        credentials = _load_credentials(token_json)

        # Check if credentials are valid or can be refreshed
        if credentials.valid:
            return credentials

        if credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
                _save_credentials(account_name, credentials)
                return credentials
            except Exception:
                # Refresh failed, need to re-auth
                pass

    # No valid credentials - need to run OAuth flow
    if client_id_file is None:
        raise ValueError(
            f"No valid credentials for account '{account_name}'. "
            "Run 'clerk accounts add' to authenticate."
        )

    return run_oauth_flow(client_id_file, account_name)


def get_oauth2_string(email: str, access_token: str) -> str:
    """Generate XOAUTH2 string for IMAP/SMTP authentication.

    The XOAUTH2 mechanism uses a specially formatted string that includes
    the user's email and OAuth2 access token.

    Args:
        email: User's email address
        access_token: OAuth2 access token

    Returns:
        Base64-encoded XOAUTH2 string
    """
    auth_string = f"user={email}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(auth_string.encode()).decode()


def revoke_credentials(account_name: str) -> None:
    """Revoke and delete stored credentials for an account.

    Args:
        account_name: Name of the account
    """
    delete_oauth_token(account_name)


def _save_credentials(account_name: str, credentials: Credentials) -> None:
    """Save credentials to keyring as JSON."""
    token_data = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes) if credentials.scopes else GMAIL_SCOPES,
    }
    save_oauth_token(account_name, json.dumps(token_data))


def _load_credentials(token_json: str) -> Credentials:
    """Load credentials from JSON string."""
    token_data = json.loads(token_json)
    return Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes", GMAIL_SCOPES),
    )
