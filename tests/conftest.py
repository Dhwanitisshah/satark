import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import llm  # noqa: E402

# main.py calls load_dotenv() on import, which never overrides variables that are already set.
# Pinning the key to empty here means the real .env can't switch the LLM on during tests.
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_FALLBACK_API_KEY"] = ""

# Everything else the LLM layer reads from the environment (see .env.example).
LLM_ENV_VARS = ["LLM_BASE_URL", "LLM_MODEL", "LLM_VISION", "LLM_FALLBACK_MODEL", "LLM_FALLBACK_BASE_URL",
                "LLM_FALLBACK_API_KEY", "LLM_FALLBACK_VISION", "LLM_TIMEOUT", "LLM_TOTAL_TIMEOUT", "LLM_TOTAL_TIMEOUT_VISION",
                "LLM_PRIMARY_TIMEOUT", "LLM_MAX_TOKENS", "LLM_FORCE_IPV4", "LLM_CONNECT_TIMEOUT",
                "LLM_KEEPALIVE_SECONDS", "SATARK_DEBUG", "DEBUG"]


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    """Every test starts with the LLM unconfigured and no LLM_* settings leaking in from .env.
    Tests that need LLM behaviour mock llm.analyse or set their own env, so pytest never makes a
    network call to a real provider."""
    monkeypatch.setenv("LLM_API_KEY", "")
    for name in LLM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    llm.clear_cache()  # a cached answer from one test must not leak into the next
    monkeypatch.setattr(llm, "_shared", None)        # no shared client or warm-up task carried between tests
    monkeypatch.setattr(llm, "_warm_task", None)
