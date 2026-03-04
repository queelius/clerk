"""Microsoft 365 OAuth module using MSAL device code flow."""

import msal  # type: ignore[import-untyped]

from .config import delete_m365_token_cache, get_m365_token_cache, save_m365_token_cache

# Shared multi-tenant Azure AD app registration
M365_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"  # Thunderbird's client ID

M365_AUTHORITY = "https://login.microsoftonline.com/common"

M365_SCOPES = [
    "https://outlook.office365.com/IMAP.AccessAsUser.All",
    "https://outlook.office365.com/SMTP.Send",
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

    result = app.acquire_token_silent(M365_SCOPES, account=accounts[0])

    if not result or "access_token" not in result:
        raise ValueError(
            f"No valid credentials for account '{account_name}'. "
            "Run 'clerk accounts auth' to re-authenticate."
        )

    if app.token_cache.has_state_changed:
        save_m365_token_cache(account_name, app.token_cache.serialize())

    return result["access_token"]  # type: ignore[no-any-return]


def run_m365_device_code_flow(account_name: str) -> None:
    """Run device code flow for Microsoft 365 authentication.

    Prints instructions for the user to authenticate in their browser.

    Raises:
        ValueError: If the flow fails
    """
    app = _build_app(account_name)
    flow = app.initiate_device_flow(scopes=M365_SCOPES)

    if "error" in flow:
        raise ValueError(
            f"Failed to initiate device code flow: {flow.get('error_description', flow['error'])}"
        )

    print(flow["message"])

    result = app.acquire_token_by_device_flow(flow)

    if "error" in result:
        raise ValueError(
            f"Authentication failed: {result.get('error_description', result['error'])}"
        )

    save_m365_token_cache(account_name, app.token_cache.serialize())


def revoke_m365_credentials(account_name: str) -> None:
    """Delete stored M365 credentials for an account."""
    delete_m365_token_cache(account_name)
