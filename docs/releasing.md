# Where the ISO is hosted

**Decision (2026-10-27):** the ISO goes to **SourceForge** with a **torrent**; **GitHub Releases**
keeps the SHA-256 checksums, the SBOM and the release notes, so GitHub stays the official source.

Why: the Standard ISO is estimated at about 2.5 GB, over GitHub's 2 GiB limit per release file, and
the NVIDIA one at about 2.9 GB. SourceForge mirrors files itself. Its documentation
([Release Files for Download](https://sourceforge.net/p/forge/documentation/Release%20Files%20for%20Download/),
read 2026-10-27) sets no upload limit but says files **over 10 GB are not replicated to the mirrors**
and cannot be downloaded: ours are well under it. It recommends `rsync` for uploads over 100 MB,
which can resume an interrupted transfer.

## What goes where

| File | GitHub Release | SourceForge | Torrent |
|---|---|---|---|
| `axon-os-X.Y.Z-standard-amd64.iso`, `...-nvidia-amd64.iso` | no | yes | yes |
| `*.iso.sha256`, `*.sbom.spdx.json`, release notes | yes | optional copy | |
| `*.iso.torrent` | yes | yes | |

Release notes carry the download links, the checksums, and how to check them:

```bash
sha256sum -c axon-os-X.Y.Z-standard-amd64.iso.sha256
```

Anyone can check the file against the checksum on GitHub, whichever mirror or the torrent it came from.

## Steps for a release (once the SourceForge project exists)

```bash
# 1. Upload (resumes if interrupted); the project and its folder come from the project settings
rsync -avP --partial dist/axon-os-X.Y.Z-*.iso \
    USER@frs.sourceforge.net:/home/frs/project/PROJECT/X.Y.Z/

# 2. Torrent, with the SourceForge file as a web seed, so it works with no other seeder
mktorrent -a udp://tracker.opentrackr.org:1337/announce \
    -w https://downloads.sourceforge.net/project/PROJECT/X.Y.Z/axon-os-X.Y.Z-standard-amd64.iso \
    -o dist/axon-os-X.Y.Z-standard-amd64.iso.torrent dist/axon-os-X.Y.Z-standard-amd64.iso

# 3. Checksums, SBOM, torrents and notes to the GitHub release
gh release upload vX.Y.Z dist/*.sha256 dist/*.sbom.spdx.json dist/*.torrent --clobber
```

The tracker above is an example; pick one when setting this up and check it is still running.

## Not done yet

- The SourceForge project, its upload key (a repository secret) and the project name are not set up,
  so `build-iso.yml` still attaches the ISO to the GitHub release in `.part` pieces (`cat` joins them).
  That stays as the fallback until the steps above are automated; the workflow should then stop
  splitting and upload to SourceForge instead.
- A seeder for the torrent besides the web seed.
