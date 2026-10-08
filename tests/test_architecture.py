"""The architecture PNG's source (scripts/make_architecture_image.py), the README Mermaid diagram and render.yaml
must describe the same models and stages, so the pictures can't drift from the real design."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import make_architecture_image as slide  # noqa: E402


def render_yaml(key: str) -> str:
    m = re.search(rf"- key: {key}\n\s+value: (\S+)", (ROOT / "render.yaml").read_text(encoding="utf-8"))
    return m.group(1)


def mermaid() -> str:
    return re.search(r"```mermaid\n(.*?)```", (ROOT / "README.md").read_text(encoding="utf-8"), re.S).group(1)


def test_both_pictures_name_the_models_that_are_deployed():
    primary = render_yaml("LLM_MODEL").split("/")[-1]                         # qwen3.8-27b
    fallback = render_yaml("LLM_FALLBACK_MODEL").replace("gemini-", "").replace("-", " ", 1)   # 3.5 flash-lite
    for name, text in (("png", slide.build_html()), ("mermaid", mermaid())):
        assert primary in text, f"{name}: primary model {primary}"
        assert fallback in text, f"{name}: fallback model {fallback}"


def test_both_pictures_show_the_same_stages_and_guarantees():
    for term in ["Groq", "Gemini", "Rule engine", "Cache", "LRU", "256", "Fusion", "Playbook", "Stage 1", "Stage 2", "1930", "cybercrime.gov.in"]:
        assert term in slide.build_html(), f"png missing {term}"
        assert term in mermaid(), f"mermaid missing {term}"


def test_slide_boxes_stay_inside_the_canvas():
    for nid, (x, y, w, h, *_rest) in slide.NODES.items():
        assert x >= 0 and x + w <= 1820, nid
