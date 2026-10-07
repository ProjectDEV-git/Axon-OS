# Third-party themes and apps in the Axon OS image

Everything below is installed by `build/config/chroot-setup.sh` and is free to
redistribute under the license listed. Swap anything here only for something
with an equally clear license: icon or theme packs that redraw another
company's app icons or logos (for example macOS or Windows look-alikes) are
not shipped.

| Component | Used for | Source | License |
|---|---|---|---|
| Papirus icon theme (`papirus-icon-theme`) | App, folder and system icons | Ubuntu archive | GPL-3.0 |
| WhiteSur GTK/Shell theme | Window and shell styling | github.com/vinceliuice/WhiteSur-gtk-theme (pinned commit) | MIT |
| Brave (`brave-browser`) | Default web browser | brave-browser-apt-release.s3.brave.com (signing key pinned) | MPL-2.0; "Brave" name and logo are Brave Software trademarks, shipped unmodified |
| Mission Center (`io.missioncenter.MissionCenter`) | System monitor | Flathub | GPL-3.0 |
| Ollama | Local AI runtime, installed on first boot | github.com/ollama/ollama (SHA-256 pinned) | MIT |

The WhiteSur *icon* theme was dropped because it reproduces Apple's app icons.
