"""Fuse rule signals and LLM judgement into one verdict the user can act on."""
from __future__ import annotations

from .actions import build_actions

SCAM_AT = 50
SUSPICIOUS_AT = 20

HEADLINES = {
    "scam": "This looks like a scam",
    "suspicious": "Be careful: this has warning signs",
    "low": "No common scam signs found",
}


def band(score: int) -> str:
    if score >= SCAM_AT:
        return "scam"
    if score >= SUSPICIOUS_AT:
        return "suspicious"
    return "low"


def fuse(text: str, rules: dict, llm: dict | None, ai_error: str | None = None) -> dict:
    rule_score = rules["score"]
    if llm is not None:
        # Rules are hard evidence, so the LLM can raise risk but never talk a hard rule hit down.
        risk = max(rule_score, round(0.6 * llm["risk"] + 0.4 * rule_score))
    else:
        risk = rule_score
    verdict = band(risk)

    # Keep only LLM quotes that really appear in the message (guards against hallucinated flags).
    lowered = text.lower()
    llm_flags = [
        f for f in (llm or {}).get("red_flags", [])
        if isinstance(f, dict) and f.get("quote") and str(f["quote"]).lower() in lowered
    ][:5]

    if llm and llm.get("explanation"):
        explanation = llm["explanation"]
    elif rules["signals"]:
        explanation = " ".join(s["why"] for s in rules["signals"][:2])
    else:
        explanation = "We didn't find patterns used in common Indian scams."

    highlights = [s["evidence"] for s in rules["signals"] if s.get("evidence")]
    highlights += [f["quote"] for f in llm_flags]

    scam_type = (llm or {}).get("scam_type") or None
    if (not scam_type or scam_type == "none") and rules["signals"]:
        scam_type = rules["signals"][0]["label"]
    if verdict == "low":
        scam_type = None

    return {
        "verdict": verdict,
        "risk": risk,
        "headline": HEADLINES[verdict],
        "scam_type": scam_type,
        "explanation": explanation,
        "signals": rules["signals"],
        "llm_flags": llm_flags,
        "highlights": list(dict.fromkeys(highlights)),
        "actions": build_actions(verdict, [s["id"] for s in rules["signals"]]),
        "scores": {"rules": rule_score, "llm": (llm or {}).get("risk")},
        "ai_used": llm is not None,
        "ai_error": None if llm is not None else ai_error,
        "ai_provider": (llm or {}).get("provider"),
        "analysed_text": text,
    }
