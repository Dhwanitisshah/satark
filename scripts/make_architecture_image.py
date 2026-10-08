"""Draw the architecture as docs/architecture.png (1920x1080), for Devpost and the demo video.

    python scripts/make_architecture_image.py

The README has the same design as a Mermaid diagram (rendered by GitHub). Mermaid's automatic layout is about four
times wider than tall, so on a 16:9 slide its text came out too small to read in a video. This script draws the
same boxes and arrows by hand as an SVG with large type, and renders it in headless Edge/Chrome (scripts/cdp.py).
tests/test_architecture.py checks that both pictures name the same models and stages.

Colour key: green = deterministic code, orange = an AI model, grey = page and cache.
"""
from __future__ import annotations

import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
OUT = ROOT / "docs" / "architecture.png"

COLORS = {"det": ("#e6f4ec", "#1f7a4d"), "ai": ("#fff3e8", "#c2410c"), "ui": ("#fffdf8", "#6b6558")}

# id: (x, y, w, h, kind, title, lines, badge). Heights fit the text: 70 + 33 per line + 30.
NODES = {
    "page": (0, 140, 390, 265, "ui", "Web page (PWA)",
             ["Paste a message, add a", "screenshot, or Share one", "from WhatsApp / SMS.", "Stage 1: rules, at once", "Stage 2: AI updates the card"], 1),
    "rules": (540, 0, 330, 199, "det", "Rule engine", ["~20 Indian scam patterns", "links, UPI IDs, phones", "instant, no AI"], 2),
    "cache": (540, 263, 330, 166, "ui", "Cache (LRU, 256)", ["a repeat check skips", "the AI call"], 3),
    "groq": (990, 0, 330, 166, "ai", "Groq: primary", ["qwen3.8-27b, text only", "about 1 second"], 4),
    "gemini": (990, 263, 330, 199, "ai", "Gemini: fallback", ["3.5 flash-lite", "the only model that", "reads screenshots"], None),
    "fusion": (1440, 0, 380, 232, "det", "Fusion", ["risk = max(rules,", "0.6 x AI + 0.4 x rules)", "AI can raise, never lower", "AI quotes must be in the text"], 5),
    "playbook": (1440, 320, 380, 166, "det", "Playbook: fixed text", ["1930, cybercrime.gov.in,", "Sanchar Saathi Chakshu"], 6),
}

# (path, dashed, thick, label (x, y, text) or None)
ARROWS = [
    ("M390,200 H465 V70 H540", False, False, None),                       # page -> rules
    ("M540,140 H502 V300 H390", False, True, (398, 334, "Stage 1")),      # rules -> page (instant verdict)
    ("M705,199 V263", False, False, (716, 238, "hints")),                 # rules -> cache
    ("M870,300 H930 V80 H990", False, False, (940, 200, "text")),         # cache -> groq
    ("M870,390 H990", True, False, (890, 378, "image")),             # cache -> gemini
    ("M1155,166 V263", True, False, (1166, 222, "if Groq fails")),        # groq -> gemini
    ("M1320,80 H1440", False, False, None),                               # groq -> fusion
    ("M1320,370 H1380 V170 H1440", False, False, None),                   # gemini -> fusion
    ("M705,0 V-30 H1630 V0", False, False, (830, -42, "rule score and evidence")),   # rules -> fusion
    ("M1630,232 V320", False, False, None),                               # fusion -> playbook
    ("M1630,486 V520 H195 V405", False, True, (780, 508, "Stage 2: verdict, red flags, next steps")),  # playbook -> page
]

STEPS = [
    "You paste text, add a screenshot, or Share a message from WhatsApp.",
    "The rules answer in milliseconds, so Stage 1 shows at once.",
    "A repeat check is served from the cache; otherwise the rule signals go to the AI as hints.",
    "Groq reads text first. Gemini reads screenshots, and takes over if Groq is slow or limited.",
    "Fusion: the AI can raise the risk, never lower a rule hit. AI quotes not in the message are dropped.",
    "Next steps come from a fixed playbook, never the AI. Stage 2 updates the card in place.",
]


def node(nid: str) -> str:
    x, y, w, h, kind, title, lines, badge = NODES[nid]
    fill, stroke = COLORS[kind]
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="22" fill="{fill}" stroke="{stroke}" stroke-width="3"/>',
           f'<text x="{x + 24}" y="{y + 50}" class="t">{escape(title)}</text>']
    for i, line in enumerate(lines):
        out.append(f'<text x="{x + 24}" y="{y + 92 + i * 33}" class="l">{escape(line)}</text>')
    if badge:
        out.append(f'<circle cx="{x}" cy="{y}" r="22" fill="{stroke}"/><text x="{x}" y="{y + 9}" class="b" text-anchor="middle">{badge}</text>')
    return "\n".join(out)


def arrow(path: str, dashed: bool, thick: bool, label) -> str:
    style = f'stroke-width="{5 if thick else 3}"' + (' stroke-dasharray="10 8"' if dashed else "")
    out = [f'<path d="{path}" fill="none" stroke="#4a463c" {style} marker-end="url(#head)"/>']
    if label:
        x, y, text = label
        out.append(f'<text x="{x}" y="{y}" class="e">{escape(text)}</text>')
    return "\n".join(out)


def build_html() -> str:
    svg = "\n".join([*(arrow(*a) for a in ARROWS), *(node(n) for n in NODES)])
    steps = "".join(f'<li><b>{i}</b><span>{escape(s)}</span></li>' for i, s in enumerate(STEPS, 1))
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin:0; width:1920px; height:1080px; background:#f6f2ea; color:#1d1b16; font:400 24px/1.35 Inter,"Segoe UI",sans-serif; }}
  .slide {{ height:1080px; padding:44px 70px 34px; position:relative; }}
  h1 {{ font:700 64px/1.05 Fraunces, Georgia, serif; margin:0; letter-spacing:-0.02em; }}
  h1 span {{ color:#c2410c; }}
  .sub {{ color:#6b6558; font-size:27px; margin:8px 0 0; }}
  svg {{ position:absolute; left:70px; top:185px; overflow:visible; }}
  svg .t {{ font:700 34px Inter, sans-serif; fill:#1d1b16; }}
  svg .l {{ font:500 24px Inter, sans-serif; fill:#3d3a31; }}
  svg .e {{ font:600 22px Inter, sans-serif; fill:#6b6558; }}
  svg .b {{ font:700 24px Inter, sans-serif; fill:#fff; }}
  ol {{ list-style:none; margin:0; padding:0; position:absolute; left:70px; right:70px; top:795px; display:grid;
        grid-template-columns:1fr 1fr; gap:10px 56px; }}
  li {{ display:flex; gap:14px; align-items:flex-start; font-size:23px; line-height:1.3; color:#3d3a31; }}
  li b {{ flex:none; width:34px; height:34px; border-radius:50%; background:#6b6558; color:#fff; text-align:center; line-height:34px; font-size:20px; }}
  .key {{ position:absolute; left:70px; top:1016px; display:flex; gap:34px; font-size:21px; color:#6b6558; }}
  .key i {{ display:inline-block; width:18px; height:18px; border-radius:5px; margin-right:9px; vertical-align:-2px; border:2px solid; }}
  .foot {{ position:absolute; right:70px; top:1016px; font-size:21px; color:#6b6558; }}
</style></head><body><div class="slide">
  <h1>How Satark decides<span>.</span></h1>
  <div class="sub">Rules answer instantly. The AI can raise the risk, never lower it. Next steps are fixed text, not AI.</div>
  <svg width="1820" height="600" viewBox="0 -60 1820 600">
    <defs><marker id="head" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="#4a463c"/></marker></defs>
    {svg}
  </svg>
  <ol>{steps}</ol>
  <div class="key"><span><i style="background:#e6f4ec;border-color:#1f7a4d"></i>deterministic code</span>
    <span><i style="background:#fff3e8;border-color:#c2410c"></i>AI model (free tiers)</span>
    <span><i style="background:#fffdf8;border-color:#6b6558"></i>page and cache</span></div>
  <div class="foot">satark-1tnt.onrender.com &middot; ForgeHacks 2026 &middot; AI + Cybersecurity</div>
</div></body></html>"""


def main() -> None:
    from cdp import Browser

    tmp = ROOT / "docs" / ".architecture-slide.html"
    tmp.write_text(build_html(), encoding="utf-8")
    try:
        with Browser(port=9479) as br:
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
