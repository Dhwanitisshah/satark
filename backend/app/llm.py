"""LLM layer — any OpenAI-compatible endpoint (Gemini by default; Groq, OpenRouter, OpenAI... also work).

The LLM adds judgement the regexes can't: context, tone, novel scripts and plain-language
explanations in the user's language. It is never the only thing deciding the verdict.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import copy
import hashlib
import json
import logging
import os
import re
import socket
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
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
  "scam_type": "<a short human-readable label of 2-4 plain words in the requested language, e.g. 'Fake KYC SMS'; never an identifier with underscores; 'none' if it is not a scam>",
  "red_flags": [{"quote": "<exact words copied from the message>", "why": "<one short sentence, in the requested language>"}],
  "explanation": "<2-3 plain sentences a non-technical person understands, in the requested language>",
  "extracted_text": "<if an image was provided, the text in it; otherwise empty>"
}

Language: write scam_type, every red flag's "why" and the explanation in the requested language (for Hindi or \
Marathi, use Devanagari script). Keep these exactly as written in Latin letters, never translated or \
transliterated: 1930, OTP, UPI, PIN, CVV, KYC, QR, APK, SMS, UPI IDs, links, cybercrime.gov.in, and the names of \
banks, apps and companies. A red flag's "quote" is always copied exactly from the message in its original language \
and script, never translated. extracted_text is the text exactly as written in the image."""


class LLMError(Exception):
    """The LLM was configured but gave us nothing usable. `reason` is safe to show the client:
    "rate_limited" | "unavailable" | "bad_response"."""

    def __init__(self, reason: str, status: int | str | None = None, took_ms: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status = status      # HTTP status, or an error name such as "ConnectError" or "timeout"
        self.took_ms = took_ms
        self.attempts: list[dict] = []   # every failed attempt of the request, filled in by analyse()


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


def _make_client() -> httpx.AsyncClient:
    """An HTTP client tuned for talking to the same two providers all day: keep-alive connections are held for
    a minute so the next check skips DNS, TCP and the TLS handshake."""
    timeout = httpx.Timeout(float(os.getenv("LLM_TIMEOUT", "40")), connect=float(os.getenv("LLM_CONNECT_TIMEOUT", "10")))
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10,
                          keepalive_expiry=float(os.getenv("LLM_KEEPALIVE_SECONDS", "60")))
    if _flag("LLM_FORCE_IPV4"):
        # Opt-in. Binding the local side to 0.0.0.0 makes the socket IPv4-only. A host that publishes IPv6
        # addresses but has no working IPv6 route can stall each new connection on the dead address before it
        # falls back to IPv4. Turn this on only if the logged connect time shows that stall.
        return httpx.AsyncClient(timeout=timeout,
                                 transport=httpx.AsyncHTTPTransport(limits=limits, local_address="0.0.0.0"))
    return httpx.AsyncClient(timeout=timeout, limits=limits)


def _client() -> httpx.AsyncClient:
    return _make_client()


# The one long-lived client, created at app startup and closed at shutdown (see startup()/shutdown()).
# Without it (scripts, the eval, tests) each call builds a short-lived client instead.
_shared: httpx.AsyncClient | None = None
_warm_task: asyncio.Task | None = None


@asynccontextmanager
async def _use_client():
    if _shared is not None:
        yield _shared  # never closed per request: that is the point
    else:
        async with _client() as client:
            yield client


_sleep = asyncio.sleep  # indirection so tests can skip the backoff


class _Trace:
    """Collects httpcore's connection events for one request, so a slow call can be split into DNS+connect,
    TLS and time-to-first-byte. Holds only timestamps: never a URL, header, key or message text."""

    def __init__(self) -> None:
        self.at: dict[str, float] = {}

    async def __call__(self, event: str, info: dict) -> None:
        self.at.setdefault(event, time.perf_counter())

    def _ms(self, start: str, end: str) -> int | None:
        if start in self.at and end in self.at:
            return round((self.at[end] - self.at[start]) * 1000)
        return None

    def summary(self) -> dict:
        return {
            # connect_tcp covers resolving the name AND opening the socket (anyio does both); None = pooled connection reused
            "connect_ms": self._ms("connection.connect_tcp.started", "connection.connect_tcp.complete"),
            "tls_ms": self._ms("connection.start_tls.started", "connection.start_tls.complete"),
            # request fully sent -> response headers back: the provider's think time plus network round trip
            "ttfb_ms": self._ms("http11.send_request_body.complete", "http11.receive_response_headers.complete"),
            "reused": "connection.connect_tcp.started" not in self.at,
        }


def configure_logging() -> None:
    """Make INFO lines from this module visible (Python only shows WARNING+ for a logger nobody configured)."""
    logger = logging.getLogger("satark")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


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


def _log_failure(provider: str, model: str, status: int | str, error: str, attempt: int, attempts: int,
                 took_ms: int | None = None) -> None:
    # Deliberately no key, URL, request body or message text here.
    log.warning("LLM call failed: provider=%s model=%s status=%s error=%s attempt=%d/%d took=%sms",
                provider, model, status, error, attempt, attempts, "-" if took_ms is None else took_ms)


@dataclass
class Reply:
    content: str
    timing: dict  # connect_ms, tls_ms, ttfb_ms, total_ms, reused, prompt_tokens, completion_tokens, reasoning_tokens


def _tokens(body: dict) -> dict:
    """Token counts from an OpenAI-style `usage` block; None where the provider doesn't say."""
    usage = body.get("usage") if isinstance(body, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("completion_tokens_details")
    details = details if isinstance(details, dict) else {}
    return {"prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens")}


async def _complete(client: httpx.AsyncClient, base: str, headers: dict, payload: dict,
                    delays: tuple[float, ...] = RETRY_DELAYS) -> Reply:
    """POST one chat completion, retrying transient errors after each wait in `delays`.
    Returns the reply text with its timing, or raises LLMError."""
    provider, model = provider_label(base), payload["model"]
    attempts = len(delays) + 1
    for attempt in range(1, attempts + 1):
        trace, started = _Trace(), time.perf_counter()
        try:
            r = await client.post(f"{base}/chat/completions", json=payload, headers=headers,
                                  extensions={"trace": trace})
        except httpx.HTTPError as e:
            took = _since(started)
            _log_failure(provider, model, "-", type(e).__name__, attempt, attempts, took)
            raise LLMError("unavailable", type(e).__name__, took) from e
        took = _since(started)

        if r.status_code == 429:
            _log_failure(provider, model, 429, "rate_limited", attempt, attempts, took)
            raise LLMError("rate_limited", 429, took)
        if r.status_code in RETRY_STATUSES:
            _log_failure(provider, model, r.status_code, "unavailable", attempt, attempts, took)
            if attempt < attempts:
                await _sleep(delays[attempt - 1])
                continue
            raise LLMError("unavailable", r.status_code, took)
        if r.status_code >= 400:  # bad key, bad model name, ...: retrying won't help
            _log_failure(provider, model, r.status_code, "http_error", attempt, attempts, took)
            raise LLMError("unavailable", r.status_code, took)

        try:
            body = r.json()
            content = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError, ValueError) as e:
            _log_failure(provider, model, r.status_code, type(e).__name__, attempt, attempts, took)
            raise LLMError("bad_response", r.status_code, took) from e
        timing = {**trace.summary(), "total_ms": took, **_tokens(body)}
        log.info("LLM call ok: provider=%s model=%s status=%s connect=%sms tls=%sms ttfb=%sms total=%sms reused=%s "
                 "prompt_tokens=%s completion_tokens=%s reasoning_tokens=%s",
                 provider, model, r.status_code, timing["connect_ms"], timing["tls_ms"], timing["ttfb_ms"], took,
                 timing["reused"], timing["prompt_tokens"], timing["completion_tokens"], timing["reasoning_tokens"])
        return Reply(content, timing)
    raise LLMError("unavailable")  # pragma: no cover (loop always returns or raises)


def _since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)

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
    reply = await _complete(client, attempt.base, headers, payload, attempt.delays)
    parsed = _parse_json(reply.content)
    if parsed is None:
        _log_failure(provider_label(attempt.base), attempt.model, 200, "unparseable_json", 1, 1,
                     reply.timing["total_ms"])
        raise LLMError("bad_response", 200, reply.timing["total_ms"])
    parsed["provider"] = provider_label(attempt.base)
    parsed["timing"] = {"provider": parsed["provider"], "model": attempt.model, **reply.timing}
    return parsed


async def _resolve(host: str) -> list:
    """Look the host up, the way the connection will (a separate function so tests don't touch real DNS)."""
    return await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)


async def _warm_up(client: httpx.AsyncClient) -> None:
    """One cheap request to each provider so the first real check doesn't pay for DNS, TCP and TLS, and so the
    log shows how long each of those takes from this machine. GET /models costs no tokens. Never raises."""
    targets: dict[str, str] = {}
    for attempt in _plan(False):
        targets.setdefault(attempt.base, attempt.api_key)
    for base, key in targets.items():
        label = provider_label(base)
        try:
            started = time.perf_counter()
            addresses = await _resolve(urlparse(base).hostname or "")
            dns_ms = _since(started)
            v6 = sum(1 for a in addresses if a[0] == socket.AF_INET6)
            trace, started = _Trace(), time.perf_counter()
            r = await asyncio.wait_for(client.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"},
                                                  extensions={"trace": trace}), timeout=15)
            t = trace.summary()
            log.info("LLM warm-up: provider=%s status=%s dns=%sms ipv4=%d ipv6=%d connect=%sms tls=%sms ttfb=%sms "
                     "total=%sms", label, r.status_code, dns_ms, len(addresses) - v6, v6, t["connect_ms"],
                     t["tls_ms"], t["ttfb_ms"], _since(started))
        except Exception as e:  # noqa: BLE001  (a warm-up problem must never stop the app from starting)
            log.warning("LLM warm-up failed: provider=%s error=%s", label, type(e).__name__)


async def startup() -> None:
    """Create the shared client and start warming it up in the background (startup doesn't wait for it)."""
    global _shared, _warm_task
    configure_logging()
    if _shared is None and is_configured():
        _shared = _client()
        _warm_task = asyncio.create_task(_warm_up(_shared))


async def shutdown() -> None:
    global _shared, _warm_task
    if _warm_task is not None:
        _warm_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _warm_task
        _warm_task = None
    if _shared is not None:
        await _shared.aclose()
        _shared = None


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
        hit = copy.deepcopy(_cache[key])
        hit["timing"] = {"cached": True}   # the stored timing described the original call, not this one
        return hit

    hints = ", ".join(h["label"] for h in rule_hints) or "none"
    user_text = (
        f"Requested language for scam_type, red-flag reasons and explanation: {LANG_NAMES.get(lang, 'English')}.\n"
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
    failures: list[dict] = []   # what went wrong with each attempt: reported so an outage can be diagnosed from outside
    async with _use_client() as client:
        for i, attempt in enumerate(plan):
            remaining = deadline - loop.time()
            if i > 0 and remaining < MIN_FALLBACK_SECONDS:
                log.warning("LLM fallback skipped: only %.1fs of the %ss budget left", max(remaining, 0), limit)
                break
            budget = min(remaining, _primary_timeout()) if i == 0 and len(plan) > 1 else remaining
            messages = vision_messages if attempt.vision else text_messages
            try:
                result = await asyncio.wait_for(_ask(client, attempt, messages), timeout=budget)
                result["timing"].update(attempt=i + 1, request_ms=round((limit - (deadline - loop.time())) * 1000),
                                        failed_attempts=failures)
                break
            except asyncio.TimeoutError:
                err = LLMError("unavailable", "timeout", round(budget * 1000))
                log.warning("LLM call failed: provider=%s model=%s status=- error=timeout budget=%.1fs",
                            provider_label(attempt.base), attempt.model, budget)
            except LLMError as e:
                err = e
            failures.append({"provider": provider_label(attempt.base), "model": attempt.model, "reason": err.reason,
                             "status": err.status, "took_ms": err.took_ms})
            first_error = first_error or err  # the primary model's failure is the one worth reporting
            if i + 1 < len(plan):
                log.warning("LLM falling back: from=%s/%s to=%s/%s reason=%s",
                            provider_label(attempt.base), attempt.model,
                            provider_label(plan[i + 1].base), plan[i + 1].model, err.reason)
    if result is None:
        # Always in the server log, whether or not the API response is allowed to carry the details.
        log.warning("LLM gave no answer after %d attempt(s): %s", len(failures),
                    "; ".join(f"{f['provider']}/{f['model']} status={f['status']} took={f['took_ms']}ms"
                              for f in failures) or "no attempt was possible")
        first_error.attempts = failures  # type: ignore[union-attr]  (the failed primary always runs first)
        raise first_error  # type: ignore[misc]

    _cache[key] = copy.deepcopy(result)  # failures are never cached
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return result
