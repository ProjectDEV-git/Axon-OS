"""Regression tests for the release version bump script."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_bump_version_accepts_numeric_patch_versions(tmp_path: Path) -> None:
    """Ensure 1.x.y versions do not trigger invalid regex backreferences."""
    repo_root = Path(__file__).resolve().parents[1]
    source_script = repo_root / "scripts" / "bump_version.py"

    project_dir = tmp_path / "repo"
    scripts_dir = project_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    script_copy = scripts_dir / "bump_version.py"
    script_copy.write_text(source_script.read_text())

    pyproject = project_dir / "pyproject.toml"
    pyproject.write_text('[project]\nname = "axon-os"\nversion = "1.1.0"\n')

    result = subprocess.run(
        [sys.executable, str(script_copy), "1.2.1"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert 'version = "1.2.1"' in pyproject.read_text()
