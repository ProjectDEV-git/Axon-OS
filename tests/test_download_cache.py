"""build/config/download-cached.sh: verified downloads kept between runs, resumed, never trusted blindly."""

import hashlib
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "build" / "config" / "download-cached.sh"
PAYLOAD = bytes(range(256)) * 400  # ~100 KB

pytestmark = pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")


class Server:
    """A local HTTP server with byte-range support that remembers what it was asked."""

    def __init__(self, payload=PAYLOAD):
        outer = self
        self.requests: list[str | None] = []  # the Range header of each request
        self.payload = payload

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                rng = self.headers.get("Range")
                outer.requests.append(rng)
                data = outer.payload
                if rng and rng.startswith("bytes="):
                    start = int(rng[6:].split("-")[0])
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                    data = data[start:]
                else:
                    self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/asset.tar.zst"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    s = Server()
    yield s
    s.close()


def fetch(url, sha, cache, fallback):
    return subprocess.run(
        [
            "bash",
            "-c",
            f'source "{LIB}"; fetch_cached "$1" "$2" asset.tar.zst "$3" "$4"',
            "x",
            url,
            sha,
            str(cache),
            str(fallback),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


SHA = hashlib.sha256(PAYLOAD).hexdigest()


def test_first_run_downloads_and_second_run_uses_the_cache(server, tmp_path):
    cache, fallback = tmp_path / "cache", tmp_path / "tmp"
    fallback.mkdir()
    first = fetch(server.url, SHA, cache, fallback)
    assert first.returncode == 0, first.stderr
    path = Path(first.stdout.strip())
    assert path == cache / f"{SHA}-asset.tar.zst" and path.read_bytes() == PAYLOAD
    assert len(server.requests) == 1

    second = fetch(server.url, SHA, cache, fallback)
    assert second.returncode == 0 and second.stdout.strip() == str(path)
    assert "cached download" in second.stderr
    assert len(server.requests) == 1, "no second download"


def test_an_interrupted_download_is_resumed_by_the_next_run(server, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{SHA}-asset.tar.zst.part").write_bytes(
        PAYLOAD[:30000]
    )  # what a dropped connection left
    result = fetch(server.url, SHA, cache, tmp_path)
    assert result.returncode == 0, result.stderr
    assert server.requests == ["bytes=30000-"], "only the missing rest was asked for"
    assert Path(result.stdout.strip()).read_bytes() == PAYLOAD
    assert not list(cache.glob("*.part"))


def test_a_corrupt_cached_file_is_replaced(server, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{SHA}-asset.tar.zst").write_bytes(b"not the release")
    result = fetch(server.url, SHA, cache, tmp_path)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).read_bytes() == PAYLOAD
    assert len(server.requests) == 1


def test_a_wrong_hash_is_refused_and_leaves_nothing_cached(server, tmp_path):
    cache = tmp_path / "cache"
    wrong = "0" * 64
    result = fetch(server.url, wrong, cache, tmp_path)
    assert result.returncode == 1 and result.stdout == ""
    assert len(server.requests) == 2, "once more from scratch, then give up"
    assert list(cache.iterdir()) == []


def test_a_bad_resumed_part_gets_one_fresh_download(server, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{SHA}-asset.tar.zst.part").write_bytes(b"x" * 1000)  # from some other release
    result = fetch(server.url, SHA, cache, tmp_path)
    assert result.returncode == 0, result.stderr
    assert server.requests == ["bytes=1000-", None]
    assert Path(result.stdout.strip()).read_bytes() == PAYLOAD


def test_an_unwritable_cache_falls_back_to_the_temporary_folder(server, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("a file where the cache directory should be")
    fallback = tmp_path / "tmp"
    fallback.mkdir()
    result = fetch(server.url, SHA, blocker / "cache", fallback)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).parent == fallback


def test_the_build_mounts_a_persistent_cache_where_the_installer_looks():
    root = LIB.parent.parent.parent
    build = (root / "build" / "build.sh").read_text()
    ollama = (root / "build" / "config" / "install-ollama.sh").read_text()
    assert 'DOWNLOAD_CACHE="${BUILD_ROOT}/download-cache"' in build
    assert 'mount --bind "${DOWNLOAD_CACHE}" "${CHROOT}/var/cache/axon-downloads"' in build
    assert "/var/cache/axon-downloads" in ollama
    deb = (root / "packaging" / "build-deb.sh").read_text()
    assert "build/config/download-cached.sh" in deb, "the installer sources it from the same folder"
