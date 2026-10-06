"""LLM layer — any OpenAI-compatible endpoint (Featherless, Gemini, Groq, OpenRouter, OpenAI...).

The LLM adds judgement the regexes can't: context, tone, novel scripts and plain-language
explanations in the user's language. It is never the only thing deciding the verdict.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from urllib.parse import urlparse

import httpx

log = logging.getLogger("satark.llm")

LANG_NAMES = {"en": "English", "hi": "Hindi (Devanagari)", "mr": "Marathi (Devanagari)"}

DEFAULT_BASE_URL = "https://api.featherless.ai/v1"

# Transient failures worth retrying; the wait before retry n is RETRY_DELAYS[n].
RETRY_STATUSES = {429, 500, 503}
RETRY_DELAYS = (2.0, 5.0)

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


class LLMError(Exception):
    """The LLM was configured but gave us nothing usable. `reason` is safe to show the client:
    "rate_limited" | "unavailable" | "bad_response"."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def is_configured() -> bool:
    return bool(os.getenv("LLM_API_KEY") and os.getenv("LLM_MODEL"))


def vision_enabled() -> bool:
    return os.getenv("LLM_VISION", "false").lower() in {"1", "true", "yes"}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=float(os.getenv("LLM_TIMEOUT", "40")))


_sleep = asyncio.sleep  # indirection so tests can skip the backoff


def _strip_fences(raw: str) -> str:
    return re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).strip()


def _parse_json(raw: str) -> dict | None:
    raw = _strip_fences(raw)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        data["risk"] = max(0, min(100, int(float(data.get("risk", 0)))))
    except (TypeError, ValueError):
        return None
    if not isinstance(data.get("red_flags"), list):
        data["red_flags"] = []
    if not isinstance(data.get("explanation"), str):
        data["explanation"] = ""
    if not isinstance(data.get("scam_type"), str):
        data["scam_type"] = "none"
    if not isinstance(data.get("extracted_text"), str):
        data["extracted_text"] = ""
    return data


def _log_failure(provider: str, model: str, status: int | str, error: str, attempt: int, attempts: int) -> None:
    # Deliberately no key, URL, request body or message text here.
    log.warning("LLM call failed: provider=%s model=%s status=%s error=%s attempt=%d/%d",
                provider, model, status, error, attempt, attempts)


async def _complete(client: httpx.AsyncClient, base: str, headers: dict, payload: dict) -> str:
    """POST one chat completion with retries. Returns the reply text or raises LLMError."""
    provider, model = urlparse(base).netloc or "unknown", payload["model"]
    attempts = len(RETRY_DELAYS) + 1
    for attempt in range(1, attempts + 1):
        try:
            r = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as e:
            _log_failure(provider, model, "-", type(e).__name__, attempt, attempts)
            raise LLMError("unavailable") from e

        if r.status_code in RETRY_STATUSES:
            reason = "rate_limited" if r.status_code == 429 else "unavailable"
            _log_failure(provider, model, r.status_code, reason, attempt, attempts)
            if attempt < attempts:
                await _sleep(RETRY_DELAYS[attempt - 1])
                continue
            raise LLMError(reason)
        if r.status_code >= 400:  # bad key, bad model name, ...: retrying won't help
            _log_failure(provider, model, r.status_code, "http_error", attempt, attempts)
            raise LLMError("unavailable")

        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError, ValueError) as e:
            _log_failure(provider, model, r.status_code, type(e).__name__, attempt, attempts)
            raise LLMError("bad_response") from e
    raise LLMError("unavailable")  # pragma: no cover (loop always returns or raises)


async def analyse(text: str, lang: str, rule_hints: list[dict],
                  image: bytes | None = None, image_mime: str = "image/png") -> dict | None:
    """Returns the parsed LLM judgement, or None if no LLM is configured.

    Raises LLMError if it is configured but failed, so the caller can fall back to rules and say why.
    """
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
    base = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"}

    async with _client() as client:
        raw = await _complete(client, base, headers, payload)
    parsed = _parse_json(raw)
    if parsed is None:
        log.warning("LLM call failed: provider=%s model=%s status=200 error=unparseable_json",
                    urlparse(base).netloc or "unknown", payload["model"])
        raise LLMError("bad_response")
    return parsed
