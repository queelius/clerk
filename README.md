# clerk

A thin CLI for LLM agents to interact with email via IMAP/SMTP.

## Installation

```bash
pip install -e .
```

## Quick Start

Configure your account in `~/.config/clerk/config.yaml`:

```yaml
default_account: personal

accounts:
  personal:
    protocol: imap
    imap:
      host: imap.fastmail.com
      port: 993
      username: user@fastmail.com
    smtp:
      host: smtp.fastmail.com
      port: 587
      username: user@fastmail.com
    from:
      address: user@fastmail.com
      name: "User Name"
```

Then set your password:
```bash
# Using keyring
python -c "import keyring; keyring.set_password('clerk', 'personal', 'your-password')"
```

## Usage

```bash
# List inbox
clerk inbox
clerk inbox --json

# Show a conversation
clerk show <conv-id>

# Search messages
clerk search "from:alice project"

# Create and send drafts
clerk draft create --to bob@example.com --subject "Hello" --body "Hi there"
clerk send <draft-id>
```

See SPEC.md for full documentation.
