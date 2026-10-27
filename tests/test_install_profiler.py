"""install.sh reads the hardware profiler's JSON from stdout; the two must agree."""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILER = ROOT / "services" / "axon-brain" / "hardware_profiler.py"


def test_profiler_prints_json_install_sh_can_read():
    out = subprocess.run(
        [sys.executable, str(PROFILER)], capture_output=True, text=True, timeout=60, check=True
    ).stdout
    data = json.loads(out)
    script = (ROOT / "install.sh").read_text()
    # every key path install.sh reads, e.g. d['hardware']['ram_gb'] or d['recommendations']['speed']['model']
    for path in set(re.findall(r"d((?:\['[a-z_]+'\])+)", script)):
        node = data
        for key in re.findall(r"\['([a-z_]+)'\]", path):
            assert key in node, f"install.sh reads {path}, the profiler has no {key!r}"
            node = node[key]
