import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import llm  # noqa: E402

# main.py calls load_dotenv() on import, which never overrides variables that are already set.
# Pinning the key to empty here means the real .env can't switch the LLM on during tests.
os.environ["LLM_API_KEY"] = ""

# Everything else the LLM layer reads from the environment (see .env.example).
LLM_ENV_VARS = ["LLM_BASE_URL", "LLM_MODEL", "LLM_VISION", "LLM_FALLBACK_MODEL", "LLM_TIMEOUT", "LLM_TOTAL_TIMEOUT",
                "LLM_MAX_TOKENS"]


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    """Every test starts with the LLM unconfigured and no LLM_* settings leaking in from .env.
    Tests that need LLM behaviour mock llm.analyse or set their own env, so pytest never makes a
    network call to a real provider."""
    monkeypatch.setenv("LLM_API_KEY", "")
    for name in LLM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    llm.clear_cache()  # a cached answer from one test must not leak into the next
