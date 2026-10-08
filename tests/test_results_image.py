"""The numbers on docs/results.png (scripts/make_results_image.py) must match the README Results table."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import make_results_image as slide  # noqa: E402


def readme_table() -> dict[str, list[str]]:
    rows = {}
    for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip().replace("*", "") for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) == 4 and re.fullmatch(r"\d+", cells[1]):
            rows[cells[0]] = cells[1:]
    return rows


def test_slide_numbers_match_the_readme_table():
    table = readme_table()
    for group, n, before, after, _ in slide.ROWS:
        key = next(k for k in table if k.startswith(group))
        assert table[key] == [str(n), before, after], group
    key = next(k for k in table if k.startswith(slide.TOTAL[0]))
    assert table[key][0] == str(slide.TOTAL[1])
    assert table[key][1].startswith(slide.TOTAL[2]) and table[key][2].startswith(slide.TOTAL[3])


def test_slide_html_contains_every_number():
    html = slide.build_html()
    for _, n, before, after, _ in slide.ROWS:
        assert before in html and after in html and f">{n}<" in html
