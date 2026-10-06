"""LLM layer failure paths, with httpx mocked so nothing touches the network."""
import asyncio
import json
import logging
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import llm
from app.main import app

KEY = "sk-test-secret-key"
MESSAGE = "Your SBI account will be blocked, update KYC now"

GOOD = {"risk": 80, "scam_type": "KYC scam", "red_flags": [{"quote": "update KYC", "why": "x"}],
        "explanation": "Banks don't do this.", "extracted_text": ""}


def chat(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.fixture
def wire(monkeypatch):
    """Configure a fake provider. Call wire(handler) with a function Request -> Response."""
    monkeypatch.setenv("LLM_API_KEY", KEY)
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1")
    sleeps: list[float] = []
    calls: list[httpx.Request] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(llm, "_sleep", fake_sleep)

    def install(handler):
        def recording(request):
            calls.append(request)
            return handler(request)

        monkeypatch.setattr(llm, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(recording)))
        return calls, sleeps

    return install


def run_analyse():
    return asyncio.run(llm.analyse(MESSAGE, "en", []))


def failure_reason() -> str:
    with pytest.raises(llm.LLMError) as exc:
        run_analyse()
    return exc.value.reason


def test_unconfigured_returns_none():
    assert run_analyse() is None


def test_success(wire):
    calls, sleeps = wire(lambda r: chat(json.dumps(GOOD)))
    assert run_analyse()["risk"] == 80
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize("status", [500, 503])
def test_retries_once_then_succeeds(wire, status):
    replies = iter([httpx.Response(status), chat(json.dumps(GOOD))])
    calls, sleeps = wire(lambda r: next(replies))
    assert run_analyse()["risk"] == 80
    assert len(calls) == 2 and sleeps == [2.0]


def test_rate_limit_is_not_retried(wire):
    calls, sleeps = wire(lambda r: httpx.Response(429))
    assert failure_reason() == "rate_limited"
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize("status", [500, 503])
def test_unavailable_after_one_retry(wire, status):
    calls, sleeps = wire(lambda r: httpx.Response(status))
    assert failure_reason() == "unavailable"
    assert len(calls) == 2 and sleeps == [2.0]


def test_total_time_is_capped(wire, monkeypatch):
    async def slow(request):
        await asyncio.sleep(2)
        return chat(json.dumps(GOOD))

    monkeypatch.setenv("LLM_TOTAL_TIMEOUT", "0.1")
    monkeypatch.setattr(llm, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(slow)))
    assert failure_reason() == "unavailable"


def test_default_time_cap_is_about_twelve_seconds():
    assert llm._total_timeout() == 12.0


def test_reading_a_screenshot_gets_a_longer_cap_than_a_text_check(monkeypatch):
    assert llm._total_timeout(reading_image=True) == 18.0   # below the UI's 20s abort
    assert llm._total_timeout(reading_image=True) > llm._total_timeout()
    monkeypatch.setenv("LLM_TOTAL_TIMEOUT_VISION", "25")
    assert llm._total_timeout(reading_image=True) == 25.0


def test_slow_screenshot_read_survives_the_text_cap_but_a_slow_text_check_does_not(wire, monkeypatch):
    async def slow(request):
        await asyncio.sleep(0.3)
        return chat(json.dumps(GOOD))

    monkeypatch.setattr(llm, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(slow)))
    monkeypatch.setenv("LLM_VISION", "true")
    monkeypatch.setenv("LLM_TOTAL_TIMEOUT", "0.1")
    monkeypatch.setenv("LLM_TOTAL_TIMEOUT_VISION", "5")
    assert asyncio.run(llm.analyse("", "en", [], b"fake-png"))["risk"] == 80   # image only: long cap
    with pytest.raises(llm.LLMError):
        asyncio.run(llm.analyse(MESSAGE + " (text only)", "en", []))           # text: short cap


def test_client_error_is_not_retried(wire):
    calls, sleeps = wire(lambda r: httpx.Response(401))
    assert failure_reason() == "unavailable"
    assert len(calls) == 1 and sleeps == []


def test_network_error_is_unavailable(wire):
    def boom(request):
        raise httpx.ConnectError("no route", request=request)

    wire(boom)
    assert failure_reason() == "unavailable"


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"no": "choices"}),
    httpx.Response(200, json={"choices": []}),
    httpx.Response(200, text="<html>gateway page</html>"),
    chat("I'm sorry, I can't help with that."),
    chat("{not valid json}"),
    chat('{"risk": "high"}'),
    chat(""),
])
def test_bad_response(wire, response):
    wire(lambda r: response)
    assert failure_reason() == "bad_response"


def test_code_fences_are_stripped(wire):
    wire(lambda r: chat("```json\n" + json.dumps(GOOD) + "\n```"))
    assert run_analyse()["scam_type"] == "KYC scam"


def test_fences_and_chatter_around_json(wire):
    wire(lambda r: chat("Sure! Here you go:\n```JSON\n" + json.dumps(GOOD) + "\n```\nHope that helps."))
    assert run_analyse()["risk"] == 80


def test_failure_log_has_provider_model_status_but_no_secrets(wire, caplog):
    wire(lambda r: httpx.Response(503))
    with caplog.at_level(logging.WARNING, logger="satark.llm"):
        failure_reason()
    logged = caplog.text
    assert "llm.example.test" in logged and "test-model" in logged and "503" in logged
    assert KEY not in logged and "SBI" not in logged and "KYC" not in logged


def test_token_budget_leaves_room_for_thinking_models(wire, monkeypatch):
    calls, _ = wire(lambda r: chat(json.dumps(GOOD)))
    run_analyse()
    assert json.loads(calls[0].content)["max_tokens"] >= 2048
    monkeypatch.setenv("LLM_MAX_TOKENS", "4096")
    asyncio.run(llm.analyse(MESSAGE + " (second message, not cached)", "en", []))
    assert json.loads(calls[1].content)["max_tokens"] == 4096


def test_system_prompt_calibrates_bank_alerts_and_keeps_keywordless_scams_in_view(wire):
    calls, _ = wire(lambda r: chat(json.dumps(GOOD)))
    run_analyse()
    system = json.loads(calls[0].content)["messages"][0]["content"].lower()
    # genuine look-alikes the model over-scored in the first eval: UPI alerts and a friend's small ask
    assert "debit/credit alerts" in system and "friend or family member asking for a small amount" in system
    # and scams with no keyword must stay in view, so calibration can't make it lenient
    assert "no obvious keywords" in system and "judge the pattern" in system


# --- cache -----------------------------------------------------------------------------------

def ask(text=MESSAGE, lang="en", image=None):
    return asyncio.run(llm.analyse(text, lang, [], image))


def test_repeat_check_is_served_from_cache(wire):
    calls, _ = wire(lambda r: chat(json.dumps(GOOD)))
    first, second = ask(), ask()
    assert first == second and len(calls) == 1


def test_cache_is_keyed_on_text_lang_and_model(wire, monkeypatch):
    calls, _ = wire(lambda r: chat(json.dumps(GOOD)))
    ask()
    ask(text=MESSAGE + " please")        # different text
    ask(lang="hi")                       # different language
    monkeypatch.setenv("LLM_MODEL", "other-model")
    ask()                                # different model
    assert len(calls) == 4
    monkeypatch.setenv("LLM_MODEL", "test-model")
    ask(), ask(lang="hi")                # back on the first model, both are still cached
    assert len(calls) == 4


def test_different_screenshots_do_not_share_an_entry(wire, monkeypatch):
    monkeypatch.setenv("LLM_VISION", "true")
    calls, _ = wire(lambda r: chat(json.dumps(GOOD)))
    ask(text="", image=b"image-one")
    ask(text="", image=b"image-two")
    ask(text="", image=b"image-one")
    assert len(calls) == 2


def test_failures_are_not_cached(wire):
    replies = iter([httpx.Response(429), chat(json.dumps(GOOD))])
    calls, _ = wire(lambda r: next(replies))
    assert failure_reason() == "rate_limited"
    assert ask()["risk"] == 80
    assert len(calls) == 2


def test_cache_returns_independent_copies(wire):
    wire(lambda r: chat(json.dumps(GOOD)))
    ask()["red_flags"].clear()
    assert ask()["red_flags"] != []


def test_cache_evicts_least_recently_used(wire, monkeypatch):
    monkeypatch.setattr(llm, "CACHE_SIZE", 2)
    calls, _ = wire(lambda r: chat(json.dumps(GOOD)))
    ask("one"), ask("two")
    ask("one")                           # touch "one": "two" is now the oldest
    ask("three")                         # evicts "two"
    assert len(calls) == 3
    ask("one")                           # still cached
    assert len(calls) == 3
    ask("two")                           # was evicted, so it calls out again
    assert len(calls) == 4


def test_default_cache_size_is_256():
    assert llm.CACHE_SIZE == 256


# --- fallback model --------------------------------------------------------------------------

def model_of(request: httpx.Request) -> str:
    return json.loads(request.content)["model"]


def test_fallback_model_used_after_primary_exhausts_retries(wire, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "backup-model")
    calls, sleeps = wire(lambda r: httpx.Response(503) if model_of(r) == "test-model" else chat(json.dumps(GOOD)))
    assert run_analyse()["risk"] == 80
    assert [model_of(c) for c in calls] == ["test-model"] * 2 + ["backup-model"]
    assert sleeps == [2.0]


def test_rate_limit_goes_straight_to_fallback(wire, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "backup-model")
    calls, sleeps = wire(lambda r: httpx.Response(429) if model_of(r) == "test-model" else chat(json.dumps(GOOD)))
    assert run_analyse()["risk"] == 80
    assert [model_of(c) for c in calls] == ["test-model", "backup-model"] and sleeps == []


def test_fallback_gets_one_attempt_only(wire, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "backup-model")
    calls, sleeps = wire(lambda r: httpx.Response(429) if model_of(r) == "test-model" else httpx.Response(503))
    assert failure_reason() == "rate_limited"  # the primary's failure is the one reported
    assert [model_of(c) for c in calls] == ["test-model", "backup-model"] and sleeps == []


def test_fallback_used_when_primary_reply_is_unreadable(wire, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "backup-model")
    calls, _ = wire(lambda r: chat("no json here") if model_of(r) == "test-model" else chat(json.dumps(GOOD)))
    assert run_analyse()["risk"] == 80
    assert [model_of(c) for c in calls] == ["test-model", "backup-model"]


def test_fallback_not_called_when_primary_works(wire, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "backup-model")
    calls, _ = wire(lambda r: chat(json.dumps(GOOD)))
    run_analyse()
    assert [model_of(c) for c in calls] == ["test-model"]


def test_fallback_same_as_primary_is_ignored(wire, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "test-model")
    calls, _ = wire(lambda r: httpx.Response(503))
    assert failure_reason() == "unavailable"
    assert len(calls) == 2


# --- cross-provider fallback -----------------------------------------------------------------

BACKUP_KEY = "gsk-backup-secret"


@pytest.fixture
def cross(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("LLM_FALLBACK_API_KEY", BACKUP_KEY)
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "backup-model")


def by_host(request: httpx.Request) -> str:
    return request.url.host


def primary_down_backup_up(request):
    return httpx.Response(503) if by_host(request) == "llm.example.test" else chat(json.dumps(GOOD))


def test_fallback_on_another_provider_uses_its_own_url_key_and_model(wire, cross):
    calls, sleeps = wire(primary_down_backup_up)
    result = run_analyse()
    assert result["risk"] == 80 and result["provider"] == "groq"
    assert [by_host(c) for c in calls] == ["llm.example.test"] * 2 + ["api.groq.com"]
    last = calls[-1]
    assert model_of(last) == "backup-model"
    assert last.headers["authorization"] == f"Bearer {BACKUP_KEY}"
    assert all(KEY not in c.headers["authorization"] for c in calls if by_host(c) == "api.groq.com")


def test_primary_answer_is_labelled_with_its_provider(wire, cross, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    wire(lambda r: chat(json.dumps(GOOD)))
    assert run_analyse()["provider"] == "gemini"


def test_unknown_provider_is_labelled_by_hostname(wire):
    wire(lambda r: chat(json.dumps(GOOD)))
    assert run_analyse()["provider"] == "llm.example.test"


def test_rate_limit_goes_to_the_other_provider(wire, cross):
    calls, sleeps = wire(lambda r: httpx.Response(429) if by_host(r) == "llm.example.test" else chat(json.dumps(GOOD)))
    assert run_analyse()["provider"] == "groq"
    assert [by_host(c) for c in calls] == ["llm.example.test", "api.groq.com"] and sleeps == []


def test_both_providers_down_reports_the_primarys_reason(wire, cross):
    wire(lambda r: httpx.Response(429) if by_host(r) == "llm.example.test" else httpx.Response(503))
    assert failure_reason() == "rate_limited"


def test_fallback_is_skipped_for_a_screenshot_only_request(wire, cross, monkeypatch):
    monkeypatch.setenv("LLM_VISION", "true")
    calls, _ = wire(lambda r: httpx.Response(429))
    with pytest.raises(llm.LLMError):
        asyncio.run(llm.analyse("", "en", [], b"fake-png"))
    assert [by_host(c) for c in calls] == ["llm.example.test"]  # text-only fallback has nothing to read


def test_fallback_gets_text_only_even_when_a_screenshot_came_with_the_text(wire, cross, monkeypatch):
    monkeypatch.setenv("LLM_VISION", "true")
    seen = {}

    def handler(request):
        seen[by_host(request)] = json.loads(request.content)["messages"][1]["content"]
        return httpx.Response(503) if by_host(request) == "llm.example.test" else chat(json.dumps(GOOD))

    wire(handler)
    asyncio.run(llm.analyse(MESSAGE, "en", [], b"fake-png"))
    assert isinstance(seen["llm.example.test"], list)   # primary got text + image
    assert isinstance(seen["api.groq.com"], str)        # fallback got plain text


def test_primary_key_is_never_sent_to_a_different_provider_without_its_own_key(wire, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "backup-model")   # note: no LLM_FALLBACK_API_KEY
    calls, _ = wire(lambda r: httpx.Response(503))
    assert failure_reason() == "unavailable"
    assert {by_host(c) for c in calls} == {"llm.example.test"}


def test_same_provider_fallback_reuses_the_primary_key(wire, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "backup-model")
    calls, _ = wire(lambda r: httpx.Response(503) if model_of(r) == "test-model" else chat(json.dumps(GOOD)))
    run_analyse()
    assert calls[-1].headers["authorization"] == f"Bearer {KEY}"


# --- per-stage time budgets ------------------------------------------------------------------
# Real (short) sleeps through an async mock transport, so the deadline arithmetic is what's tested.

def slow_world(monkeypatch, primary_s: float, backup_s: float):
    """Primary and backup providers that each take a fixed time to answer. Returns the list of hosts called."""
    called: list[str] = []

    async def handler(request):
        host = request.url.host
        called.append(host)
        await asyncio.sleep(primary_s if host == "llm.example.test" else backup_s)
        return chat(json.dumps(GOOD))

    monkeypatch.setattr(llm, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return called


def timed(coro_factory):
    start = time.perf_counter()
    try:
        return coro_factory(), time.perf_counter() - start
    except llm.LLMError as e:
        return e, time.perf_counter() - start


@pytest.fixture
def budgets(monkeypatch, wire, cross):
    def setup(total: float, primary: float):
        monkeypatch.setenv("LLM_TOTAL_TIMEOUT", str(total))
        monkeypatch.setenv("LLM_PRIMARY_TIMEOUT", str(primary))
    return setup


def test_default_budgets_leave_the_fallback_room():
    assert llm._primary_timeout() == 7.0
    assert llm._primary_timeout() < llm._total_timeout()


def test_a_slow_primary_still_leaves_the_fallback_time_to_answer(monkeypatch, budgets):
    budgets(total=3.0, primary=0.2)
    called = slow_world(monkeypatch, primary_s=5.0, backup_s=0.1)   # the old code would have burned the whole cap
    result, took = timed(run_analyse)
    assert result["provider"] == "groq" and result["risk"] == 80
    assert called == ["llm.example.test", "api.groq.com"]          # primary tried once, then the fallback
    assert took < 1.0                                                # ~0.3s, nowhere near the 3s cap


def test_the_fallback_gets_the_remainder_not_a_budget_of_its_own(monkeypatch, budgets):
    budgets(total=1.0, primary=0.3)
    slow_world(monkeypatch, primary_s=5.0, backup_s=0.5)            # 0.7s left after the primary: just enough
    result, took = timed(run_analyse)
    assert result["provider"] == "groq"
    assert took < 1.1


def test_a_fallback_slower_than_the_remainder_is_cut_off_at_the_total_cap(monkeypatch, budgets):
    budgets(total=1.0, primary=0.3)
    called = slow_world(monkeypatch, primary_s=5.0, backup_s=3.0)
    err, took = timed(run_analyse)
    assert isinstance(err, llm.LLMError) and err.reason == "unavailable"
    assert called == ["llm.example.test", "api.groq.com"]            # the fallback did get its turn...
    assert 0.8 < took < 1.4                                          # ...and the overall cap is respected, not exceeded


def test_without_a_fallback_the_primary_gets_the_whole_cap(monkeypatch, wire):
    monkeypatch.setenv("LLM_TOTAL_TIMEOUT", "2.0")
    monkeypatch.setenv("LLM_PRIMARY_TIMEOUT", "0.1")                 # irrelevant: nobody is waiting behind it
    slow_world(monkeypatch, primary_s=0.4, backup_s=0.0)
    assert run_analyse()["provider"] == "llm.example.test"


def test_fallback_is_not_started_when_the_primary_used_up_the_clock(monkeypatch, budgets):
    budgets(total=0.6, primary=5.0)                                  # primary budget is capped by what remains
    called = slow_world(monkeypatch, primary_s=5.0, backup_s=0.1)
    err, took = timed(run_analyse)
    assert isinstance(err, llm.LLMError)
    assert called == ["llm.example.test"]                            # < 0.5s left, so no pointless fallback call
    assert took < 1.1


def test_a_fast_primary_is_untouched_by_the_budgets(monkeypatch, budgets):
    budgets(total=3.0, primary=0.2)
    called = slow_world(monkeypatch, primary_s=0.05, backup_s=0.05)
    assert run_analyse()["provider"] == "llm.example.test"
    assert called == ["llm.example.test"]


def test_a_timed_out_primary_is_logged_without_secrets(monkeypatch, budgets, caplog):
    budgets(total=3.0, primary=0.1)
    slow_world(monkeypatch, primary_s=5.0, backup_s=0.05)
    with caplog.at_level(logging.WARNING, logger="satark.llm"):
        run_analyse()
    assert "error=timeout" in caplog.text and "falling back" in caplog.text
    assert KEY not in caplog.text and BACKUP_KEY not in caplog.text and "KYC" not in caplog.text


# --- through the API -------------------------------------------------------------------------

def test_api_reports_which_provider_answered(monkeypatch):
    async def ok(*args, **kwargs):
        return {**GOOD, "provider": "groq"}

    monkeypatch.setattr(llm, "analyse", ok)
    body = TestClient(app).post("/api/check", data={"text": MESSAGE}).json()
    assert body["ai_provider"] == "groq"


def test_api_ai_provider_is_null_without_ai():
    body = TestClient(app).post("/api/check", data={"text": "Are we meeting at 8?"}).json()
    assert body["ai_provider"] is None

client = TestClient(app)


@pytest.mark.parametrize("reason", ["rate_limited", "unavailable", "bad_response"])
def test_api_exposes_ai_error_and_falls_back_to_rules(monkeypatch, reason):
    async def failing(*args, **kwargs):
        raise llm.LLMError(reason)

    monkeypatch.setattr(llm, "analyse", failing)
    body = client.post("/api/check", data={"text": MESSAGE + " http://sbi-kyc.xyz"}).json()
    assert body["ai_used"] is False and body["ai_error"] == reason
    assert body["verdict"] == "scam"  # the rules answer on their own


def test_api_no_ai_error_when_ai_works(monkeypatch):
    async def ok(*args, **kwargs):
        return dict(GOOD)

    monkeypatch.setattr(llm, "analyse", ok)
    body = client.post("/api/check", data={"text": MESSAGE}).json()
    assert body["ai_used"] is True and body["ai_error"] is None


def test_api_no_ai_error_when_unconfigured():
    body = client.post("/api/check", data={"text": "Are we meeting at 8?"}).json()
    assert body["ai_used"] is False and body["ai_error"] is None


def test_unreadable_screenshot_is_an_error_not_an_all_clear(monkeypatch):
    async def failing(*args, **kwargs):
        raise llm.LLMError("unavailable")

    monkeypatch.setattr(llm, "analyse", failing)
    monkeypatch.setattr("app.main.ocr.available", lambda: False)
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "vision_enabled", lambda: True)
    r = client.post("/api/check", files={"image": ("shot.png", b"\x89PNG fake", "image/png")})
    assert r.status_code == 503
