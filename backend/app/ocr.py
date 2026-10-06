"""Optional local OCR for screenshots when the LLM has no vision support.

Needs the Tesseract binary + `pip install pytesseract pillow`. If missing, `available()` is False
and the API tells the user to paste the text or enable a vision model instead.
"""
from __future__ import annotations

import io


def available() -> bool:
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def image_to_text(data: bytes) -> str:
    import pytesseract
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    try:
        return pytesseract.image_to_string(img, lang="eng+hin").strip()
    except pytesseract.TesseractError:  # Hindi language pack not installed
        return pytesseract.image_to_string(img, lang="eng").strip()
