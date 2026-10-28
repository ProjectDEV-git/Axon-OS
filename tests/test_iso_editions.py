"""ISO editions: package lists, the NVIDIA metapackage, build options and the size budget."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LISTS = ROOT / "build" / "package-lists"
SCRIPT = ROOT / "scripts" / "check-iso-size.sh"
GPU_HELPER = ROOT / "build" / "config" / "install-ollama-gpu.sh"


def packages(name: str) -> list[str]:
    lines = (LISTS / f"{name}.list").read_text().splitlines()
    return [ln.strip() for ln in lines if not re.match(r"^\s*(#|$)", ln)]


def control(name: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in (ROOT / "packaging" / name / "control").read_text().splitlines():
        if line and not line.startswith(" ") and ":" in line:
            key, _, value = line.partition(":")
            fields[key] = value.strip()
    return fields


# -- package lists -------------------------------------------------------------------------


@pytest.mark.unit
def test_each_edition_has_a_list_of_bare_package_names():
    for name in ("common", "standard", "nvidia"):
        entries = packages(name)
        assert entries, name
        bad = [p for p in entries if not re.fullmatch(r"[a-z0-9][a-z0-9+.\-]+(:[a-z0-9]+)?", p)]
        assert not bad, f"{name}.list: not bare package names: {bad}"
        assert len(entries) == len(set(entries)), f"{name}.list has duplicates"


@pytest.mark.unit
def test_editions_do_not_repeat_the_common_list_or_each_other():
    common, standard, nvidia = (set(packages(n)) for n in ("common", "standard", "nvidia"))
    assert not common & standard
    assert not common & nvidia
    assert not standard & nvidia


@pytest.mark.unit
def test_the_nvidia_edition_installs_the_nvidia_metapackage():
    assert packages("nvidia") == ["axon-nvidia"]
    assert (ROOT / "packaging" / "axon-nvidia" / "control").is_file()
    assert not any("nvidia" in p for p in packages("common")), "NVIDIA belongs to its own edition"


@pytest.mark.unit
def test_nvidia_metapackage_uses_signed_prebuilt_modules_and_has_32_bit_gl():
    depends = [d.strip() for d in control("axon-nvidia")["Depends"].split(",")]
    assert "linux-modules-nvidia-595-open-generic" in depends, "prebuilt, Secure Boot signed"
    assert "libnvidia-gl-595:i386" in depends, "Steam on NVIDIA needs the 32-bit GL libraries"
    assert not any("dkms" in d or "kernel-source" in d for d in depends), "no compiler on the image"
    branches = {m.group(1) for d in depends if (m := re.search(r"nvidia[a-z0-9-]*?-(\d{3})\b", d))}
    assert branches == {"595"}, f"every NVIDIA package must be the same branch: {branches}"


@pytest.mark.unit
def test_every_package_built_in_the_repo_that_a_list_names_has_a_control_file():
    for name in ("common", "standard", "nvidia"):
        for pkg in packages(name):
            if pkg.startswith("axon-"):
                assert (ROOT / "packaging" / pkg / "control").is_file(), pkg


@pytest.mark.unit
def test_chroot_setup_reads_the_edition_lists_and_fails_without_the_driver():
    text = (ROOT / "build" / "config" / "chroot-setup.sh").read_text()
    assert "build/package-lists/common.list" in text
    assert "build/package-lists/${EDITION}.list" in text
    assert "dpkg -s axon-nvidia" in text and "libnvidia-gl-595:i386" in text
    assert "snapd" in text and "language-pack-" in text, "slimming step"
    assert "rm -f /tmp/axon-gaming-debs/axon-nvidia_*.deb" in text, "standard edition has no driver"


# -- build.sh options ------------------------------------------------------------------------


def build(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(ROOT / "build" / "build.sh"), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("args", "code"),
    [
        (["--edition", "bogus"], 2),
        (["--edition=gaming"], 2),
        (["--nope"], 2),
        (["--edition"], 1),
        (["--edition=nvidia", "--compression"], 1),
        (["--cmd"], 1),
    ],
)
def test_build_script_rejects_bad_options_before_doing_anything(args, code):
    result = build(*args)
    assert result.returncode == code, result.stderr


@pytest.mark.unit
def test_build_script_names_and_separates_editions():
    text = (ROOT / "build" / "build.sh").read_text()
    assert 'ISO_NAME="axon-os-${VERSION}-${EDITION}-${ARCH}.iso"' in text
    assert 'WORK_DIR="${BUILD_ROOT}/edition-${EDITION}"' in text
    assert "NOT release quality" in text, "--fast warns"
    assert "check_size_budget" in text and "size-budget.conf" in text


# -- size budget ---------------------------------------------------------------------------------


def check(tmp_path: Path, edition: str, megabytes: int, *extra: str, budget: Path | None = None):
    iso = tmp_path / f"axon-os-{edition}.iso"
    with iso.open("wb") as f:
        f.truncate(megabytes * 1_000_000)
    env = dict(os.environ)
    if budget:
        env["AXON_SIZE_BUDGET"] = str(budget)
    return subprocess.run(
        ["bash", str(SCRIPT), "--edition", edition, *extra, str(iso)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.mark.unit
def test_budget_file_holds_the_documented_limits():
    conf = (ROOT / "build" / "size-budget.conf").read_text()
    values = dict(re.findall(r"^([A-Z_]+)=(\d+)$", conf, re.M))
    assert values == {
        "STANDARD_TARGET_MB": "3500",
        "STANDARD_LIMIT_MB": "3900",
        "NVIDIA_TARGET_MB": "4300",
        "NVIDIA_LIMIT_MB": "4700",
        "ADDED_MB_NEEDS_REASON": "50",
    }


@pytest.mark.unit
def test_under_target_passes(tmp_path):
    result = check(tmp_path, "standard", 3400)
    assert result.returncode == 0 and "OK: 100 MB under the target" in result.stdout


@pytest.mark.unit
def test_between_target_and_limit_warns_but_passes(tmp_path):
    result = check(tmp_path, "standard", 3829)
    assert result.returncode == 0
    assert "WARNING: 3829 MB is over the 3500 MB target by 329 MB" in result.stdout


@pytest.mark.unit
def test_over_the_hard_limit_fails(tmp_path):
    result = check(tmp_path, "standard", 3901)
    assert result.returncode == 1 and "FAIL" in result.stdout


@pytest.mark.unit
def test_each_edition_has_its_own_limit(tmp_path):
    assert check(tmp_path, "standard", 4000).returncode == 1
    assert check(tmp_path, "nvidia", 4000).returncode == 0
    assert check(tmp_path, "nvidia", 4701).returncode == 1


@pytest.mark.unit
def test_usage_errors(tmp_path):
    assert check(tmp_path, "gaming", 100).returncode == 2
    missing = subprocess.run(
        ["bash", str(SCRIPT), "--edition", "standard", str(tmp_path / "nope.iso")],
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 2
    assert check(tmp_path, "standard", 100, "--top", "many").returncode == 2


@pytest.mark.unit
def test_a_custom_budget_file_is_honoured(tmp_path):
    budget = tmp_path / "budget.conf"
    budget.write_text(
        "STANDARD_TARGET_MB=100\nSTANDARD_LIMIT_MB=200\nNVIDIA_TARGET_MB=1\nNVIDIA_LIMIT_MB=2\n"
    )
    assert check(tmp_path, "standard", 150, budget=budget).returncode == 0
    assert check(tmp_path, "standard", 250, budget=budget).returncode == 1


@pytest.mark.unit
def test_reports_the_biggest_packages_and_what_changed(tmp_path):
    new = tmp_path / "new.tsv"
    old = tmp_path / "old.tsv"
    old.write_text(
        "# iso_bytes=3700000000\n1000000\tlibreoffice-core\t24\n500000\tlinux-firmware\t1\n"
        "2048\tbash\t5\n"
    )
    rows = [f"{1000 + i}\tpkg{i:02d}\t1" for i in range(40)]
    new.write_text("# iso_bytes=3829402002\n500000\tlinux-firmware\t1\n300000\tsteam-libs\t1\n"
                   + "\n".join(rows) + "\n")  # fmt: skip
    result = check(tmp_path, "standard", 3829, "--packages", str(new), "--previous", str(old))
    out = result.stdout
    section = out.split("biggest installed packages ==")[1].strip().splitlines()
    top = section[: next(i for i, ln in enumerate(section) if "installed in total" in ln)]
    assert len(top) == 30
    assert ("linux-firmware" in top[0] and "steam-libs" in top[1]) or "steam-libs" in top[0]
    assert re.search(r"added\s+steam-libs\s+\+\s*293\.0 MB", out)
    assert re.search(r"removed\s+libreoffice-core\s+-\s*976\.\d MB", out)
    assert "ISO size: +129 MB (was 3700 MB)" in out
    assert "more than 50 MB added" not in out or "NOTE" in out


@pytest.mark.unit
def test_a_big_addition_asks_for_a_reason(tmp_path):
    new = tmp_path / "new.tsv"
    old = tmp_path / "old.tsv"
    old.write_text("# iso_bytes=3000000000\n2048\tbash\t5\n")
    new.write_text("# iso_bytes=3100000000\n2048\tbash\t5\n204800\tbig-thing\t1\n")  # +200 MB
    out = check(tmp_path, "standard", 3100, "--packages", str(new), "--previous", str(old)).stdout
    assert "NOTE: more than 50 MB added" in out and "rule (A, B or C)" in out


# -- Ollama GPU libraries --------------------------------------------------------------------------

needs_zstd = pytest.mark.skipif(shutil.which("zstd") is None, reason="needs zstd")


def gpu(tmp_path: Path, *args: str, prefix: Path | None = None, extra: dict | None = None):
    env = {
        "PATH": os.environ["PATH"],
        "OLLAMA_PIN_FILE": str(tmp_path / "pin.env"),
        "OLLAMA_PREFIX": str(prefix or tmp_path / "prefix"),
        "OLLAMA_DOWNLOAD_DIR": str(tmp_path / "dl"),
        **(extra or {}),
    }
    return subprocess.run(
        ["bash", str(GPU_HELPER), *args], capture_output=True, text=True, env=env, timeout=60
    )


@pytest.fixture
def release(tmp_path):
    """A fake Ollama release: CPU libs, two CUDA directories, a ROCm one, and a pin file."""
    if shutil.which("zstd") is None:
        pytest.skip("needs zstd")
    main, rocm, dl = tmp_path / "main", tmp_path / "rocm", tmp_path / "dl"
    for d in ("lib/ollama/cuda_v12", "lib/ollama/cuda_v13", "lib/ollama/vulkan"):
        (main / d).mkdir(parents=True)
        (main / d / "lib.so").write_text(d)
    (main / "lib/ollama/libggml-base.so").write_text("cpu")
    (rocm / "lib/ollama/rocm_v7_2").mkdir(parents=True)
    (rocm / "lib/ollama/rocm_v7_2/lib.so").write_text("rocm")
    dl.mkdir()
    for name, src in (("main", main), ("rocm", rocm)):
        subprocess.run(
            ["tar", "--zstd", "-cf", str(dl / f"{name}.tar.zst"), "-C", str(src), "lib"], check=True
        )

    def sha(name):
        out = subprocess.run(
            ["sha256sum", str(dl / f"{name}.tar.zst")], capture_output=True, text=True, check=True
        )
        return out.stdout.split()[0]

    (tmp_path / "pin.env").write_text(
        'OLLAMA_VERSION="v9.9.9"\nOLLAMA_ASSET="main.tar.zst"\n'
        f'OLLAMA_SHA256="{sha("main")}"\nOLLAMA_ROCM_ASSET="rocm.tar.zst"\n'
        f'OLLAMA_ROCM_SHA256="{sha("rocm")}"\n'
    )
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "ollama").write_text("#!/bin/sh\n")
    (prefix / "bin" / "ollama").chmod(0o755)
    return prefix


def files(prefix: Path) -> set[str]:
    return {
        str(p.relative_to(prefix / "lib" / "ollama"))
        for p in (prefix / "lib").rglob("*")
        if p.is_file()
    }


@pytest.mark.unit
@needs_zstd
def test_cuda_install_unpacks_one_directory_and_leaves_the_rest(tmp_path, release):
    result = gpu(tmp_path, "cuda")
    assert result.returncode == 0, result.stdout + result.stderr
    assert files(release) == {".axon-gpu-cuda", "cuda_v12/lib.so"}  # no driver: the older CUDA
    assert (release / "lib/ollama/.axon-gpu-cuda").read_text().strip() == "v9.9.9"


@pytest.mark.unit
@needs_zstd
def test_rocm_install_and_status_and_remove(tmp_path, release):
    assert gpu(tmp_path, "rocm").returncode == 0
    assert files(release) == {".axon-gpu-rocm", "rocm_v7_2/lib.so"}
    assert gpu(tmp_path, "status").stdout.strip() == "rocm v9.9.9"
    assert gpu(tmp_path, "remove", "rocm").returncode == 0
    assert files(release) == set()
    assert gpu(tmp_path, "status").stdout.strip() == ""


@pytest.mark.unit
@needs_zstd
def test_a_download_that_does_not_match_the_pin_is_refused(tmp_path, release):
    (tmp_path / "dl" / "main.tar.zst").write_bytes(b"tampered")
    result = gpu(tmp_path, "cuda")
    assert result.returncode == 1 and "SHA-256 mismatch" in result.stdout
    assert files(release) == set()


@pytest.mark.unit
@needs_zstd
def test_reinstalling_replaces_the_old_version(tmp_path, release):
    gpu(tmp_path, "cuda")
    (release / "lib/ollama/cuda_v12/stale.so").write_text("old")
    gpu(tmp_path, "cuda")
    assert "cuda_v12/stale.so" not in files(release)


@pytest.mark.unit
def test_usage_and_missing_pin(tmp_path):
    assert gpu(tmp_path).returncode == 2
    assert gpu(tmp_path, "vulkan").returncode == 2
    assert gpu(tmp_path, "remove").returncode == 2
    assert gpu(tmp_path, "cuda").returncode == 3  # no pin file, no Ollama


@pytest.mark.unit
def test_refuses_without_a_pinned_hash(tmp_path):
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "ollama").write_text("x")
    (prefix / "bin" / "ollama").chmod(0o755)
    (tmp_path / "pin.env").write_text('OLLAMA_VERSION="v1"\nOLLAMA_ASSET="a.tar.zst"\n')
    assert gpu(tmp_path, "cuda").returncode == 3
    assert gpu(tmp_path, "rocm").returncode == 3


@pytest.mark.unit
@pytest.mark.parametrize(
    ("driver", "expected"),
    [("13.0", "cuda_v13"), ("12.4", "cuda_v12")],
)
def test_cuda_directory_follows_the_driver(tmp_path, driver, expected):
    fake = tmp_path / "bin"
    fake.mkdir()
    smi = fake / "nvidia-smi"
    smi.write_text(
        f'#!/bin/sh\necho "| NVIDIA-SMI 1  Driver Version: 1   CUDA Version: {driver}  |"\n'
    )
    smi.chmod(0o755)
    (tmp_path / "pin.env").write_text(
        'OLLAMA_VERSION="v1"\nOLLAMA_ASSET="a.tar.zst"\nOLLAMA_SHA256="00"\n'
    )
    result = gpu(
        tmp_path,
        "cuda",
        extra={"OLLAMA_GPU_DRY_RUN": "1", "PATH": f"{fake}:{os.environ['PATH']}"},
    )
    assert f"extract lib/ollama/{expected}" in result.stdout


@pytest.mark.unit
def test_cuda_directory_without_a_loaded_driver_defaults_to_the_older_one(tmp_path):
    (tmp_path / "pin.env").write_text(
        'OLLAMA_VERSION="v1"\nOLLAMA_ASSET="a.tar.zst"\nOLLAMA_SHA256="00"\n'
    )
    result = gpu(tmp_path, "cuda", extra={"OLLAMA_GPU_DRY_RUN": "1"})
    assert "extract lib/ollama/cuda_v" in result.stdout


@pytest.mark.unit
def test_pinned_release_names_both_gpu_assets():
    pin = (ROOT / "build" / "config" / "ollama-release.env").read_text()
    for key in ("OLLAMA_SHA256", "OLLAMA_ROCM_SHA256"):
        assert re.search(rf'^{key}="[0-9a-f]{{64}}"$', pin, re.M), key
    policy = (ROOT / "data" / "polkit" / "org.axonos.ollama-gpu.policy").read_text()
    assert "/usr/lib/axon/ollama/install-ollama-gpu.sh" in policy
    assert "auth_admin" in policy and "<allow_active>auth_admin_keep" in policy


@pytest.mark.unit
def test_a_large_package_list_does_not_make_the_size_check_fail(tmp_path):
    """A real list has 1900 packages. `sort | head` used to die of SIGPIPE under pipefail, and
    build.sh reported that as "ISO is over its size limit" for an ISO that was 540 MB under it."""
    pkgs = tmp_path / "packages.tsv"
    rows = [
        f"{(i * 7919) % 100000}\tpackage-with-a-fairly-long-name-{i}\t1.2.{i}-0ubuntu1"
        for i in range(6000)
    ]
    pkgs.write_text("# iso_bytes=2960793600\n" + "\n".join(rows) + "\n")
    result = check(tmp_path, "standard", 2960, "--packages", str(pkgs))
    assert result.returncode == 0, result.stderr
    assert "biggest installed packages" in result.stdout
    assert "installed in total (6000 packages)" in result.stdout, "the report reaches its last line"
    assert "Broken pipe" not in result.stderr


@pytest.mark.unit
def test_build_tells_a_failed_size_check_from_an_over_limit_iso():
    text = (ROOT / "build" / "build.sh").read_text()
    assert "size check failed" in text, "exit code other than 1 is the script's own failure"
