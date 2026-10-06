"""LLM layer — any OpenAI-compatible endpoint (Gemini by default; Groq, OpenRouter, OpenAI... also work).

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

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"  # Gemini, OpenAI-compatible

# Server-side blips worth one retry. A 429 is deliberately not retried: waiting a few seconds rarely
# clears a quota, so we go straight to the fallback model (or rules-only) instead.
RETRY_STATUSES = {500, 503}
RETRY_DELAYS = (2.0,)  # the wait before retry n is RETRY_DELAYS[n]


def _total_timeout() -> float:
    """Cap on all LLM work for one request (retries and fallback included), so the UI never hangs."""
    return float(os.getenv("LLM_TOTAL_TIMEOUT", "12"))

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


async def _complete(client: httpx.AsyncClient, base: str, headers: dict, payload: dict,
                    delays: tuple[float, ...] = RETRY_DELAYS) -> str:
    """POST one chat completion, retrying transient errors after each wait in `delays`.
    Returns the reply text or raises LLMError."""
    provider, model = urlparse(base).netloc or "unknown", payload["model"]
    attempts = len(delays) + 1
    for attempt in range(1, attempts + 1):
        try:
            r = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as e:
            _log_failure(provider, model, "-", type(e).__name__, attempt, attempts)
            raise LLMError("unavailable") from e

        if r.status_code == 429:
            _log_failure(provider, model, 429, "rate_limited", attempt, attempts)
            raise LLMError("rate_limited")
        if r.status_code in RETRY_STATUSES:
            _log_failure(provider, model, r.status_code, "unavailable", attempt, attempts)
            if attempt < attempts:
                await _sleep(delays[attempt - 1])
                continue
            raise LLMError("unavailable")
        if r.status_code >= 400:  # bad key, bad model name, ...: retrying won't help
            _log_failure(provider, model, r.status_code, "http_error", attempt, attempts)
            raise LLMError("unavailable")

        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError, ValueError) as e:
            _log_failure(provider, model, r.status_code, type(e).__name__, attempt, attempts)
            raise LLMError("bad_response") from e
    raise LLMError("unavailable")  # pragma: no cover (loop always returns or raises)


async def _ask(client: httpx.AsyncClient, base: str, headers: dict, payload: dict,
               delays: tuple[float, ...]) -> dict:
    """One model: call it (with retries), parse the JSON. Raises LLMError."""
    parsed = _parse_json(await _complete(client, base, headers, payload, delays))
    if parsed is None:
        _log_failure(urlparse(base).netloc or "unknown", payload["model"], 200, "unparseable_json", 1, 1)
        raise LLMError("bad_response")
    return parsed


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
        # Thinking models spend part of this budget on hidden reasoning; too small and the JSON gets cut off.
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "2048")),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    }
    base = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"}

    # Primary model (one retry on 500/503); then, if set, the fallback model (same endpoint and key) once.
    models = [payload["model"]]
    fallback = os.getenv("LLM_FALLBACK_MODEL", "").strip()
    if fallback and fallback != models[0]:
        models.append(fallback)

    async def run() -> dict:
        first_error: LLMError | None = None
        async with _client() as client:
            for i, model in enumerate(models):
                payload["model"] = model
                try:
                    return await _ask(client, base, headers, payload, RETRY_DELAYS if i == 0 else ())
                except LLMError as e:
                    first_error = first_error or e
                    if i + 1 < len(models):
                        log.warning("LLM falling back: provider=%s from=%s to=%s reason=%s",
                                    urlparse(base).netloc or "unknown", model, models[i + 1], e.reason)
        raise first_error  # the primary model's failure is the one worth reporting

    try:
        return await asyncio.wait_for(run(), timeout=_total_timeout())
    except asyncio.TimeoutError:
        log.warning("LLM call failed: provider=%s model=%s status=- error=total_timeout limit=%ss",
                    urlparse(base).netloc or "unknown", models[0], _total_timeout())
        raise LLMError("unavailable") from None
