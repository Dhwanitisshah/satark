from fastapi.testclient import TestClient

from app import llm
from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_tests_never_use_a_real_llm():
    assert llm.is_configured() is False


def test_check_scam_without_llm(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    r = client.post("/api/check", data={"text": "Your SBI account will be blocked. Update KYC: http://sbi-kyc.xyz"})
    body = r.json()
    assert r.status_code == 200
    assert body["verdict"] == "scam"
    assert body["ai_used"] is False
    assert any("1930" in a for a in body["actions"])
    assert "http://sbi-kyc.xyz" in body["highlights"]


def test_check_low_risk():
    r = client.post("/api/check", data={"text": "Are we meeting at 8 tomorrow?"})
    assert r.json()["verdict"] == "low"


def test_empty_request_rejected():
    assert client.post("/api/check", data={"text": "  "}).status_code == 400


def test_llm_raises_but_cannot_lower(monkeypatch):
    async def fake(text, lang, hints, image=None, image_mime="image/png"):
        return {"risk": 5, "scam_type": "none", "red_flags": [{"quote": "invented words", "why": "x"}],
                "explanation": "Looks fine.", "extracted_text": ""}

    monkeypatch.setattr(llm, "analyse", fake)
    body = client.post("/api/check", data={"text": "Install SBI REWARD.apk to redeem points today"}).json()
    assert body["verdict"] == "scam"           # hard rule hit survives a low LLM score
    assert body["llm_flags"] == []             # hallucinated quote is dropped


def test_llm_can_raise_risk(monkeypatch):
    async def fake(text, lang, hints, image=None, image_mime="image/png"):
        return {"risk": 90, "scam_type": "Romance scam", "red_flags": [{"quote": "gift card", "why": "x"}],
                "explanation": "Classic romance scam.", "extracted_text": ""}

    monkeypatch.setattr(llm, "analyse", fake)
    body = client.post("/api/check", data={"text": "Darling, I'm stuck at the airport, buy me a gift card"}).json()
    assert body["verdict"] == "scam" and body["scam_type"] == "Romance scam"
