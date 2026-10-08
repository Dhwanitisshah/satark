"""Render the README Results table as a 1920x1080 slide: docs/results.png (for the demo video and Devpost).

    python scripts/make_results_image.py

The numbers are in ROWS below, copied from the README "Results" table (a test checks they still match it).
It draws the slide as HTML in headless Edge/Chrome (scripts/cdp.py) with the same fonts as the site.
"""
from __future__ import annotations

import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# (group, n, rules only, rules + AI, kind): kind says what the numbers count.
ROWS = [
    ("Known-script scams", 35, "35 / 35", "35 / 35", "caught"),
    ("Rules-blind scams", 8, "0 / 8", "8 / 8", "caught"),
    ("Genuine messages", 20, "0 / 20", "0 / 20", "false alarms"),
]
TOTAL = ("All scams", 43, "35 / 43", "43 / 43")
OUT = ROOT / "docs" / "results.png"

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root { --paper:#f6f2ea; --card:#fffdf8; --ink:#1d1b16; --muted:#6b6558; --line:#e4ddcf; --accent:#c2410c;
          --good:#1f7a4d; --good-bg:#e6f4ec; --bad:#b42318; }
  * { box-sizing: border-box; }
  html, body { margin:0; width:1920px; height:1080px; background:var(--paper); color:var(--ink);
               font:400 28px/1.4 Inter, "Segoe UI", system-ui, sans-serif; }
  .slide { padding:72px 110px 56px; height:1080px; display:flex; flex-direction:column; }
  h1 { font:700 76px/1.05 Fraunces, Georgia, serif; margin:0; letter-spacing:-0.02em; }
  h1 span { color:var(--accent); }
  .sub { color:var(--muted); font-size:30px; margin:14px 0 40px; }
  table { width:100%; border-collapse:separate; border-spacing:0 14px; }
  th { text-align:left; font:600 24px Inter, sans-serif; letter-spacing:.09em; text-transform:uppercase; color:var(--muted);
       padding:0 32px 4px; }
  th.num, td.num { text-align:center; }
  td { background:var(--card); padding:26px 32px; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
  td:first-child { border-left:1px solid var(--line); border-radius:20px 0 0 20px; width:42%; }
  td:last-child { border-right:1px solid var(--line); border-radius:0 20px 20px 0; }
  .group { font:600 40px/1.1 Inter, sans-serif; }
  .what { color:var(--muted); font-size:25px; margin-top:6px; }
  .n { font-weight:500; color:var(--muted); font-size:34px; width:9%; }
  .val { font:700 62px/1 Inter, sans-serif; font-variant-numeric:tabular-nums; }
  .val.before { color:var(--muted); }
  .val.after { color:var(--good); }
  .val.after.bad { color:var(--bad); }
  tr.hero td { background:#fff3e8; border-color:#f0c9a8; }
  tr.hero .group::after { content:"the fair test"; margin-left:18px; padding:4px 16px; border-radius:999px; background:var(--accent);
                          color:#fff; font:600 22px Inter, sans-serif; vertical-align:middle; }
  .total { display:flex; gap:48px; align-items:baseline; margin:6px 0 0 32px; font-size:30px; color:var(--muted); }
  .total b { color:var(--ink); font-weight:700; }
  .notes { margin-top:auto; font-size:23px; line-height:1.45; color:var(--muted); display:grid; gap:6px; }
  .notes b { color:var(--ink); font-weight:600; }
  .foot { display:flex; justify-content:space-between; margin-top:20px; font-size:23px; color:var(--muted); }
</style></head><body><div class="slide">
  <h1>Satark<span>.</span> The AI catches what the rules can't</h1>
  <div class="sub">63 labelled messages, rules only vs rules + AI, including 20 genuine bank, delivery and college messages.</div>
  <table>
    <tr><th>Message group</th><th class="num">n</th><th class="num">Rules only</th><th class="num">Rules + AI</th></tr>
    __ROWS__
  </table>
  <div class="total"><span>All scams: <b>__TOT_A__</b> (81%) &rarr; <b>__TOT_B__</b> (100%)</span></div>
  <div class="notes">
    <div><b>Known scripts are in-sample</b>: I tuned the rules on them. <b>Rules-blind</b> scams contain no keyword the rules know and no rule was added for them, so that row is the honest measure of generalisation.</div>
    <div><b>0 false alarms with Groq answering.</b> If the Gemini fallback answers instead, it flags one: a friend asking for &#8377;500 on UPI. Production run, 63 of 63 samples AI-scored. A small, author-written set is a smoke test, not a benchmark.</div>
  </div>
  <div class="foot"><span>satark-1tnt.onrender.com</span><span>ForgeHacks 2026 &middot; AI + Cybersecurity</span></div>
</div></body></html>"""


def row_html(group: str, n: int, before: str, after: str, kind: str) -> str:
    hero = " hero" if group.startswith("Rules-blind") else ""
    bad = " bad" if kind == "false alarms" and not after.startswith("0") else ""
    return (f'<tr class="{hero.strip()}"><td><div class="group">{escape(group)}</div><div class="what">{kind}</div></td>'
            f'<td class="num n">{n}</td><td class="num"><span class="val before">{before}</span></td>'
            f'<td class="num"><span class="val after{bad}">{after}</span></td></tr>')


def build_html() -> str:
    rows = "\n    ".join(row_html(*r) for r in ROWS)
    return PAGE.replace("__ROWS__", rows).replace("__TOT_A__", TOTAL[2]).replace("__TOT_B__", TOTAL[3])


def main() -> None:
    from cdp import Browser

    html = build_html()
    tmp = ROOT / "docs" / ".results-slide.html"
    tmp.write_text(html, encoding="utf-8")
    try:
        with Browser(port=9478) as br:
            br.viewport(1920, 1080, 1)
            br.color_scheme("light")
            br.goto(tmp.as_uri())
            br.wait_for("document.fonts.status === 'loaded'", 30)
            br.screenshot(OUT, full_page=False)
    finally:
        tmp.unlink(missing_ok=True)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
