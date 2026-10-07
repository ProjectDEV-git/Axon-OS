# Diagnostics

Axon OS has a built-in health check. Use it when something isn't working, and
attach its report to bug reports.

- **Axon Settings:** click the Diagnostics button at the top left, or
  right-click Axon Settings in the app grid and choose **Diagnostics**.
- **Terminal:** run `axon-diagnose`. Add `--json` for machine-readable output,
  `-o report.txt` to save a copy, or `--only ai` (repeatable) to run one check.
  It exits with 1 when a check fails.

## What it checks

| Check | Looks at |
|-------|----------|
| `services` | Each Axon background service (`axon-brain`, `axon-search`, ...) is running |
| `ai` | Ollama answers on `localhost:11434`, and which models are installed |
| `disk` | Free space on the system disk and home (warns under 10 GB, fails under 2 GB) |
| `memory` | Available RAM (warns under 15%, fails under 5%) and swap |
| `gpu` | Graphics card; for NVIDIA, whether the driver is loaded |
| `network` | DNS and an HTTPS connection to the Axon update server |
| `updates` | Installed `axon-os` version, the Axon apt source, the daily update timer, the last automatic update's result, and pending package updates |
| `units` | Failed systemd system and user units |

Each warning or failure comes with the command that fixes or explains it.

The checks only read; nothing needs root. The report leaves out the user name,
host name and home path, so it can be shared as is. The checks live in
`services/diagnostics.py`, shared by the command and the Settings window.
