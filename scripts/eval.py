"""Score every sample in samples/messages.json and print a per-sample table plus a grouped summary.

    python scripts/eval.py                  # rules only
    python scripts/eval.py --llm            # rules only AND rules + LLM, side by side (needs .env)
    python scripts/eval.py --llm --delay 8  # wait 8s between LLM calls (default 4, or EVAL_DELAY)

Groups in the summary:
  all scams        every sample whose expected verdict isn't "low" (a scam is "caught" if it isn't rated low)
  known scripts    the scams our rules were written for
  rules-blind      scams with no keyword the rules know ("rules_blind": true), which only the LLM can catch
  genuine          real messages; a "false alarm" is any genuine message not rated low

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


def group_of(sample: dict) -> str:
    if sample["expected"] == "low":
        return "genuine"
    return "rules-blind" if sample.get("rules_blind") else "known"


def tally(rows: list[dict], groups: set[str], key: str) -> tuple[int, int]:
    """(flagged, total) over the rows in `groups`, using the verdict stored under `key`."""
    chosen = [r for r in rows if r["group"] in groups]
    return sum(r[key] != "low" for r in chosen), len(chosen)


def fmt(flagged: int, total: int) -> str:
    return f"{flagged}/{total} ({100 * flagged / total:.0f}%)" if total else "-"


async def main(use_llm: bool, delay: float) -> None:
    samples = json.loads((ROOT / "samples" / "messages.json").read_text("utf-8"))
    if use_llm and not llm.is_configured():
        sys.exit("--llm needs LLM_API_KEY and LLM_MODEL in .env")

    rows: list[dict] = []
    errors: dict[str, int] = {}
    head = f"{'sample':26} {'expected':10} {'rules-only':>14}"
    print(head + (f" {'llm':>4} {'rules+llm':>14}  {'ai':12} group" if use_llm else " group"))
    for i, s in enumerate(samples):
        rules = run_rules(s["text"])
        base = fuse(s["text"], rules, None)
        judgement, ai_error = None, None
        if use_llm:
            if i:
                await asyncio.sleep(delay)
            try:
                judgement = await llm.analyse(s["text"], "en", rules["signals"])
            except llm.LLMError as e:  # falls back to rules, like the API does
                ai_error = e.reason
        out = fuse(s["text"], rules, judgement, ai_error)
        if ai_error:
            errors[ai_error] = errors.get(ai_error, 0) + 1
        rows.append({"group": group_of(s), "rules": base["verdict"], "final": out["verdict"], "ai": out["ai_used"]})
        llm_score = out["scores"]["llm"]
        ai_col = "yes" if out["ai_used"] else (ai_error or "-")
        cells = f"{base['verdict']:>10} {base['risk']:>3}"
        if use_llm:
            cells += f" {str(llm_score) if llm_score is not None else '-':>4} {out['verdict']:>10} {out['risk']:>3}  {ai_col:12}"
        print(f"{s['id']:26} {s['expected']:10} {cells} {group_of(s)}")

    def summary_line(name: str, groups: set[str]) -> str:
        n = tally(rows, groups, "rules")[1]
        line = f"{name:18}{n:>4}  {fmt(tally(rows, groups, 'rules')[0], n):>16}"
        if use_llm:
            line += f"  {fmt(tally(rows, groups, 'final')[0], n):>16}"
        return line

    print()
    print(f"{'':18}{'n':>4}  {'rules only':>16}" + (f"  {'rules + LLM':>16}" if use_llm else ""))
    print("Scams caught (rated suspicious or scam):")
    print(summary_line("all scams", {"known", "rules-blind"}))
    print(summary_line("  known scripts", {"known"}))
    print(summary_line("  rules-blind", {"rules-blind"}))
    print("False alarms (genuine rated suspicious or scam):")
    print(summary_line("genuine", {"genuine"}))

    if use_llm:
        ai_scored = sum(r["ai"] for r in rows)
        detail = ", ".join(f"{n} {r}" for r, n in errors.items())
        print(f"\nAI-scored: {ai_scored}/{len(rows)}"
              + (f"   (fell back to rules: {len(rows) - ai_scored}: {detail})" if ai_scored < len(rows) else ""))
        print(f"Model: {os.getenv('LLM_MODEL')}" + (f"  fallback: {os.getenv('LLM_FALLBACK_MODEL')}"
                                                     if os.getenv("LLM_FALLBACK_MODEL") else ""))


if __name__ == "__main__":
    asyncio.run(main("--llm" in sys.argv, delay_from_args(sys.argv)))
