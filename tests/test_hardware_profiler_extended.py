"""Extended tests for hardware_profiler — GPU detection and recommendations."""

from unittest.mock import MagicMock, mock_open, patch

from services.axon_brain.hardware_profiler import (
    get_gpu_info,
    get_system_ram,
    profile_hardware,
)


class TestGetSystemRam:
    def test_parses_proc_meminfo(self):
        mock_content = "MemTotal:       16384000 kB\nMemFree:         1000000 kB"
        with patch("builtins.open", mock_open(read_data=mock_content)):
            ram = get_system_ram()
        assert abs(ram - 15.62) < 0.1  # ~15.6 GB

    def test_fallback_on_error(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            ram = get_system_ram()
        assert ram == 8.0

    def test_fallback_on_no_match(self):
        with patch("builtins.open", mock_open(read_data="no meminfo here")):
            ram = get_system_ram()
        assert ram == 8.0


class TestGetGpuInfo:
    def test_nvidia_detected(self):
        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="RTX 3080, 10240\n", returncode=0)
                gpu = get_gpu_info()
        assert gpu["vendor"] == "NVIDIA"
        assert gpu["model"] == "RTX 3080"
        assert abs(gpu["vram"] - 10.0) < 0.1

    def test_amd_rocm_detected(self):
        with patch(
            "shutil.which", side_effect=lambda x: "/usr/bin/rocm-smi" if x == "rocm-smi" else None
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    stdout="VRAM Total Memory (B): 8589934592\n", returncode=0
                )
                gpu = get_gpu_info()
        assert gpu["vendor"] == "AMD"
        assert abs(gpu["vram"] - 8.0) < 0.1

    def test_lspci_nvidia_fallback(self):
        with patch("shutil.which", return_value=None):
            with patch(
                "subprocess.check_output",
                return_value="01:00.0 VGA compatible controller: NVIDIA Corporation",
            ):
                gpu = get_gpu_info()
        assert gpu["vendor"] == "NVIDIA"
        assert gpu["status"] == "unsupported_driver"

    def test_lspci_amd_fallback(self):
        with patch("shutil.which", return_value=None):
            with patch(
                "subprocess.check_output", return_value="01:00.0 VGA: Advanced Micro Devices"
            ):
                gpu = get_gpu_info()
        assert gpu["vendor"] == "AMD"

    def test_lspci_intel_fallback(self):
        with patch("shutil.which", return_value=None):
            with patch("subprocess.check_output", return_value="00:02.0 VGA: Intel Corporation"):
                gpu = get_gpu_info()
        assert gpu["vendor"] == "Intel"
        assert gpu["status"] == "cpu_shared"

    def test_cpu_fallback(self):
        with patch("shutil.which", return_value=None):
            with patch("subprocess.check_output", side_effect=Exception("no lspci")):
                gpu = get_gpu_info()
        assert gpu["vendor"] == "CPU"
        assert gpu["vram"] == 0.0


class TestProfileHardware:
    def test_returns_hardware_and_recommendations(self):
        with patch("services.axon_brain.hardware_profiler.get_system_ram", return_value=16.0):
            with patch(
                "services.axon_brain.hardware_profiler.get_gpu_info",
                return_value={
                    "vendor": "NVIDIA",
                    "model": "RTX 3060",
                    "vram": 12.0,
                    "status": "detected",
                },
            ):
                profile = profile_hardware()
        assert "hardware" in profile
        assert "recommendations" in profile
        assert profile["hardware"]["ram_gb"] == 16.0
        assert "speed" in profile["recommendations"]
        assert "general" in profile["recommendations"]
        assert "deep" in profile["recommendations"]

    def test_high_vram_nvidia_recommendations(self):
        with patch("services.axon_brain.hardware_profiler.get_system_ram", return_value=32.0):
            with patch(
                "services.axon_brain.hardware_profiler.get_gpu_info",
                return_value={
                    "vendor": "NVIDIA",
                    "model": "RTX 4090",
                    "vram": 24.0,
                    "status": "detected",
                },
            ):
                profile = profile_hardware()
        assert "14b" in profile["recommendations"]["deep"]["model"]

    def test_low_vram_recommendations(self):
        with patch("services.axon_brain.hardware_profiler.get_system_ram", return_value=8.0):
            with patch(
                "services.axon_brain.hardware_profiler.get_gpu_info",
                return_value={
                    "vendor": "NVIDIA",
                    "model": "GT 1030",
                    "vram": 2.0,
                    "status": "detected",
                },
            ):
                profile = profile_hardware()
        assert "3b" in profile["recommendations"]["deep"]["model"]

    def test_cpu_with_high_ram(self):
        with patch("services.axon_brain.hardware_profiler.get_system_ram", return_value=32.0):
            with patch(
                "services.axon_brain.hardware_profiler.get_gpu_info",
                return_value={
                    "vendor": "CPU",
                    "model": "Generic",
                    "vram": 0.0,
                    "status": "fallback",
                },
            ):
                profile = profile_hardware()
        assert "8b" in profile["recommendations"]["deep"]["model"]

    def test_cpu_with_low_ram(self):
        with patch("services.axon_brain.hardware_profiler.get_system_ram", return_value=4.0):
            with patch(
                "services.axon_brain.hardware_profiler.get_gpu_info",
                return_value={
                    "vendor": "CPU",
                    "model": "Generic",
                    "vram": 0.0,
                    "status": "fallback",
                },
            ):
                profile = profile_hardware()
        assert "3b" in profile["recommendations"]["deep"]["model"]
