"""Score every sample in samples/messages.json and print a table + catch-rate summary.

    python scripts/eval.py            # rules only
    python scripts/eval.py --llm      # rules + LLM (needs .env)

Paste the summary into the README "Results" section — judges like real numbers.
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from app import llm  # noqa: E402
from app.rules.engine import run_rules  # noqa: E402
from app.verdict import fuse  # noqa: E402


async def main(use_llm: bool) -> None:
    samples = json.loads((ROOT / "samples" / "messages.json").read_text("utf-8"))
    tp = fn = fp = tn = 0
    print(f"{'sample':22} {'expected':11} {'got':11} {'rules':>5} {'llm':>5} {'final':>5}")
    for s in samples:
        rules = run_rules(s["text"])
        judgement = None
        if use_llm:
            try:
                judgement = await llm.analyse(s["text"], "en", rules["signals"])
            except llm.LLMError:
                pass  # falls back to rules, like the API does
        out = fuse(s["text"], rules, judgement)
        is_bad, flagged = s["expected"] != "low", out["verdict"] != "low"
        tp += is_bad and flagged
        fn += is_bad and not flagged
        fp += (not is_bad) and flagged
        tn += (not is_bad) and not flagged
        print(f"{s['id']:22} {s['expected']:11} {out['verdict']:11} {rules['score']:5} "
              f"{str(out['scores']['llm'] or '-'):>5} {out['risk']:5}")
    print(f"\nScams caught: {tp}/{tp + fn}   False alarms on genuine messages: {fp}/{fp + tn}")


if __name__ == "__main__":
    asyncio.run(main("--llm" in sys.argv))
