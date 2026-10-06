"""Satark API — POST a suspicious message or screenshot, get a verdict, red flags and next steps."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import llm, ocr
from .rules.engine import run_rules
from .verdict import fuse

try:  # load .env when running locally
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 5000
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

@asynccontextmanager
async def lifespan(_: FastAPI):
    # One long-lived HTTP client (keep-alive, so TLS connections are reused) plus a warm-up request to each
    # AI provider, instead of building a client and opening connections on every check.
    await llm.startup()
    try:
        yield
    finally:
        await llm.shutdown()


app = FastAPI(title="Satark", version="0.1.0", description="India-first AI scam checker", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "llm": llm.is_configured(),
        "vision": llm.is_configured() and llm.vision_enabled(),
        "fallback": llm.is_configured() and bool(os.getenv("LLM_FALLBACK_MODEL", "").strip()),
        "ocr": ocr.available(),
    }


@app.post("/api/check")
async def check(
    text: str = Form(""),
    lang: str = Form("en"),
    ai: bool = Form(True),
    image: UploadFile | None = File(None),
) -> dict:
    """`ai=false` returns the rules-only verdict at once (the UI shows it first, then asks again with ai=true)."""
    text = (text or "").strip()[:MAX_TEXT_CHARS]
    if lang not in llm.LANG_NAMES:
        lang = "en"

    img_bytes: bytes | None = None
    img_mime = "image/png"
    if image is not None and image.filename:
        img_bytes = await image.read()
        img_mime = image.content_type or img_mime
        if len(img_bytes) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "Image is larger than 5 MB.")
        if not img_mime.startswith("image/"):
            raise HTTPException(415, "Please upload a PNG or JPG screenshot.")

    if not text and img_bytes is None:
        raise HTTPException(400, "Paste a message or upload a screenshot.")

    # Screenshot path: local OCR first (fast, free); else rely on a vision LLM to read it.
    if img_bytes is not None and not text:
        if ocr.available():
            text = ocr.image_to_text(img_bytes)[:MAX_TEXT_CHARS]
        elif not ai:
            raise HTTPException(422, "Reading a screenshot needs the AI step. Send it with ai=true.")
        elif not (llm.is_configured() and llm.vision_enabled()):
            raise HTTPException(
                422, "Screenshot reading isn't set up on this server. Paste the message text instead."
            )

    rules = run_rules(text)
    judgement, ai_error, ai_attempts = None, None, None
    if ai:
        try:
            judgement = await llm.analyse(text, lang, rules["signals"], img_bytes, img_mime)
        except llm.LLMError as e:  # rules still answer; tell the client why the AI part is missing
            ai_error, ai_attempts = e.reason, e.attempts

    # A vision model may have read the image for us; re-run rules on that text.
    if not text and judgement and judgement.get("extracted_text"):
        text = judgement["extracted_text"][:MAX_TEXT_CHARS]
        rules = run_rules(text)

    if not text:  # screenshot we couldn't read: "no scam signs" would be a false all-clear
        raise HTTPException(503, "Couldn't read that screenshot right now. Try again, or paste the message text.")

    out = fuse(text, rules, judgement, ai_error)
    if ai_attempts:  # one entry per failed attempt: provider, model, reason, HTTP status or error name, milliseconds
        out["ai_attempts"] = ai_attempts
    return out


if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(FRONTEND / "index.html")
