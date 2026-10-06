"""Score every sample in samples/messages.json and print a table + catch-rate summary.

    python scripts/eval.py                  # rules only
    python scripts/eval.py --llm            # rules + LLM (needs .env)
    python scripts/eval.py --llm --delay 8  # wait 8s between LLM calls (default 4, or EVAL_DELAY)

Free-tier LLM quotas are small, so --llm paces its calls. The summary says how many samples actually got
an AI score and how many fell back to rules, so the numbers you paste into the README are honest.
"""
import asyncio
import json
import os
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

DEFAULT_DELAY = 4.0


def delay_from_args(argv: list[str]) -> float:
    if "--delay" in argv:
        return float(argv[argv.index("--delay") + 1])
    return float(os.getenv("EVAL_DELAY", DEFAULT_DELAY))


async def main(use_llm: bool, delay: float) -> None:
    samples = json.loads((ROOT / "samples" / "messages.json").read_text("utf-8"))
    if use_llm and not llm.is_configured():
        sys.exit("--llm needs LLM_API_KEY and LLM_MODEL in .env")
    tp = fn = fp = tn = ai_scored = 0
    errors: dict[str, int] = {}
    print(f"{'sample':22} {'expected':11} {'got':11} {'rules':>5} {'llm':>5} {'final':>5}  ai")
    for i, s in enumerate(samples):
        rules = run_rules(s["text"])
        judgement, ai_error = None, None
        if use_llm:
            if i:
                await asyncio.sleep(delay)
            try:
                judgement = await llm.analyse(s["text"], "en", rules["signals"])
            except llm.LLMError as e:  # falls back to rules, like the API does
                ai_error = e.reason
        out = fuse(s["text"], rules, judgement, ai_error)
        is_bad, flagged = s["expected"] != "low", out["verdict"] != "low"
        tp += is_bad and flagged
        fn += is_bad and not flagged
        fp += (not is_bad) and flagged
        tn += (not is_bad) and not flagged
        ai_scored += out["ai_used"]
        if ai_error:
            errors[ai_error] = errors.get(ai_error, 0) + 1
        ai_col = "yes" if out["ai_used"] else (ai_error or "-")
        print(f"{s['id']:22} {s['expected']:11} {out['verdict']:11} {rules['score']:5} "
              f"{str(out['scores']['llm'] if out['scores']['llm'] is not None else '-'):>5} {out['risk']:5}  {ai_col}")
    print(f"\nScams caught: {tp}/{tp + fn}   False alarms on genuine messages: {fp}/{fp + tn}")
    if use_llm:
        detail = ", ".join(f"{n} {r}" for r, n in errors.items())
        print(f"AI-scored: {ai_scored}/{len(samples)}"
              + (f"   (fell back to rules: {len(samples) - ai_scored}: {detail})" if ai_scored < len(samples) else ""))


if __name__ == "__main__":
    asyncio.run(main("--llm" in sys.argv, delay_from_args(sys.argv)))
