"""Screenshot input: the vision LLM reads the image, the rules then check the text it found."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import llm
from app.main import app

client = TestClient(app)
ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "samples" / "screenshots"
SAMPLES = {s["id"]: s for s in json.loads((ROOT / "samples" / "messages.json").read_text("utf-8"))}
PNG = b"\x89PNG\r\n\x1a\n" + b"fake"


@pytest.fixture
def vision_on(monkeypatch):
    """A server with a vision model and no local OCR."""
    monkeypatch.setattr("app.main.ocr.available", lambda: False)
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "vision_enabled", lambda: True)


def reads(text: str, seen: dict | None = None, risk: int = 90):
    """A fake vision response that 'reads' `text` out of the image."""
    async def fake(msg_text, lang, hints, image=None, image_mime="image/png"):
        if seen is not None:
            seen.update(text=msg_text, image=image, mime=image_mime, lang=lang)
        return {"risk": risk, "scam_type": "none", "red_flags": [], "explanation": "ok", "extracted_text": text,
                "provider": "gemini"}
    return fake


def upload(data=PNG, mime="image/png", **form):
    return client.post("/api/check", data=form, files={"image": ("shot.png", data, mime)})


def test_vision_text_is_checked_by_the_rules(vision_on, monkeypatch):
    seen: dict = {}
    text = SAMPLES["kyc-sms"]["text"]
    monkeypatch.setattr(llm, "analyse", reads(text, seen))
    body = upload(lang="hi").json()
    assert seen["text"] == "" and seen["image"] == PNG and seen["mime"] == "image/png" and seen["lang"] == "hi"
    assert body["analysed_text"] == text                                 # shown back to the user
    assert any(s["id"] == "url_lookalike" for s in body["signals"])      # rules ran on the extracted text
    assert "http://sbi-kyc-update.xyz/login" in body["highlights"]
    assert body["verdict"] == "scam" and body["ai_provider"] == "gemini"


@pytest.mark.parametrize("name,sample_id,verdict", [
    ("kyc-sms.png", "kyc-sms", "scam"),
    ("family-hindi-whatsapp.png", "family-hindi-new-number", "scam"),
    ("bank-otp-genuine.png", "bank-otp", "low"),
])
def test_generated_screenshots_end_to_end_with_a_faithful_reader(vision_on, monkeypatch, name, sample_id, verdict):
    data = (SHOTS / name).read_bytes()
    assert data.startswith(b"\x89PNG")
    risk = 5 if verdict == "low" else 90
    monkeypatch.setattr(llm, "analyse", reads(SAMPLES[sample_id]["text"], risk=risk))
    body = upload(data=data).json()
    assert body["verdict"] == verdict and body["analysed_text"] == SAMPLES[sample_id]["text"]


@pytest.mark.parametrize("reason", ["unavailable", "rate_limited", "bad_response"])
def test_unreadable_screenshot_gets_the_friendly_503(vision_on, monkeypatch, reason):
    async def failing(*args, **kwargs):
        raise llm.LLMError(reason)

    monkeypatch.setattr(llm, "analyse", failing)
    r = upload()
    assert r.status_code == 503 and "Couldn't read that screenshot" in r.json()["detail"]


def test_vision_that_finds_no_text_is_a_503_not_an_all_clear(vision_on, monkeypatch):
    monkeypatch.setattr(llm, "analyse", reads(""))
    assert upload().status_code == 503


def test_screenshot_without_vision_or_ocr_explains_itself(monkeypatch):
    monkeypatch.setattr("app.main.ocr.available", lambda: False)
    r = upload()
    assert r.status_code == 422 and "Paste the message text" in r.json()["detail"]


def test_wrong_type_and_oversize_images_are_rejected(vision_on, monkeypatch):
    monkeypatch.setattr(llm, "analyse", reads("x"))
    assert upload(mime="application/pdf").status_code == 415
    assert upload(data=b"0" * (5 * 1024 * 1024 + 1)).status_code == 413


def test_text_plus_screenshot_uses_the_typed_text(vision_on, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(llm, "analyse", reads("", seen))
    upload(text="Are we meeting at 8?")
    assert seen["text"] == "Are we meeting at 8?"
