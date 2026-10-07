# Updating Axon OS

Everything Axon ships that changes between releases (services, apps, the
GNOME Shell extension, theme, helper scripts and their systemd units) is one
package, `axon-os`, built by `packaging/build-deb.sh`. The ISO build installs
it, and installed systems upgrade it from the Axon apt repository:

```
https://projectdev-git.github.io/Axon-OS/apt  stable  main
```

The package itself adds that source and its signing key, so `axon-update`
(the daily timer, or the Axon OS Updater app) picks up new releases along
with Ubuntu's updates. Running Axon services restart on upgrade; the voice
service restarts at the next login.

Not in the package: the live-session installer, the GRUB and Plymouth themes,
GNOME default settings and anything else that only matters when the image is
built. Changes there still need a new ISO.

## Publishing a release

Run the **Release** workflow with the new version. After it tags the release,
it calls **Publish apt repo**, which builds the package, signs the repository
with the `APT_SIGNING_KEY` secret and pushes it to the `gh-pages` branch.
Pushing a `v*` tag yourself also publishes. The repository keeps the newest
five versions, and each package is also attached to its GitHub release.

## One-time setup

1. Create a signing key and keep the private half only as a secret:

   ```bash
   export GNUPGHOME="$(mktemp -d)"
   gpg --batch --passphrase '' --quick-gen-key "Axon OS Archive <axon-os@users.noreply.github.com>" rsa4096 sign 5y
   gpg --armor --export > packaging/axon-os-archive-keyring.asc
   gpg --armor --export-secret-keys | gh secret set APT_SIGNING_KEY --repo ProjectDEV-git/Axon-OS
   rm -rf "${GNUPGHOME}"
   ```

   Commit `packaging/axon-os-archive-keyring.asc`. Every installed system
   trusts this key, so rotating it later means shipping the new public key in
   a release signed with the old one first.

2. Enable GitHub Pages for the `gh-pages` branch (root folder) after the
   first publish creates it.

## Systems installed before the package existed

Installs from ISOs before the update channel have no `axon-os` package and
no Axon apt source. Install the package once from a release and the updater
takes over from there:

```bash
sudo apt install ./axon-os_<version>_all.deb
```
