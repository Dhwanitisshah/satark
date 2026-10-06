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


# --- per-call timing and logs ----------------------------------------------------------------

def with_usage(reasoning=None):
    usage = {"prompt_tokens": 486, "completion_tokens": 219, "total_tokens": 705}
    if reasoning is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(GOOD)}}], "usage": usage})


def test_the_answer_carries_its_timing_and_token_counts(wire):
    wire(lambda r: with_usage(reasoning=247))
    t = run_analyse()["timing"]
    assert t["provider"] == "llm.example.test" and t["model"] == "test-model" and t["attempt"] == 1
    assert t["total_ms"] >= 0 and t["request_ms"] >= t["total_ms"] - 5
    assert (t["prompt_tokens"], t["completion_tokens"], t["reasoning_tokens"]) == (486, 219, 247)
    assert set(t) >= {"connect_ms", "tls_ms", "ttfb_ms", "reused"}      # None here: a mock has no real socket


def test_missing_usage_is_none_not_an_error(wire):
    wire(lambda r: chat(json.dumps(GOOD)))
    t = run_analyse()["timing"]
    assert t["prompt_tokens"] is None and t["completion_tokens"] is None and t["reasoning_tokens"] is None


def test_timing_names_the_model_that_actually_answered(wire, cross):
    wire(primary_down_backup_up)
    t = run_analyse()["timing"]
    assert t["provider"] == "groq" and t["model"] == "backup-model" and t["attempt"] == 2


def test_each_successful_call_is_logged_with_its_numbers_and_nothing_secret(wire, caplog):
    wire(lambda r: with_usage(reasoning=0))
    with caplog.at_level(logging.INFO, logger="satark.llm"):
        run_analyse()
    line = next(r.getMessage() for r in caplog.records if "LLM call ok" in r.getMessage())
    for part in ("provider=llm.example.test", "model=test-model", "status=200", "connect=", "tls=", "ttfb=", "total=",
                 "prompt_tokens=486", "completion_tokens=219", "reasoning_tokens=0", "reused="):
        assert part in line
    assert KEY not in caplog.text and "SBI" not in caplog.text and "KYC" not in caplog.text and "Bearer" not in caplog.text


def test_failures_log_how_long_they_took(wire, caplog):
    wire(lambda r: httpx.Response(401))
    with caplog.at_level(logging.WARNING, logger="satark.llm"):
        failure_reason()
    assert "status=401" in caplog.text and "took=" in caplog.text and "ms" in caplog.text


def test_the_trace_hook_turns_httpcore_events_into_phases():
    trace = llm._Trace()
    clock = iter([0.0, 0.050, 0.060, 0.200, 0.210, 6.900])      # connect 50ms, TLS 140ms, then a 6.69s wait
    events = ["connection.connect_tcp.started", "connection.connect_tcp.complete", "connection.start_tls.started",
              "connection.start_tls.complete", "http11.send_request_body.complete",
              "http11.receive_response_headers.complete"]
    import unittest.mock as mock
    with mock.patch.object(llm.time, "perf_counter", side_effect=lambda: next(clock)):
        for e in events:
            asyncio.run(trace(e, {}))
    s = trace.summary()
    assert (s["connect_ms"], s["tls_ms"], s["ttfb_ms"], s["reused"]) == (50, 140, 6690, False)


def test_a_reused_connection_is_reported_as_reused():
    trace = llm._Trace()
    asyncio.run(trace("http11.send_request_body.complete", {}))
    asyncio.run(trace("http11.receive_response_headers.complete", {}))
    s = trace.summary()
    assert s["reused"] is True and s["connect_ms"] is None and s["tls_ms"] is None and s["ttfb_ms"] is not None


def test_info_lines_become_visible_once_logging_is_configured():
    logger = logging.getLogger("satark")
    before = list(logger.handlers)
    try:
        llm.configure_logging()
        llm.configure_logging()          # idempotent: no duplicate handler
        assert logger.level == logging.INFO and len(logger.handlers) == len(before) + (0 if before else 1)
    finally:
        for h in list(logger.handlers):
            if h not in before:
                logger.removeHandler(h)
        logger.setLevel(logging.NOTSET)


# --- cache -----------------------------------------------------------------------------------

def ask(text=MESSAGE, lang="en", image=None):
    return asyncio.run(llm.analyse(text, lang, [], image))


def test_repeat_check_is_served_from_cache(wire):
    calls, _ = wire(lambda r: chat(json.dumps(GOOD)))
    first, second = ask(), ask()
    assert len(calls) == 1
    assert {k: v for k, v in first.items() if k != "timing"} == {k: v for k, v in second.items() if k != "timing"}
    assert second["timing"] == {"cached": True} and first["timing"]["total_ms"] >= 0   # a hit says it was a hit


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


# --- swapped roles: a fast text-only primary (Groq) with a vision fallback (Gemini) ----------------

GROQ, GEMINI = "api.groq.com", "generativelanguage.googleapis.com"


@pytest.fixture
def swapped(monkeypatch, wire):
    """Primary = Groq (text only). Fallback = Gemini (reads screenshots). Both have their own key."""
    monkeypatch.setenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("LLM_MODEL", "groq-text-model")
    monkeypatch.setenv("LLM_VISION", "false")
    monkeypatch.setenv("LLM_FALLBACK_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    monkeypatch.setenv("LLM_FALLBACK_API_KEY", BACKUP_KEY)
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "gemini-vision-model")
    monkeypatch.setenv("LLM_FALLBACK_VISION", "true")
    return wire


def ok_everywhere(request):
    return chat(json.dumps(GOOD))


def test_text_checks_go_to_the_text_primary_first(swapped):
    calls, _ = swapped(ok_everywhere)
    result = run_analyse()
    assert result["provider"] == "groq" and [by_host(c) for c in calls] == [GROQ]
    assert calls[0].headers["authorization"] == f"Bearer {KEY}"


def test_a_429_from_the_text_primary_goes_straight_to_gemini(swapped):
    calls, sleeps = swapped(lambda r: httpx.Response(429) if by_host(r) == GROQ else chat(json.dumps(GOOD)))
    assert run_analyse()["provider"] == "gemini"
    assert [by_host(c) for c in calls] == [GROQ, GEMINI] and sleeps == []   # no retry, no waiting
    assert calls[1].headers["authorization"] == f"Bearer {BACKUP_KEY}"


def test_a_429_from_gemini_as_the_fallback_ends_in_rules_only(swapped):
    calls, _ = swapped(lambda r: httpx.Response(503) if by_host(r) == GROQ else httpx.Response(429))
    assert failure_reason() == "unavailable"        # the primary's reason is the one reported
    assert [by_host(c) for c in calls] == [GROQ, GROQ, GEMINI]   # Groq: one retry on 503; Gemini: one attempt


def test_screenshot_only_always_goes_to_gemini_and_never_to_the_text_model(swapped, monkeypatch):
    seen = {}

    def handler(request):
        seen[by_host(request)] = json.loads(request.content)["messages"][1]["content"]
        return chat(json.dumps(GOOD))

    calls, _ = swapped(handler)
    result = asyncio.run(llm.analyse("", "en", [], b"fake-png"))
    assert result["provider"] == "gemini"
    assert [by_host(c) for c in calls] == [GEMINI]
    assert any(part.get("type") == "image_url" for part in seen[GEMINI])   # the image really went along


def test_screenshot_only_gemini_gets_the_retry_and_a_429_ends_it_without_touching_groq(swapped):
    replies = iter([httpx.Response(503), chat(json.dumps(GOOD))])
    calls, sleeps = swapped(lambda r: next(replies))
    assert asyncio.run(llm.analyse("", "en", [], b"fake-png"))["provider"] == "gemini"
    assert sleeps == [2.0] and {by_host(c) for c in calls} == {GEMINI}


def test_screenshot_only_rate_limited_gemini_is_a_clean_failure(swapped):
    calls, _ = swapped(lambda r: httpx.Response(429))
    with pytest.raises(llm.LLMError) as exc:
        asyncio.run(llm.analyse("", "en", [], b"fake-png"))
    assert exc.value.reason == "rate_limited" and [by_host(c) for c in calls] == [GEMINI]


def test_text_plus_screenshot_text_primary_gets_text_and_the_fallback_gets_the_image(swapped):
    seen = {}

    def handler(request):
        seen[by_host(request)] = json.loads(request.content)["messages"][1]["content"]
        return httpx.Response(503) if by_host(request) == GROQ else chat(json.dumps(GOOD))

    swapped(handler)
    asyncio.run(llm.analyse(MESSAGE, "en", [], b"fake-png"))
    assert isinstance(seen[GROQ], str)      # the text model never receives image data
    assert isinstance(seen[GEMINI], list)   # Gemini does, because it can read it


def test_no_vision_capable_model_means_a_clean_failure_and_no_calls(swapped, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_VISION", "false")
    calls, _ = swapped(ok_everywhere)
    with pytest.raises(llm.LLMError):
        asyncio.run(llm.analyse("", "en", [], b"fake-png"))
    assert calls == []


def test_vision_enabled_follows_whichever_model_can_see(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "m")
    assert llm.vision_enabled() is False
    monkeypatch.setenv("LLM_FALLBACK_VISION", "true")
    assert llm.vision_enabled() is True                        # only the fallback can see
    monkeypatch.delenv("LLM_FALLBACK_MODEL")
    assert llm.vision_enabled() is False                       # the flag means nothing without a fallback model
    monkeypatch.setenv("LLM_VISION", "true")
    assert llm.vision_enabled() is True                        # the primary can see


def test_a_screenshot_only_read_uses_the_whole_vision_cap_not_the_primary_budget(swapped, monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY_TIMEOUT", "0.1")           # would cut a normal primary short...
    monkeypatch.setenv("LLM_TOTAL_TIMEOUT_VISION", "5")

    async def slow(request):
        await asyncio.sleep(0.4)
        return chat(json.dumps(GOOD))

    monkeypatch.setattr(llm, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(slow)))
    assert asyncio.run(llm.analyse("", "en", [], b"fake-png"))["provider"] == "gemini"   # ...but Gemini is alone here


def test_api_accepts_a_screenshot_when_only_the_fallback_can_read_it(swapped, monkeypatch):
    monkeypatch.setattr("app.main.ocr.available", lambda: False)
    swapped(ok_everywhere)
    assert TestClient(app).get("/api/health").json()["vision"] is True
    r = TestClient(app).post("/api/check", data={"ai": "true"}, files={"image": ("s.png", b"x", "image/png")})
    assert r.status_code in (200, 503)      # accepted (not the 422 "isn't set up"); this fake image has no text


# --- through the API -------------------------------------------------------------------------

def test_api_reports_which_provider_answered(monkeypatch):
    async def ok(*args, **kwargs):
        return {**GOOD, "provider": "groq"}

    monkeypatch.setattr(llm, "analyse", ok)
    body = TestClient(app).post("/api/check", data={"text": MESSAGE}).json()
    assert body["ai_provider"] == "groq"


def test_api_exposes_the_timing_of_the_call_that_answered(monkeypatch):
    timing = {"provider": "groq", "model": "m", "connect_ms": 40, "tls_ms": 90, "ttfb_ms": 700, "total_ms": 900,
              "reused": False, "completion_tokens": 219}

    async def ok(*args, **kwargs):
        return {**GOOD, "provider": "groq", "timing": timing}

    monkeypatch.setattr(llm, "analyse", ok)
    body = TestClient(app).post("/api/check", data={"text": MESSAGE}).json()
    assert body["ai_timing"] == timing


def test_api_ai_timing_is_null_without_ai():
    body = TestClient(app).post("/api/check", data={"text": "Are we meeting at 8?"}).json()
    assert body["ai_timing"] is None


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
