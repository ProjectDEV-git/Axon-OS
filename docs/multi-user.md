# Multi-User Support in Axon OS

Axon OS follows the [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/latest/) for all user-specific data, ensuring proper multi-user support and compatibility with Linux desktop standards.

## Directory Layout

### User Data (`$XDG_DATA_HOME/axon/`)
Default: `~/.local/share/axon/`

Contains persistent user data:
- `conversations.db` — AI conversation history (SQLite)
- `semantic-index.db` — File search index with vector embeddings
- `clipboard.db` — Clipboard history
- `models/whisper/` — Downloaded voice models

### User Configuration (`$XDG_CONFIG_HOME/axon/`)
Default: `~/.config/axon/`

Contains user preferences:
- `config.toml` — Main configuration file
- `context.json` — Context service settings

### User Cache (`$XDG_CACHE_HOME/axon/`)
Default: `~/.cache/axon/`

Contains temporary data:
- Model downloads in progress
- Temporary audio files

## Privacy

All user data is stored with owner-only permissions (0o600), ensuring:
- Conversation history is private to each user
- Clipboard data is not accessible to other users
- Search indexes are per-user

## Migration from Legacy Paths

If migrating from Axon OS versions that used `~/.axon/`, move the directory:

```bash
# Move data to XDG-compliant location
mv ~/.axon ~/.local/share/axon

# Create symlink for backward compatibility (optional)
ln -s ~/.local/share/axon ~/.axon
```

## Environment Variables

Override default locations by setting:
- `XDG_DATA_HOME` — Custom data directory
- `XDG_CONFIG_HOME` — Custom config directory
- `XDG_CACHE_HOME` — Custom cache directory

Example:
```bash
export XDG_DATA_HOME="/custom/path"
# Axon services will use /custom/path/axon/
```

## Service Isolation

Each user session runs its own instance of Axon services via D-Bus session bus:
- `org.axonos.Brain` — Per-user AI inference
- `org.axonos.Context` — Per-user desktop context
- `org.axonos.Search` — Per-user file index
- `org.axonos.Voice` — Per-user voice settings

Services are automatically started on first use via D-Bus activation.
