"""Reading graphics cards and drivers."""

import gpu_drivers
import pytest

LSPCI = """\
00:02.0 VGA compatible controller [0300]: Intel Corporation Raptor Lake-S GT1 [UHD Graphics 770] [8086:a780] (rev 04)
\tSubsystem: ASUSTeK Computer Inc. Device [1043:8882]
\tKernel driver in use: i915
\tKernel modules: i915, xe
01:00.0 VGA compatible controller [0300]: NVIDIA Corporation AD104 [GeForce RTX 4070 SUPER] [10de:2783] (rev a1)
\tKernel modules: nouveau
01:00.1 Audio device [0403]: NVIDIA Corporation AD104 High Definition Audio Controller [10de:22bc] (rev a1)
\tKernel driver in use: snd_hda_intel
03:00.0 Display controller [0380]: Advanced Micro Devices, Inc. [AMD/ATI] Phoenix1 [1002:15bf] (rev c4)
\tKernel driver in use: amdgpu
"""


@pytest.mark.unit
def test_parse_lspci_finds_graphics_devices_only():
    gpus = gpu_drivers.parse_lspci(LSPCI)
    assert [(g.vendor, g.pci_id, g.driver) for g in gpus] == [
        ("intel", "8086:a780", "i915"),
        ("nvidia", "10de:2783", ""),  # no driver bound: nouveau is only available
        ("amd", "1002:15bf", "amdgpu"),
    ]
    assert gpus[1].name == "NVIDIA Corporation AD104 [GeForce RTX 4070 SUPER]"


@pytest.mark.unit
def test_parse_lspci_empty():
    assert gpu_drivers.parse_lspci("") == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("nvidia-driver-550, (kernel modules provided by nvidia-dkms-550)\n", "nvidia-driver-550"),
        (
            "nvidia-driver-570-open, (kernel modules provided by linux-modules-nvidia-570-open-generic)",
            "nvidia-driver-570-open",
        ),
        ("", None),
        ("some-other-package, (x)", None),
        ("== /sys/devices/pci0000:00 ==\n", None),
    ],
)
def test_parse_recommended(text, expected):
    assert gpu_drivers.parse_recommended(text) == expected


@pytest.mark.unit
def test_sysfs_vram(tmp_path):
    card0 = tmp_path / "card0" / "device"
    card0.mkdir(parents=True)
    (card0 / "vendor").write_text("0x1002\n")
    (card0 / "mem_info_vram_total").write_text(str(8 * 1024**3))
    card1 = tmp_path / "card1" / "device"
    card1.mkdir(parents=True)
    (card1 / "vendor").write_text("0x8086\n")  # no mem_info_vram_total: integrated Intel
    (tmp_path / "card0-DP-1").mkdir()
    assert gpu_drivers.sysfs_vram_gb(tmp_path) == {"1002": 8.0}


@pytest.mark.unit
def test_profiler_uses_real_amd_vram_when_rocm_is_missing(monkeypatch, tmp_path):
    from unittest.mock import patch

    from services.axon_brain import hardware_profiler as hp

    card = tmp_path / "card0" / "device"
    card.mkdir(parents=True)
    (card / "vendor").write_text("0x1002\n")
    (card / "mem_info_vram_total").write_text(str(16 * 1024**3))
    monkeypatch.setattr(
        hp, "Path", lambda p: tmp_path if p == "/sys/class/drm" else __import__("pathlib").Path(p)
    )
    with (
        patch("shutil.which", return_value=None),
        patch(
            "subprocess.check_output",
            return_value="03:00.0 VGA: Advanced Micro Devices [AMD/ATI] Navi",
        ),
    ):
        gpu = hp.get_gpu_info()
    assert gpu["vendor"] == "AMD" and gpu["vram"] == 16.0
