"""Check Hindi and Marathi answers from a running Satark (the live site by default).

    python scripts/check_translations_live.py [--base URL] [--ai N]

For a spread of samples it asks for lang=en, lang=hi and lang=mr and checks, per answer:
  - the headline, every red-flag label and reason, and every "what to do now" step contain Devanagari text;
  - names people must recognise stay as written (1930, cybercrime.gov.in, OTP, UPI, ...): every one that appears
    in the English step or signal also appears in the translated one;
  - the number of steps and signals matches English (nothing dropped);
  - with --ai N, the first N samples are also run with the AI on, and its explanation must be in that language.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
KEEP = ["1930", "cybercrime.gov.in", "sancharsaathi.gov.in", "Sanchar Saathi", "Chakshu", "UPI", "OTP", "PIN", "CVV",
        "KYC", "AnyDesk", "TeamViewer", "SEBI", "CBI", "APK", "QR", "WhatsApp", "Telegram", "Play Store", ".gov.in", ".apk"]
IDS = ["digital-arrest", "kyc-sms", "upi-cashback", "family-new-number", "task-job", "otp-card-review", "anydesk-refund", "apk-rewards", "bank-otp"]


def has(term: str, text: str) -> bool:
    if term.isalpha() and term.isupper():
        return re.search(rf"(?<![A-Za-z]){term}(?![A-Za-z])", text) is not None
    return term in text


def ask(client: httpx.Client, base: str, text: str, lang: str, ai: bool) -> dict:
    for attempt in range(3):
        r = client.post(f"{base}/api/check", data={"text": text, "lang": lang, "ai": str(ai).lower()}, timeout=60)
        if r.status_code == 200:
            return r.json()
        time.sleep(5)
    r.raise_for_status()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://satark-1tnt.onrender.com")
    ap.add_argument("--ai", type=int, default=2, help="how many samples to also check with the AI on")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    samples = {s["id"]: s for s in json.loads((ROOT / "samples" / "messages.json").read_text(encoding="utf-8"))}
    ids = [i for i in IDS if i in samples] or list(samples)[:6]
    problems, checked = [], 0

    def fail(msg):
        problems.append(msg)
        print("  FAIL", msg)

    with httpx.Client() as client:
        for n, sid in enumerate(ids):
            text = samples[sid]["text"]
            en = ask(client, base, text, "en", False)
            print(f"{sid}: verdict={en['verdict']} risk={en['risk']} signals={len(en['signals'])} steps={len(en['actions'])}")
            for lang in ("hi", "mr"):
                for ai in ([False, True] if n < a.ai else [False]):
                    d = ask(client, base, text, lang, ai)
                    tag = f"{sid}/{lang}{'+ai' if ai else ''}"
                    checked += 1
                    if d["verdict"] != en["verdict"] and not ai:
                        fail(f"{tag}: verdict {d['verdict']} differs from English {en['verdict']}")
                    if not DEVANAGARI.search(d["headline"]):
                        fail(f"{tag}: headline not translated: {d['headline']!r}")
                    if len(d["actions"]) != len(en["actions"]) and not ai:
                        fail(f"{tag}: {len(d['actions'])} steps vs {len(en['actions'])} in English")
                    for i, step in enumerate(d["actions"]):
                        if not DEVANAGARI.search(step):
                            fail(f"{tag}: step {i + 1} has no Devanagari: {step!r}")
                        if i < len(en["actions"]):
                            for term in KEEP:
                                if has(term, en["actions"][i]) and not has(term, step):
                                    fail(f"{tag}: step {i + 1} lost '{term}': {step!r}")
                    for s_en, s in zip(en["signals"], d["signals"]):
                        if s["id"] == s_en["id"]:
                            for field in ("label", "why"):
                                if not DEVANAGARI.search(s[field]):
                                    fail(f"{tag}: signal {s['id']} {field} not translated: {s[field]!r}")
                                for term in KEEP:
                                    if has(term, s_en[field]) and not has(term, s[field]):
                                        fail(f"{tag}: signal {s['id']} {field} lost '{term}'")
                            if s["evidence"] != s_en["evidence"]:
                                fail(f"{tag}: evidence changed ({s['evidence']!r}); it must stay verbatim")
                    if ai:
                        if d.get("ai_used") and not DEVANAGARI.search(d["explanation"]):
                            fail(f"{tag}: AI explanation not in {lang}: {d['explanation'][:80]!r}")
                        print(f"  {tag}: AI answered by {d.get('ai_provider')}, risk {d['risk']}, headline: {d['headline']}")
                        time.sleep(12)   # stay under Groq's free-tier token limit
    print(f"\n{checked} answers checked, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
