# Axon OS

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Build & Lint](https://github.com/kaorii-ako/Axon-OS/actions/workflows/build.yml/badge.svg)](https://github.com/kaorii-ako/Axon-OS/actions/workflows/build.yml)

Axon OS is a local-first, AI-native Linux distribution built on Ubuntu 24.04 LTS. Every AI capability runs entirely on-device through Ollama — no cloud accounts, no API keys, no data leaving your machine.

The operating system is built around two centralized D-Bus services — **Axon Brain** (AI inference, model management, conversation history) and **Axon Context** (ambient desktop awareness) — that power a purpose-built GNOME Shell environment with named workspaces, an intent-driven command bar, a persistent AI side panel, and AI-native applications.

## Screenshots

> Screenshots coming soon. ISO build and desktop preview images will be added here once the first release candidate is available.

## Features

- **Axon Brain (`org.axonos.Brain`)** — A centralized D-Bus AI gateway that handles Ollama communication, model lifecycle, task-to-model routing (Speed / General / Deep tiers), conversation persistence in SQLite, and streaming token generation — accessible to every app on the system.
- **Axon Context (`org.axonos.Context`)** — An ambient context engine that tracks the active window, workspace, clipboard, open files, and terminal history, then feeds this context into AI queries for situationally-aware responses.
- **Hardware Profiler** — Automatically scans your system RAM, GPU vendor (NVIDIA / AMD / Intel), and VRAM to recommend three local models: a **Speed** tier for instant responses, a **General** tier for daily use, and a **Deep** tier for complex reasoning and coding.
- **Spaces (Super+1-9)** — Named workspaces with AI-powered window auto-routing. Open a terminal and it routes to the "Terminal" space; open VS Code and it goes to "Code". Each space carries its own context.
- **Intent Bar (Super+Space)** — A floating command bar that accepts natural-language input and maps it to system actions, app launches, and shell commands through the Brain service.
- **AI Panel (Super+A)** — A slide-in side panel with persistent, context-aware AI conversations. The panel automatically includes your desktop context (active window, open files, recent commands) in every query.
- **Axon Terminal** — An AI-powered terminal with automatic error diagnosis, natural-language-to-command conversion, and smart suggestions when commands fail.
- **GTK4 + libadwaita theming** — Native GNOME components with a custom Axon dark theme built on libadwaita for crisp HiDPI rendering.
- **Zero cloud dependency** — No telemetry, no mandatory accounts, no remote inference endpoints. All data stays on your machine in `~/.axon/`.

## Installation

### From ISO (recommended)

1. Download the latest `.iso` from the [Releases](https://github.com/kaorii-ako/Axon-OS/releases) page.
2. Flash to a USB drive (replace `/dev/sdX` with your device):
   ```bash
   sudo dd if=axon-os.iso of=/dev/sdX bs=4M status=progress oflag=sync
   ```
3. Boot from the USB and follow the guided installer.

### From Source (development / testing)

```bash
# Clone the repository
git clone https://github.com/kaorii-ako/Axon-OS.git
cd Axon-OS

# Install system dependencies
sudo apt-get install -y python3-gi python3-gi-cairo gir1.2-gtk-4.0 \
    gir1.2-adw-1 python3-dbus python3-vte-2.91

# Install Ollama (if not already installed)
curl -fsSL https://ollama.com/install.sh | sh

# Run the full installer (sets up D-Bus services, shell extension, apps)
./install.sh
```

> **Note**: Running from source requires an existing Ubuntu 24.04 GNOME session.

## Building

Full build instructions — including how to produce a bootable ISO with the custom installer and Plymouth splash — are documented in [docs/building.md](docs/building.md).

## Architecture

Axon OS uses a layered D-Bus architecture where all AI capabilities flow through two centralized services:

```
┌─────────────────────────────────────────────────────────┐
│                         User                            │
└─────────────────────────┬───────────────────────────────┘
                          │
            ┌─────────────▼──────────────┐
            │        GNOME Shell         │
            │  ┌──────────┐  ┌────────┐  │
            │  │Intent Bar│  │AI Panel│  │
            │  └────┬─────┘  └───┬────┘  │
            │       │  Spaces    │       │
            │  ┌────▼────────────▼────┐  │
            │  │   Space Manager     │  │
            │  └──────────┬──────────┘  │
            └─────────────┼─────────────┘
                          │ D-Bus (session bus)
         ┌────────────────┼────────────────┐
         │                │                │
┌────────▼─────────┐ ┌───▼─────────┐ ┌────▼──────────┐
│  org.axonos.Brain │ │org.axonos.  │ │ Axon Terminal │
│  ├─ Model Routing│ │  Context    │ │ Axon Files    │
│  ├─ Conversations│ │  ├─ Window  │ │ Axon Settings │
│  ├─ HW Profiling │ │  ├─ Clipboard│ └───────────────┘
│  └─ Streaming    │ │  └─ Files   │
└────────┬─────────┘ └─────────────┘
         │ HTTP (localhost)
┌────────▼─────────┐
│  Ollama Daemon   │
│  (localhost:11434)│
└────────┬─────────┘
         │ llama.cpp / CUDA / ROCm
┌────────▼─────────┐
│       GPU        │
└──────────────────┘
```

For the full architecture document, see [docs/architecture.md](docs/architecture.md).

## Tech Stack

| Component | Version / Detail |
|-----------|-----------------|
| Base OS | Ubuntu 24.04 LTS |
| Desktop | GNOME Shell 45+ |
| UI Toolkit | GTK4 + libadwaita |
| IPC Layer | D-Bus (session bus) |
| AI Services | `org.axonos.Brain`, `org.axonos.Context` |
| AI Runtime | Ollama (localhost) |
| Model Strategy | 3-tier: Speed / General / Deep (auto-profiled) |
| Conversation DB | SQLite (`~/.axon/conversations.db`) |
| Shell Extension | GJS (GNOME JavaScript) |
| Apps | Python 3.11+ with GTK4 |

## Project Structure

```
Axon-OS/
├── apps/
│   ├── axon-ai-panel/     # AI conversation side panel
│   ├── axon-terminal/     # AI-powered terminal
│   ├── axon-welcome/      # First-boot welcome & model setup wizard
│   └── intent-bar/        # Natural language command palette
├── services/
│   ├── axon-brain/        # Central AI D-Bus service
│   └── axon-context/      # Ambient desktop context engine
├── shell/
│   └── axon-shell/        # GNOME Shell extension (dock, spaces, intent bar)
├── theme/                 # GTK4 dark theme
├── installer/             # Calamares-based installer
├── plymouth/              # Boot splash
├── build/                 # ISO build scripts
├── tests/                 # Integration and unit tests
└── docs/                  # Architecture and build documentation
```

## Contributing

Contributions are welcome. Please open an issue first to discuss significant changes. For smaller fixes, a pull request with a clear description is sufficient. All Python code must pass `ruff check` and all shell scripts must pass `shellcheck` before merging (enforced by CI).

1. Fork the repository and create a feature branch.
2. Make your changes, ensuring CI passes locally where possible.
3. Open a pull request against `main`.

## License

Axon OS is released under the [MIT License](LICENSE).  
Copyright (c) 2024 Axon OS Contributors.
