#!/usr/bin/env python3
import unittest
from unittest.mock import mock_open, patch

import hardware_profiler


class TestHardwareProfiler(unittest.TestCase):
    def test_get_system_ram_valid(self):
        meminfo_content = "MemTotal:       16384000 kB\nMemFree:         4000000 kB\n"
        with patch("builtins.open", mock_open(read_data=meminfo_content)):
            ram = hardware_profiler.get_system_ram()
            # 16384000 kB / 1024 / 1024 = 15.625 GB
            self.assertAlmostEqual(ram, 15.625, places=3)

    def test_get_system_ram_invalid_format(self):
        meminfo_content = "TotalMem: 12345\n"
        with patch("builtins.open", mock_open(read_data=meminfo_content)):
            ram = hardware_profiler.get_system_ram()
            self.assertEqual(ram, 8.0)  # Default fallback

    def test_get_system_ram_file_not_found(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            ram = hardware_profiler.get_system_ram()
            self.assertEqual(ram, 8.0)  # Default fallback

    def test_get_system_ram_empty_file(self):
        with patch("builtins.open", mock_open(read_data="")):
            ram = hardware_profiler.get_system_ram()
            self.assertEqual(ram, 8.0)  # Default fallback


if __name__ == "__main__":
    unittest.main()
