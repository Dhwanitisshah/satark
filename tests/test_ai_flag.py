"""The two-stage flow: ai=false answers from the rules at once, ai=true adds the LLM."""
import pytest
from fastapi.testclient import TestClient

from app import llm
from app.main import app

client = TestClient(app)
SCAM = "Your SBI account will be blocked today. Update KYC: http://sbi-kyc.xyz/login"


@pytest.fixture
def spy(monkeypatch):
    calls = []

    async def fake(text, lang, hints, image=None, image_mime="image/png"):
        calls.append(text)
        return {"risk": 95, "scam_type": "KYC scam", "red_flags": [], "explanation": "AI says scam.",
                "extracted_text": "", "provider": "gemini"}

    monkeypatch.setattr(llm, "analyse", fake)
    return calls


def test_ai_false_skips_the_llm_and_returns_rules_only(spy):
    body = client.post("/api/check", data={"text": SCAM, "ai": "false"}).json()
    assert spy == []
    assert body["ai_used"] is False and body["ai_error"] is None and body["ai_provider"] is None
    assert body["verdict"] == "scam" and body["scores"]["llm"] is None
    assert any("1930" in a for a in body["actions"])


@pytest.mark.parametrize("value", ["false", "False", "0", "no", "off"])
def test_ai_false_spellings(spy, value):
    client.post("/api/check", data={"text": SCAM, "ai": value})
    assert spy == []


@pytest.mark.parametrize("data", [{"text": SCAM}, {"text": SCAM, "ai": "true"}, {"text": SCAM, "ai": "1"}])
def test_ai_defaults_to_true(spy, data):
    body = client.post("/api/check", data=data).json()
    assert spy == [SCAM] and body["ai_used"] is True and body["ai_provider"] == "gemini"


def test_second_stage_can_only_raise_the_risk(spy):
    first = client.post("/api/check", data={"text": SCAM, "ai": "false"}).json()
    second = client.post("/api/check", data={"text": SCAM, "ai": "true"}).json()
    assert second["risk"] >= first["risk"]
    assert first["highlights"] and set(first["highlights"]) <= set(second["highlights"])


def test_ai_false_still_rejects_empty_input(spy):
    assert client.post("/api/check", data={"text": " ", "ai": "false"}).status_code == 400


def test_screenshot_only_with_ai_false_needs_the_ai_step(spy, monkeypatch):
    monkeypatch.setattr("app.main.ocr.available", lambda: False)
    r = client.post("/api/check", data={"ai": "false"}, files={"image": ("s.png", b"x", "image/png")})
    assert r.status_code == 422 and "ai=true" in r.json()["detail"]
    assert spy == []


def test_screenshot_with_local_ocr_works_without_the_ai(spy, monkeypatch):
    monkeypatch.setattr("app.main.ocr.available", lambda: True)
    monkeypatch.setattr("app.main.ocr.image_to_text", lambda data: SCAM)
    body = client.post("/api/check", data={"ai": "false"}, files={"image": ("s.png", b"x", "image/png")}).json()
    assert body["verdict"] == "scam" and body["ai_used"] is False and spy == []
