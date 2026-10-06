"""LLM layer — any OpenAI-compatible endpoint (Gemini by default; Groq, OpenRouter, OpenAI... also work).

The LLM adds judgement the regexes can't: context, tone, novel scripts and plain-language
explanations in the user's language. It is never the only thing deciding the verdict.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import logging
import os
import re
from collections import OrderedDict
from dataclasses import dataclass, replace
from urllib.parse import urlparse

import httpx

log = logging.getLogger("satark.llm")

LANG_NAMES = {"en": "English", "hi": "Hindi (Devanagari)", "mr": "Marathi (Devanagari)"}

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"  # Gemini, OpenAI-compatible

# Server-side blips worth one retry. A 429 is deliberately not retried: waiting a few seconds rarely
# clears a quota, so we go straight to the fallback model (or rules-only) instead.
RETRY_STATUSES = {500, 503}
RETRY_DELAYS = (2.0,)  # the wait before retry n is RETRY_DELAYS[n]


def _total_timeout(reading_image: bool = False) -> float:
    """Cap on all LLM work for one request (retries and fallback included), so the UI never hangs.

    A screenshot-only request has no instant rules result to show while it waits, and reading an image takes
    longer, so it gets a longer cap (kept under the UI's own 20s abort)."""
    if reading_image:
        return float(os.getenv("LLM_TOTAL_TIMEOUT_VISION", "18"))
    return float(os.getenv("LLM_TOTAL_TIMEOUT", "12"))


def _primary_timeout() -> float:
    """How long the primary model may take when a fallback is waiting. The fallback gets the rest of the
    total cap, so a slow primary can't use up the whole clock (seen live: Gemini at 12s, Groq never asked)."""
    return float(os.getenv("LLM_PRIMARY_TIMEOUT", "7"))


MIN_FALLBACK_SECONDS = 0.5  # less than this left and the fallback can't answer, so don't start it

SYSTEM_PROMPT = """You are Satark, a fraud analyst protecting ordinary people in India from scams \
(digital arrest, KYC/account block, courier/customs, task jobs, investment groups, UPI refund/QR tricks, \
fake electricity bills, APK malware, impersonated relatives, AI voice clones).

Assess the message the user received. Be calibrated. These are LOW risk (under 15) unless something else \
in the message is wrong: bank or UPI debit/credit alerts with an account suffix and reference number \
(including the standard "if this wasn't you, call the bank" line); OTP messages that tell you not to share \
the code; delivery and order updates; official notices whose links are on the real organisation's own \
domain; and a friend or family member asking for a small amount in an ordinary, specific, unhurried way.
Raise risk for concrete red flags: pressure or threats, requests for an OTP/PIN/password, remote-access or \
app installs, payment to a stranger or unknown account, prizes or guaranteed returns, officials demanding \
money, look-alike links, secrecy, or a relative writing from a "new number". Scams can also contain NO \
obvious keywords (romance and gift-card asks, recruiters charging for kits, wrong-number chats that turn \
to investing, fake fee deadlines, distressed "it's me" texts with no amount, threats to message your \
contacts): judge the pattern, not just the keywords. Do not invent facts that are not in the message.

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


def _flag(name: str) -> bool:
    return os.getenv(name, "false").lower() in {"1", "true", "yes"}


def vision_enabled() -> bool:
    """True if some configured model can read screenshots: the primary (LLM_VISION), or the fallback when
    it is marked LLM_FALLBACK_VISION. A text-only primary with a vision fallback still reads screenshots."""
    fallback = bool(os.getenv("LLM_FALLBACK_MODEL", "").strip()) and _flag("LLM_FALLBACK_VISION")
    return _flag("LLM_VISION") or fallback


# In-memory LRU of successful judgements, so re-checking the same message (demo re-runs, a family
# forwarding the same scam) is instant and costs no quota. Not persisted: it dies with the process.
CACHE_SIZE = 256
_cache: OrderedDict[tuple[str, str, str], dict] = OrderedDict()


def clear_cache() -> None:
    _cache.clear()


def _cache_key(text: str, lang: str, model: str, image: bytes | None) -> tuple[str, str, str]:
    digest = hashlib.sha256(text.encode("utf-8"))
    if image is not None:  # two different screenshots with no text must not share an entry
        digest.update(b"\0image\0" + image)
    return digest.hexdigest(), lang, model


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

PROVIDER_LABELS = {
    "generativelanguage.googleapis.com": "gemini",
    "api.groq.com": "groq",
    "api.openai.com": "openai",
    "openrouter.ai": "openrouter",
}


def provider_label(base: str) -> str:
    """Short name for the provider behind a base URL, safe to show the client (never the key or path)."""
    host = urlparse(base).netloc or "unknown"
    return PROVIDER_LABELS.get(host, host)


@dataclass(frozen=True)
class Attempt:
    """One model on one provider. The fallback may be a different provider with its own key."""
    base: str
    api_key: str
    model: str
    delays: tuple[float, ...]
    vision: bool  # can read a screenshot (LLM_VISION / LLM_FALLBACK_VISION); text-only models never get the image


def _plan(reading_image: bool) -> list[Attempt]:
    """Models to try, in order: the primary, then the fallback if one is configured.

    `reading_image` means a screenshot-only request: there is no text for a text-only model to work from, so
    only vision-capable models are kept. Whichever model ends up first gets the one retry on 500/503."""
    base = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    key, model = os.environ["LLM_API_KEY"], os.environ["LLM_MODEL"]
    plan = [Attempt(base, key, model, (), vision=_flag("LLM_VISION"))]

    fb_model = os.getenv("LLM_FALLBACK_MODEL", "").strip()
    if fb_model:
        fb_base = (os.getenv("LLM_FALLBACK_BASE_URL", "").strip() or base).rstrip("/")
        fb_key = os.getenv("LLM_FALLBACK_API_KEY", "").strip()
        if not fb_key and fb_base != base:  # never hand the primary provider's key to a different host
            log.warning("LLM fallback skipped: LLM_FALLBACK_BASE_URL is a different provider but "
                        "LLM_FALLBACK_API_KEY is not set")
        elif (fb_base, fb_model) != (base, model):
            plan.append(Attempt(fb_base, fb_key or key, fb_model, (), vision=_flag("LLM_FALLBACK_VISION")))

    if reading_image:
        plan = [a for a in plan if a.vision]
    if plan:
        plan[0] = replace(plan[0], delays=RETRY_DELAYS)
    return plan


async def _ask(client: httpx.AsyncClient, attempt: Attempt, messages: list[dict]) -> dict:
    """One model on one provider: call it (with its retries), parse the JSON. Raises LLMError."""
    payload = {
        "model": attempt.model,
        "temperature": 0.1,
        # Thinking models spend part of this budget on hidden reasoning; too small and the JSON gets cut off.
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "2048")),
        "messages": messages,
    }
    headers = {"Authorization": f"Bearer {attempt.api_key}"}
    parsed = _parse_json(await _complete(client, attempt.base, headers, payload, attempt.delays))
    if parsed is None:
        _log_failure(urlparse(attempt.base).netloc or "unknown", attempt.model, 200, "unparseable_json", 1, 1)
        raise LLMError("bad_response")
    parsed["provider"] = provider_label(attempt.base)
    return parsed


async def analyse(text: str, lang: str, rule_hints: list[dict],
                  image: bytes | None = None, image_mime: str = "image/png") -> dict | None:
    """Returns the parsed LLM judgement (with "provider" naming who answered), or None if no LLM is configured.

    Raises LLMError if it is configured but failed, so the caller can fall back to rules and say why.
    """
    if not is_configured():
        return None

    if image is not None and not text.strip() and not vision_enabled():
        # Nothing to read and nothing that can read it: don't ask a text model about "(see image)".
        log.warning("LLM call failed: screenshot-only request but no configured model can read images")
        raise LLMError("unavailable")

    sees_image = image is not None and vision_enabled()
    key = _cache_key(text, lang, os.environ["LLM_MODEL"], image if sees_image else None)
    if key in _cache:
        _cache.move_to_end(key)
        return copy.deepcopy(_cache[key])

    hints = ", ".join(h["label"] for h in rule_hints) or "none"
    user_text = (
        f"Explanation language: {LANG_NAMES.get(lang, 'English')}.\n"
        f"Signals our rule engine already found: {hints}.\n\n"
        f"Message received:\n\"\"\"\n{text or '(see image)'}\n\"\"\""
    )
    system = {"role": "system", "content": SYSTEM_PROMPT}
    text_messages = [system, {"role": "user", "content": user_text}]
    vision_messages = text_messages
    if sees_image:
        b64 = base64.b64encode(image).decode()
        vision_messages = [system, {"role": "user", "content": [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{b64}"}},
        ]}]

    # A screenshot-only request (no text to read) can only go to models that can see images.
    reading_image = sees_image and not text.strip()
    plan = _plan(reading_image)
    if not plan:
        log.warning("LLM call failed: no configured model can read a screenshot")
        raise LLMError("unavailable")

    # One deadline for the whole request. A slow primary may only spend its own budget (when there is a
    # fallback to leave time for), so the fallback always gets the remainder instead of a dead clock.
    limit = _total_timeout(reading_image=reading_image)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    result: dict | None = None
    first_error: LLMError | None = None
    async with _client() as client:
        for i, attempt in enumerate(plan):
            remaining = deadline - loop.time()
            if i > 0 and remaining < MIN_FALLBACK_SECONDS:
                log.warning("LLM fallback skipped: only %.1fs of the %ss budget left", max(remaining, 0), limit)
                break
            budget = min(remaining, _primary_timeout()) if i == 0 and len(plan) > 1 else remaining
            messages = vision_messages if attempt.vision else text_messages
            try:
                result = await asyncio.wait_for(_ask(client, attempt, messages), timeout=budget)
                break
            except asyncio.TimeoutError:
                err = LLMError("unavailable")
                log.warning("LLM call failed: provider=%s model=%s status=- error=timeout budget=%.1fs",
                            provider_label(attempt.base), attempt.model, budget)
            except LLMError as e:
                err = e
            first_error = first_error or err  # the primary model's failure is the one worth reporting
            if i + 1 < len(plan):
                log.warning("LLM falling back: from=%s/%s to=%s/%s reason=%s",
                            provider_label(attempt.base), attempt.model,
                            provider_label(plan[i + 1].base), plan[i + 1].model, err.reason)
    if result is None:
        raise first_error  # type: ignore[misc]  (set by the failed primary, which always runs first)

    _cache[key] = copy.deepcopy(result)  # failures are never cached
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return result
