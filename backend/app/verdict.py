"""Fuse rule signals and LLM judgement into one verdict the user can act on."""
from __future__ import annotations

from .actions import build_actions
from .rules.translations import localize_signal

SCAM_AT = 50
SUSPICIOUS_AT = 20

HEADLINES = {
    "scam": "This looks like a scam",
    "suspicious": "Be careful: this has warning signs",
    "low": "No common scam signs found",
}

NO_PATTERNS = "We didn't find patterns used in common Indian scams."

# English -> (Hindi, Marathi). Fixed strings. The page shows the same headlines (frontend/index.html, I18N), and a
# test keeps the two in step.
TRANSLATIONS: dict[str, tuple[str, str]] = {
    HEADLINES["scam"]: ("यह संदेश धोखाधड़ी लगता है", "हा संदेश फसवणूक वाटतो"),
    HEADLINES["suspicious"]: ("सावधान: इसमें खतरे के संकेत हैं", "सावध रहा: यात धोक्याची चिन्हे आहेत"),
    HEADLINES["low"]: ("धोखाधड़ी के आम संकेत नहीं मिले", "फसवणुकीची सामान्य चिन्हे आढळली नाहीत"),
    NO_PATTERNS: ("हमें आम भारतीय धोखाधड़ियों वाले पैटर्न नहीं मिले।",
                  "सामान्य भारतीय फसवणुकीत दिसणारे प्रकार आम्हाला आढळले नाहीत."),
}
_LANG_INDEX = {"hi": 0, "mr": 1}


def translate(text: str, lang: str) -> str:
    pair = TRANSLATIONS.get(text)
    return pair[_LANG_INDEX[lang]] if pair and lang in _LANG_INDEX else text


def band(score: int) -> str:
    if score >= SCAM_AT:
        return "scam"
    if score >= SUSPICIOUS_AT:
        return "suspicious"
    return "low"


def fuse(text: str, rules: dict, llm: dict | None, ai_error: str | None = None, lang: str = "en") -> dict:
    """`lang` (en / hi / mr) picks the fixed headline, red-flag text and next steps. The LLM's own explanation is
    already in the requested language, and the evidence (words from the message) is never translated."""
    rule_score = rules["score"]
    signals = [localize_signal(s, lang) for s in rules["signals"]]
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
    elif signals:
        explanation = " ".join(s["why"] for s in signals[:2])
    else:
        explanation = translate(NO_PATTERNS, lang)

    highlights = [s["evidence"] for s in signals if s.get("evidence")]
    highlights += [f["quote"] for f in llm_flags]

    scam_type = (llm or {}).get("scam_type") or None
    if (not scam_type or scam_type == "none") and signals:
        scam_type = signals[0]["label"]
    if verdict == "low":
        scam_type = None

    return {
        "verdict": verdict,
        "risk": risk,
        "headline": translate(HEADLINES[verdict], lang),
        "scam_type": scam_type,
        "explanation": explanation,
        "signals": signals,
        "llm_flags": llm_flags,
        "highlights": list(dict.fromkeys(highlights)),
        "actions": build_actions(verdict, [s["id"] for s in rules["signals"]], lang),
        "scores": {"rules": rule_score, "llm": (llm or {}).get("risk")},
        "ai_used": llm is not None,
        "ai_error": None if llm is not None else ai_error,
        "ai_provider": (llm or {}).get("provider"),
        "ai_timing": (llm or {}).get("timing"),
        "analysed_text": text,
    }
