# Microsoft 365 OAuth Support

**Date**: 2026-03-03
**Status**: Approved

## Problem

SIUE (and most universities/organizations using Microsoft 365) have disabled basic IMAP/SMTP authentication. Clerk currently supports Gmail OAuth and basic IMAP auth, but not Microsoft 365's OAuth2 flow. This blocks M365 users entirely.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| App registration | Shared multi-tenant Azure AD app | Users just authenticate — no Azure portal needed. Same model as Thunderbird. |
| Auth flow | Device code flow | Works on headless/SSH machines. Microsoft-recommended for CLI tools. |
| Config model | New `microsoft365` protocol | Clean separation like Gmail. Minimal user config — hosts/ports hardcoded. |
| Library | MSAL (`msal` package) | Official Microsoft SDK. Device code flow + token refresh built-in. |

## User Experience

### Config

```yaml
accounts:
  siue:
    protocol: microsoft365
    from:
      address: atowell@siue.edu
      name: Alex Towell
```

No IMAP/SMTP hosts, ports, or credentials needed.

### Setup

```bash
clerk accounts add siue --protocol microsoft365 --email atowell@siue.edu
# Prints: "Go to https://microsoft.com/devicelogin and enter code: ABCD-EFGH"
# User authenticates in browser
# "Account 'siue' added successfully!"
```

### Re-authentication

```bash
clerk accounts auth siue
# Re-runs device code flow if tokens are invalidated
```

## Architecture

### New File: `src/clerk/microsoft365.py` (~120 lines)

- `M365_CLIENT_ID` — shared multi-tenant app registration client ID
- `M365_SCOPES` — `["https://outlook.office365.com/IMAP.AccessAsUser.All", "https://outlook.office365.com/SMTP.Send", "offline_access"]`
- `M365_AUTHORITY` — `"https://login.microsoftonline.com/common"` (multi-tenant)
- `run_device_code_flow(account_name: str) -> None` — initiates flow, stores token cache in keyring
- `get_m365_credentials(account_name: str) -> str` — returns valid access token (refreshes silently if needed)

Uses MSAL's `PublicClientApplication` with serialized token cache.

### Modified Files

**`config.py`**:
- Extend protocol: `Literal["imap", "gmail", "microsoft365"]`
- Add validation: `microsoft365` protocol only requires `from:` field
- Add `save_m365_token_cache()` / `get_m365_token_cache()` keyring helpers (service: `"clerk-m365"`)

**`imap_client.py`**:
- Add `_connect_microsoft365()` method
- Connects to `outlook.office365.com:993` with SSL
- Authenticates via `oauth2_login()` using XOAUTH2 (same mechanism as Gmail)

**`smtp_client.py`**:
- Add `_send_microsoft365()` method
- Connects to `smtp.office365.com:587` with STARTTLS
- Authenticates via XOAUTH2 (same as Gmail path)

**`cli.py`**:
- Add `_setup_microsoft365_account()` — prompts for display name, runs device code flow
- Add `microsoft365` to protocol choices in `accounts add`
- Add `clerk accounts auth <name>` subcommand for re-authentication

## Token Flow

```
1. clerk accounts add → MSAL device code flow → user authenticates in browser
2. MSAL returns access_token + refresh_token (in its token cache)
3. Serialized MSAL token cache stored in keyring ("clerk-m365", account_name)
4. On connect: get_m365_credentials() deserializes cache, acquires token silently
5. MSAL handles refresh automatically if access token expired
6. Access token passed to IMAPClient.oauth2_login() / SMTP XOAUTH2
```

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Token expired, refresh succeeds | Transparent — MSAL handles silently |
| Token expired, refresh fails | Prompt: "Re-authenticate with `clerk accounts auth <name>`" |
| Tenant blocks IMAP | "Your organization may have disabled IMAP access. Contact IT." |
| Device code timeout (15 min) | "Authentication timed out. Run `clerk accounts auth <name>` to try again." |
| Network error during auth | Standard MSAL error propagation |

## Testing

- **Unit tests**: Mock `msal.PublicClientApplication` — test token acquisition, silent refresh, cache serialization, error paths
- **CLI tests**: Mock the device code flow — test `accounts add --protocol microsoft365` and `accounts auth`
- **IMAP/SMTP tests**: Mock `IMAPClient.oauth2_login()` and SMTP XOAUTH2 — test that M365 credentials are passed correctly
- **Integration**: Manual test with real M365 account (not automated in CI)

## Dependencies

Add to `pyproject.toml`:
```
msal >= 1.24.0
```

## Open Questions

- **Shared app registration**: Need to register a multi-tenant Azure AD app and get the client ID. This is a one-time setup by the Clerk maintainer.
- **Tenant restrictions**: Some organizations may block third-party app consent. Users in those orgs would need admin consent or to register their own app (future enhancement: allow `client_id` override in config).
