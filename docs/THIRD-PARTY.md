# Third-party themes and apps in the Axon OS image

Everything below is installed by `build/config/chroot-setup.sh` and is free to
redistribute under the license listed. Swap anything here only for something
with an equally clear license: icon or theme packs that redraw another
company's app icons or logos (for example macOS or Windows look-alikes) are
not shipped.

| Component | Used for | Source | License |
|---|---|---|---|
| Papirus icon theme (`papirus-icon-theme`) | App, folder and system icons | Ubuntu archive | GPL-3.0 |
| WhiteSur GTK/Shell theme (opt-in, `AXON_WHITESUR=1`) | Optional macOS-style window and shell styling; the default is the Axon theme | github.com/vinceliuice/WhiteSur-gtk-theme (pinned commit) | MIT |
| Brave (`brave-browser`) | Default web browser | brave-browser-apt-release.s3.brave.com (signing key pinned) | MPL-2.0; "Brave" name and logo are Brave Software trademarks, shipped unmodified |
| Steam launcher (`steam-installer`) | Game store and launcher; the Steam runtime downloads on first start | Ubuntu multiverse | Proprietary (Valve Steam Subscriber Agreement, shown by Steam on first start) |
| Ollama | Local AI runtime, installed on first boot | github.com/ollama/ollama (SHA-256 pinned) | MIT |
| APEX-WIN (`apex-win/`, vendored) | Runs Windows programs with Wine on double-click | github.com/Gaming-RF/APEX-WIN (commit in `apex-win/AXON-VENDOR.md`) | MIT |

The WhiteSur *icon* theme was dropped because it reproduces Apple's app icons.
Axon's own app icons, wallpapers, boot splash and GRUB art are original and
generated from scripts in this repository (see docs/design-system.md).
