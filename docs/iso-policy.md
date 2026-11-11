# ISO policy: what goes in the ISO vs. what downloads later

A package goes **into the ISO** only if **at least one** of these is true **and** its license
allows redistribution:

- **A. Boot / install / connect:** needed to boot, install, or get online on supported hardware
  (kernel, firmware, network stack, installer, GPU base drivers for that edition).
- **B. Axon identity:** a core Axon OS component (Brain, Context, Search, Voice, GuiAgent, shell
  extension, Axon apps, Ollama **CPU** runtime, updater, watchdog).
- **C. Headline experience offline:** required for "gaming works out of the box" without internet
  (Steam launcher, Vulkan, 32-bit graphics libs, GameMode, MangoHud, APEX-WIN/Wine).

**Everything else downloads later** (Game Center, welcome wizard, Hardware Profiler, or the app's
own first-run updater). Never in the ISO: AI models; Ollama GPU libraries (CUDA, ROCm); Flatpak
runtimes and Flatpak apps; launchers other than Steam; emulators, streaming/chat/recording/RGB
tools; Proton-GE/Wine-GE, Wine Mono/Gecko; anything needing a EULA click-through; optional media
codecs (a welcome-wizard checkbox); extra language packs.

Every download-later item must: be listed in `data/gaming/catalog.json` with size, source and
category; install without a terminal; be removable from the same UI; have a verified source
(Ubuntu/Axon apt, Flathub, or a pinned URL + SHA-256); fail gracefully offline with a retry action.

ISO size budgets (enforced in CI): Standard target ≤ 3.5 GB, hard limit 3.9 GB; NVIDIA edition
target ≤ 4.3 GB, hard limit 4.7 GB. A PR that adds more than **50 MB** to either ISO must say why
and which rule (A, B or C) justifies it. No `curl | sh` in shipped code: use apt, Flatpak, or a
pinned URL + SHA-256. Verify external identifiers (package names, Flatpak IDs, PPAs) when you
use them and record the date in a comment.
