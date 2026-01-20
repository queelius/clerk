# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2025-01-18

### Added
- **ClerkAPI**: New centralized API layer for all business logic
- **Search Parser**: Advanced search with operators (`from:`, `to:`, `subject:`, `body:`, `has:attachment`, `is:unread`, `is:read`, `is:flagged`, `after:`, `before:`, `date:`)
- **Interactive Shell**: New `clerk shell` command with tab completion and command history
- **Raw SQL Search**: `clerk search-sql` command for power users
- **Attachment Download**: `clerk attachment` command to list and download attachments
- **MCP Server**: `clerk_search_sql` and `clerk_attachments` tools
- **Integration Tests**: Greenmail-based tests for real IMAP/SMTP operations

### Changed
- CLI refactored to use ClerkAPI layer
- MCP server refactored to use ClerkAPI layer
- All `datetime.utcnow()` replaced with `datetime.now(timezone.utc)` (deprecation fix)

### Dependencies
- Added `prompt-toolkit>=3.0.0` for interactive shell

## [0.3.0] - 2025-01-17

### Added
- **MCP Server**: Model Context Protocol server for LLM integration
- Tools: `clerk_inbox`, `clerk_show`, `clerk_conversation`, `clerk_search`, `clerk_draft`, `clerk_send`, `clerk_drafts`, `clerk_folders`, `clerk_unread`, `clerk_move`, `clerk_delete_draft`
- Two-step send confirmation with tokens for safety

## [0.2.0] - 2025-01-16

### Added
- **Account Management**: `clerk accounts add`, `clerk accounts remove`, `clerk accounts list`, `clerk accounts test`
- **Gmail OAuth**: Full OAuth2 flow for Gmail accounts via `clerk accounts add-gmail`
- **Multi-account Support**: Switch accounts with `--account` flag
- **Draft Management**: `clerk draft new`, `clerk draft list`, `clerk draft show`, `clerk draft delete`
- **Send Emails**: `clerk send <draft-id>` with two-step confirmation
- **Folders**: `clerk folders` to list IMAP folders, `--folder` flag for operations

### Changed
- Configuration moved to `~/.config/clerk/config.toml`
- Cache moved to `~/.local/share/clerk/cache.db`

## [0.1.0] - 2025-01-15

### Added
- Initial release
- **Inbox**: `clerk inbox` with threading and caching
- **Show**: `clerk show <conv-id>` to display conversations
- **Search**: `clerk search <query>` with FTS5 full-text search
- **Cache**: SQLite-based caching with configurable TTL
- **Threading**: JWZ-style email threading by References/In-Reply-To headers
- **IMAP**: Support for standard IMAP servers
- JSON output format with `--json` flag
