"""LLM layer — any OpenAI-compatible endpoint (Featherless, Gemini, Groq, OpenRouter, OpenAI...).

The LLM adds judgement the regexes can't: context, tone, novel scripts and plain-language
explanations in the user's language. It is never the only thing deciding the verdict.
"""
from __future__ import annotations

import base64
import json
import os
import re

import httpx

LANG_NAMES = {"en": "English", "hi": "Hindi (Devanagari)", "mr": "Marathi (Devanagari)"}

SYSTEM_PROMPT = """You are Satark, a fraud analyst protecting ordinary people in India from scams \
(digital arrest, KYC/account block, courier/customs, task jobs, investment groups, UPI refund/QR tricks, \
fake electricity bills, APK malware, impersonated relatives, AI voice clones).

Assess the message the user received. Be calibrated: genuine bank OTP alerts, delivery updates and \
personal chats are LOW risk. Do not invent facts that are not in the message.

Reply with ONLY a JSON object, no prose, in this exact shape:
{
  "risk": <integer 0-100>,
  "scam_type": "<short label or 'none'>",
  "red_flags": [{"quote": "<exact words copied from the message>", "why": "<one short sentence>"}],
  "explanation": "<2-3 plain sentences a non-technical person understands, in the requested language>",
  "extracted_text": "<if an image was provided, the text in it; otherwise empty>"
}"""


def is_configured() -> bool:
    return bool(os.getenv("LLM_API_KEY") and os.getenv("LLM_MODEL"))


def vision_enabled() -> bool:
    return os.getenv("LLM_VISION", "false").lower() in {"1", "true", "yes"}


def _parse_json(raw: str) -> dict | None:
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    try:
        data["risk"] = max(0, min(100, int(data.get("risk", 0))))
    except (TypeError, ValueError):
        return None
    data.setdefault("red_flags", [])
    data.setdefault("explanation", "")
    data.setdefault("scam_type", "none")
    data.setdefault("extracted_text", "")
    return data


async def analyse(text: str, lang: str, rule_hints: list[dict],
                  image: bytes | None = None, image_mime: str = "image/png") -> dict | None:
    """Returns the parsed LLM judgement, or None if unconfigured / failed (rules still answer)."""
    if not is_configured():
        return None

    hints = ", ".join(h["label"] for h in rule_hints) or "none"
    user_text = (
        f"Explanation language: {LANG_NAMES.get(lang, 'English')}.\n"
        f"Signals our rule engine already found: {hints}.\n\n"
        f"Message received:\n\"\"\"\n{text or '(see image)'}\n\"\"\""
    )
    content: list | str = user_text
    if image is not None and vision_enabled():
        b64 = base64.b64encode(image).decode()
        content = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{b64}"}},
        ]

    payload = {
        "model": os.environ["LLM_MODEL"],
        "temperature": 0.1,
        "max_tokens": 700,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    }
    base = os.getenv("LLM_BASE_URL", "https://api.featherless.ai/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"}
    try:
        async with httpx.AsyncClient(timeout=float(os.getenv("LLM_TIMEOUT", "40"))) as client:
            r = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return None
    return _parse_json(raw or "")
