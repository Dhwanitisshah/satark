"""Runs tests/ui_flow.js (the page's two-stage flow against a stub DOM) when Node is available."""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "ui_flow.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_page_script_flow():
    r = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
