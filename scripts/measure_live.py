"""Measure a running Satark service: N text checks and M screenshot checks, with the median and worst times and
which AI provider answered each one.

    python scripts/measure_live.py https://satark-1tnt.onrender.com
    python scripts/measure_live.py https://satark-1tnt.onrender.com --texts 10 --shots 3 --gap 12

Every request is made unique (a tag on the text, one changed pixel in the image) so the server's cache can't make
a result look faster than it is. --gap paces the checks: Groq's free tier allows roughly 6 a minute, and going
faster would measure its rate limit instead of its speed. Needs httpx (already a dependency); Pillow (the dev
requirements) is used to vary the screenshots.
"""
import argparse
import io
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ["kyc-sms.png", "family-hindi-whatsapp.png", "bank-otp-genuine.png"]


def unique_png(path: Path, nonce: int) -> bytes:
    """The screenshot with one pixel nudged, so every run has new bytes (the cache keys on a hash of them)."""
    data = path.read_bytes()
    try:
        from PIL import Image
    except ImportError:
        return data
    img = Image.open(io.BytesIO(data)).convert("RGB")
    r, g, b = img.getpixel((0, 0))
    img.putpixel((0, 0), (r, g, (b + 1 + nonce) % 256))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def summarise(rows: list[dict]) -> dict:
    """Median and worst wall time (seconds), the provider mix, and how many checks got no AI."""
    walls = [r["wall_s"] for r in rows]
    mix: dict[str, int] = {}
    for r in rows:
        mix[r["provider"] or "none (rules only)"] = mix.get(r["provider"] or "none (rules only)", 0) + 1
    return {"n": len(rows), "median_s": round(statistics.median(walls), 2) if walls else None,
            "worst_s": round(max(walls), 2) if walls else None, "providers": mix,
            "no_ai": sum(1 for r in rows if not r["provider"])}


def one(client: httpx.Client, base: str, data: dict, files: dict | None = None) -> dict:
    started = time.perf_counter()
    try:
        r = client.post(f"{base}/api/check", data=data, files=files, timeout=60)
        wall = time.perf_counter() - started
        body = r.json()
    except Exception as e:  # noqa: BLE001
        return {"wall_s": time.perf_counter() - started, "provider": None, "error": type(e).__name__, "timing": {}}
    if r.status_code != 200:
        return {"wall_s": wall, "provider": None, "error": f"HTTP {r.status_code}: {body.get('detail', '')[:60]}",
                "timing": {}, "attempts": []}
    return {"wall_s": wall, "provider": body.get("ai_provider"), "error": body.get("ai_error"),
            "timing": body.get("ai_timing") or {}, "attempts": body.get("ai_attempts") or [], "verdict": body.get("verdict")}


def show(label: str, i: int, row: dict) -> None:
    t = row["timing"]
    detail = ""
    if t.get("cached"):
        detail = "  (CACHED, not a real measurement)"
    elif t:
        detail = (f"  call={t.get('total_ms')}ms connect={t.get('connect_ms')} tls={t.get('tls_ms')} "
                  f"ttfb={t.get('ttfb_ms')} reused={t.get('reused')} tokens={t.get('completion_tokens')}")
    if row.get("attempts"):
        detail += "  FAILED: " + "; ".join(f"{a['provider']} {a['status']} ({a['took_ms']}ms)" for a in row["attempts"])
    print(f"{label} {i:>2}: {row['wall_s']:5.1f}s  {row['provider'] or 'no AI':<8} {row.get('error') or '':<14}{detail}")


def report(title: str, rows: list[dict]) -> None:
    s = summarise(rows)
    print(f"\n{title}: n={s['n']}  median {s['median_s']}s  worst {s['worst_s']}s  "
          f"providers {s['providers']}  checks with no AI: {s['no_ai']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", help="e.g. https://satark-1tnt.onrender.com")
    ap.add_argument("--texts", type=int, default=10)
    ap.add_argument("--shots", type=int, default=3)
    ap.add_argument("--gap", type=float, default=12, help="seconds between checks (default 12)")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    run = uuid.uuid4().hex[:6]
    samples = [s for s in json.loads((ROOT / "samples" / "messages.json").read_text("utf-8")) if s["expected"] != "low"]

    with httpx.Client() as client:
        print(f"health: {client.get(base + '/api/health', timeout=90).text}")   # also wakes a sleeping host
        texts: list[dict] = []
        for i in range(args.texts):
            msg = f"{samples[i % len(samples)]['text']} [ref {run}-{i}]"
            row = one(client, base, {"text": msg, "lang": "en", "ai": "true"})
            show("text", i + 1, row)
            texts.append(row)
            time.sleep(args.gap)
        shots: list[dict] = []
        for i in range(args.shots):
            png = unique_png(ROOT / "samples" / "screenshots" / SHOTS[i % len(SHOTS)], i + int(run, 16) % 200)
            row = one(client, base, {"lang": "en", "ai": "true"}, {"image": (f"shot{i}.png", png, "image/png")})
            show("shot", i + 1, row)
            shots.append(row)
            time.sleep(args.gap)

    report("TEXT checks", texts)
    report("SCREENSHOT checks", shots)
    if any(not r["provider"] for r in texts + shots):
        print("\nSome checks got no AI. The FAILED lines above say why (HTTP status per provider): "
              "401 = rejected key, 429 = rate limit, 5xx = provider outage, timeout = too slow.")
        sys.exit(1)


if __name__ == "__main__":
    main()
