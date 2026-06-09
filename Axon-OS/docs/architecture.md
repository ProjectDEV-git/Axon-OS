# Axon OS Architecture

## Overview

Axon OS is an AI-native desktop operating system built on Ubuntu 24.04 LTS with GNOME Shell. The system is designed around two centralized D-Bus services — **Axon Brain** and **Axon Context** — that provide a shared AI backbone for all desktop components. All inference runs locally through Ollama, keeping user data entirely on-device.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                          User                               │
└─────────────────────────┬───────────────────────────────────┘
                          │
            ┌─────────────▼────────────────────────┐
            │           GNOME Shell                │
            │                                      │
            │  ┌──────────┐  ┌──────────────────┐  │
            │  │Intent Bar│  │   AI Panel        │  │
            │  └────┬─────┘  └───┬──────────────┘  │
            │       │            │                 │
            │  ┌────▼────────────▼────┐  ┌──────┐  │
            │  │   Space Manager     │  │ Dock │  │
            │  │  (AI auto-routing)  │  │      │  │
            │  └──────────┬──────────┘  └──┬───┘  │
            └─────────────┼────────────────┼──────┘
                          │  D-Bus         │
         ┌────────────────┼────────────────┼──────────────┐
         │                │                │              │
┌────────▼─────────┐ ┌───▼──────────┐ ┌───▼───────────┐  │
│ org.axonos.Brain │ │ org.axonos.  │ │ AI-Native     │  │
│                  │ │   Context    │ │ Applications  │  │
│ ├ Generate()     │ │              │ │               │  │
│ ├ SendMessage()  │ │ ├ Window     │ │ ├ Terminal    │  │
│ ├ ClassifyIntent │ │ │  tracking  │ │ ├ Files       │  │
│ ├ ClassifyWindow │ │ ├ Clipboard  │ │ └ Settings    │  │
│ ├ PullModel()    │ │ │  hooks     │ └───────────────┘  │
│ ├ ListModels()   │ │ ├ Open file  │                    │
│ ├ GetStatus()    │ │ │  scanning  │                    │
│ └ Conversations  │ │ └ Terminal   │                    │
│   (SQLite CRUD)  │ │   history   │                    │
└────────┬─────────┘ └─────────────┘                    │
         │ HTTP (localhost:11434)                        │
┌────────▼─────────┐                                    │
│  Ollama Daemon   │◄───────────────────────────────────┘
│  (systemd user)  │
└────────┬─────────┘
         │ llama.cpp / CUDA / ROCm
┌────────▼─────────┐
│   GPU / CPU      │
└──────────────────┘
```

## Core Services

### Axon Brain (`org.axonos.Brain`)

The Brain is the central AI gateway for the entire operating system. It is a D-Bus session service that:

- **Wraps Ollama**: All components communicate with the Brain via D-Bus; only the Brain talks to Ollama over HTTP. This provides a single point of model management, caching, and error handling.
- **Routes tasks to models**: The Brain maps different task types to different model tiers:
  - **Speed model** (e.g., `llama3.2:1b`): Used for instant classifications — window routing, intent parsing.
  - **General model** (e.g., `llama3.2:3b`): Used for chat, summarisation, everyday queries.
  - **Deep model** (e.g., `llama3:8b`): Used for complex reasoning, code generation, deep analysis.
- **Profiles hardware**: On first launch, the [hardware_profiler.py](services/axon-brain/hardware_profiler.py) scans system RAM, GPU vendor (NVIDIA, AMD, Intel), and VRAM to recommend appropriate models for each tier.
- **Persists conversations**: Uses SQLite (`~/.axon/conversations.db`) to store chat history with full CRUD operations.
- **Streams tokens**: Supports both synchronous and streaming generation via D-Bus signals (`TokenGenerated`, `GenerationCompleted`).

| D-Bus Method | Signature | Purpose |
|-------------|-----------|---------|
| `GetStatus()` | `→ s` | JSON status: Ollama online, active models, config |
| `ListModels()` | `→ s` | JSON array of locally pulled models |
| `PullModel(name)` | `s → b` | Starts background model download, emits `PullProgress` signals |
| `Generate(prompt, context, model, stream)` | `sssb → s` | Unified text generation |
| `CreateConversation(system_prompt, title)` | `ss → s` | Creates a new conversation, returns ID |
| `SendMessage(conv_id, message, context, model, stream)` | `ssssb → s` | Sends a message in a conversation |
| `ClassifyIntent(text)` | `s → s` | Classifies natural language into system actions |
| `ClassifyWindow(title, wm_class)` | `ss → s` | Routes a window to a named workspace |

### Axon Context (`org.axonos.Context`)

The Context Engine is an ambient intelligence service that aggregates user activity into a structured JSON snapshot:

- **Window tracking**: Monitors the active window title, WM class, and position via `Wnck`.
- **Workspace tracking**: Tracks the current named Space.
- **Clipboard hooks**: Optionally captures clipboard contents.
- **File scanning**: Detects open files in code editors by inspecting `/proc/*/fd`.
- **Terminal history**: Reads recent shell commands from history files.

| D-Bus Method | Signature | Purpose |
|-------------|-----------|---------|
| `GetActiveContext()` | `→ s` | Full JSON snapshot of current desktop state |
| `GetContextString()` | `→ s` | Pre-formatted, prompt-ready context string |

## Spaces

Spaces are persistent, named workspaces with AI-powered window auto-routing. When a new window opens, the Shell extension calls `org.axonos.Brain.ClassifyWindow(title, wm_class)` to determine which Space it belongs to. The nine default spaces are:

| Space | Typical windows |
|-------|----------------|
| Code | VS Code, JetBrains, vim |
| Web | Firefox, Chrome |
| Chat | Discord, Telegram, Slack |
| Files | Nautilus, file managers |
| Media | VLC, Spotify, GIMP |
| Work | LibreOffice, Google Docs |
| Personal | Email, calendar |
| Terminal | Terminal emulators |
| Notes | Obsidian, Notion, text editors |

## Intent Bar

The Intent Bar (`apps/intent-bar/`) is a keyboard-driven command palette activated via `Super+Space`. It accepts natural-language intents and routes them through `org.axonos.Brain.ClassifyIntent()`:

- **Run command**: `{"action": "run_command", "command": "sudo apt update"}`
- **Open application**: `{"action": "open_app", "app": "firefox"}`
- **Default answer**: Plain text response displayed inline.

## AI Panel

The AI Panel (`apps/axon-ai-panel/`) is a persistent sidebar that provides a conversational interface. It:

- Calls `org.axonos.Brain.SendMessage()` with streaming enabled.
- Includes desktop context from `org.axonos.Context.GetContextString()` in every query.
- Maintains persistent conversation history via the Brain's SQLite store.

## Config Directory (`~/.axon/`)

| Path | Purpose |
|------|---------|
| `~/.axon/config.json` | Model configuration (speed, general, deep model names) |
| `~/.axon/conversations.db` | SQLite database for chat history |
| `~/.axon/hardware_profile.json` | Cached hardware profiling results |
| `~/.axon/spaces.json` | Persisted Space state |
| `~/.axon/logs/` | Application and service debug logs |

## Data Flow

1. **User types in Intent Bar** → Intent Bar calls `Brain.ClassifyIntent(text)` over D-Bus.
2. **Brain receives request** → Routes to Speed model for fast classification → Returns JSON action or plain text.
3. **User opens AI Panel** → Panel calls `Context.GetContextString()` to gather desktop state → Calls `Brain.SendMessage()` with context + user message → Brain streams tokens back via `TokenGenerated` signal.
4. **User opens a new window** → Shell extension detects `window-created` signal → Calls `Brain.ClassifyWindow(title, wm_class)` → Moves window to the appropriate Space.
