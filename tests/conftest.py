import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

# main.py calls load_dotenv() on import, which never overrides variables that are already set.
# Pinning the key to empty here means the real .env can't switch the LLM on during tests.
os.environ["LLM_API_KEY"] = ""


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    """Every test starts with the LLM unconfigured. Tests that need LLM behaviour mock llm.analyse
    or set their own env, so pytest never makes a network call to a real provider."""
    monkeypatch.setenv("LLM_API_KEY", "")
