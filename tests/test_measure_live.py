"""The live measurement script's maths and its cache-busting, without touching a network."""
import importlib.util
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("measure_live", ROOT / "scripts" / "measure_live.py")
measure = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = measure
spec.loader.exec_module(measure)


def rows(*pairs):
    return [{"wall_s": w, "provider": p} for w, p in pairs]


def test_median_worst_and_provider_mix():
    s = measure.summarise(rows((1.0, "groq"), (2.0, "groq"), (9.0, "gemini"), (3.0, "groq")))
    assert s["n"] == 4 and s["median_s"] == 2.5 and s["worst_s"] == 9.0
    assert s["providers"] == {"groq": 3, "gemini": 1} and s["no_ai"] == 0


def test_checks_with_no_ai_are_counted_not_hidden():
    s = measure.summarise(rows((1.0, "groq"), (0.7, None), (0.8, None)))
    assert s["no_ai"] == 2 and s["providers"]["none (rules only)"] == 2


def test_no_rows_is_not_a_crash():
    assert measure.summarise([])["median_s"] is None


def test_each_screenshot_run_has_new_bytes_so_the_cache_cannot_flatter_the_numbers():
    pytest.importorskip("PIL")
    from PIL import Image

    path = ROOT / "samples" / "screenshots" / "kyc-sms.png"
    a, b = measure.unique_png(path, 1), measure.unique_png(path, 2)
    assert a != b and a != path.read_bytes()
    assert Image.open(io.BytesIO(a)).size == Image.open(path).size       # still the same picture
