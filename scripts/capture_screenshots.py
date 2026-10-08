"""Capture the README / Devpost screenshots from a running Satark site (the live one by default).

    python scripts/capture_screenshots.py                              # live site -> docs/screenshots/
    python scripts/capture_screenshots.py --base http://127.0.0.1:8000 --only kyc,otp

It drives headless Edge/Chrome (scripts/cdp.py), so the pictures are the real page talking to the real AI,
not mock-ups. Each shot is taken at a desktop and a phone width. A shot only counts if the AI answered, so a
rate-limited run is retried rather than saved as a rules-only picture.

Shots: kyc (hero), digital-arrest, rules-blind (stage 1 "rules found nothing", then "AI raised risk"),
otp (genuine bank OTP, low risk), upload (a screenshot instead of text), hindi (only with --hindi, after the
Hindi/Marathi playbook is deployed).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from cdp import Browser  # noqa: E402

VIEWS = {"desktop": dict(width=1100, height=900, scale=1, mobile=False),
         "mobile": dict(width=390, height=844, scale=2, mobile=True)}
SAMPLES = {s["id"]: s for s in json.loads((ROOT / "samples" / "messages.json").read_text(encoding="utf-8"))}
UPLOAD = ROOT / "samples" / "screenshots" / "family-hindi-whatsapp.png"
# The AI has answered when the scores line has a second part ("Rule score 0 · AI score 95", in any language) and the note is clear.
AI_DONE = "document.getElementById('scores').textContent.includes(' · ') && document.getElementById('aiNote').textContent === ''"
RESULT_UP = "document.getElementById('result').style.display === 'block'"
# Hold back the AI request by a few seconds so the rules-only first stage can be photographed.
DELAY_AI = """(() => { const f = window.fetch.bind(window); window.fetch = (u, o) =>
  (o && o.body && o.body.get && o.body.get('ai') === 'true') ? new Promise(r => setTimeout(r, %d)).then(() => f(u, o)) : f(u, o); })()"""


def chip(br: Browser, label: str):
    br.js(f"[...document.querySelectorAll('.chip')].find(b => b.textContent === {json.dumps(label)}).click()")


def paste(br: Browser, text: str):
    br.js(f"document.getElementById('text').value = {json.dumps(text)}; document.getElementById('form').requestSubmit()")


def open_page(br: Browser, base: str, view: dict, lang: str | None = None):
    br.viewport(view["width"], view["height"], view["scale"], view["mobile"])
    br.color_scheme("light")   # match the other README images, whatever this machine's theme is
    br.goto(base + "/")
    br.wait_for("document.fonts.status === 'loaded'", 30)
    if lang:
        br.js(f"document.getElementById('lang').value = {json.dumps(lang)}; document.getElementById('lang').onchange()")


def settle(br: Browser, seconds: float = 3.6):
    """Let the 'AI raised risk' pulse finish (2 x 1.6s) so it isn't caught half way."""
    time.sleep(seconds)


def shot_chip(label: str, expect_scam: bool | None = None):
    def run(br, base, view, out, lang=None):
        open_page(br, base, view, lang)
        chip(br, label)
        br.wait_for(AI_DONE, 60)
        settle(br)
        br.screenshot(out)
    return run


def shot_blind(br, base, view, out, lang=None):
    """Two pictures: the rules-only first stage (nothing found), then the AI raising the risk."""
    open_page(br, base, view, lang)
    br.js(DELAY_AI % 5000)
    paste(br, SAMPLES["blind-voice-clone-mama"]["text"])
    br.wait_for(RESULT_UP, 30)
    br.wait_for("document.getElementById('aiNote').textContent.includes('double-checking')", 10)
    br.screenshot(out.with_name(out.name.replace("rules-blind", "rules-blind-1-rules-only")))
    br.wait_for("!document.getElementById('delta').hidden", 60)
    time.sleep(0.5)                       # the pulse is at its widest around half a second in
    br.wait_for(AI_DONE, 10)
    br.screenshot(out.with_name(out.name.replace("rules-blind", "rules-blind-2-ai-raised-risk")))


def shot_upload(br, base, view, out, lang=None):
    open_page(br, base, view, lang)
    br.set_files("#image", [str(UPLOAD)])
    br.js("document.getElementById('form').requestSubmit()")
    br.wait_for(RESULT_UP, 90)
    br.wait_for(AI_DONE, 90)
    settle(br)
    br.screenshot(out)


SHOTS = {
    "kyc": shot_chip("KYC SMS"),
    "digital-arrest": shot_chip("Digital arrest"),
    "rules-blind": shot_blind,
    "otp": shot_chip("Real bank OTP"),
    "upload": shot_upload,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="https://satark-1tnt.onrender.com")
    ap.add_argument("--out", default=str(ROOT / "docs" / "screenshots"))
    ap.add_argument("--only", help="comma-separated shot names")
    ap.add_argument("--hindi", action="store_true", help="also take the Hindi result (the family-emergency sample)")
    ap.add_argument("--attempts", type=int, default=3)
    a = ap.parse_args()

    shots = dict(SHOTS)
    if a.hindi:
        shots["hindi"] = lambda br, base, view, out, lang=None: shot_chip("Family emergency")(br, base, view, out, "hi")
    names = a.only.split(",") if a.only else list(shots)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    failures = []
    with Browser(port=9477) as br:
        for name in names:
            for view_name, view in VIEWS.items():
                path = out_dir / f"{view_name}-{name}.png"
                for attempt in range(1, a.attempts + 1):
                    try:
                        shots[name](br, a.base.rstrip("/"), view, path)
                        print(f"ok   {view_name:<8}{name}")
                        break
                    except (TimeoutError, RuntimeError) as e:
                        print(f"retry {view_name} {name} (attempt {attempt}): {type(e).__name__}")
                        time.sleep(15)   # Groq's free tier is token-limited per minute; give it room
                else:
                    failures.append(f"{view_name}-{name}")
                time.sleep(8)
    if failures:
        print("FAILED:", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
