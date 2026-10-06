"""Render sample messages into chat-style phone screenshots, to test the screenshot (vision) path.

    python scripts/make_screenshots.py            # writes samples/screenshots/*.png

Texts come from samples/messages.json (one source of truth). Needs Pillow and a system font; a Devanagari font
is used for Hindi/Marathi text. The images are synthetic: no real app screenshots or personal data.
"""
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples" / "screenshots"

LATIN_FONTS = ["C:/Windows/Fonts/segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
               "/System/Library/Fonts/Supplemental/Arial.ttf"]
DEVANAGARI_FONTS = ["C:/Windows/Fonts/Nirmala.ttc", "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
                    "/System/Library/Fonts/Kohinoor.ttc"]
DEVANAGARI = re.compile(r"[\u0900-\u097F]")

# id in samples/messages.json -> how to dress it up
SHOTS = [
    {"id": "kyc-sms", "file": "kyc-sms.png", "style": "sms", "sender": "VM-SBIINB", "time": "10:42 AM"},
    {"id": "family-hindi-new-number", "file": "family-hindi-whatsapp.png", "style": "whatsapp",
     "sender": "+91 98765 43210", "time": "11:07 PM"},
    {"id": "bank-otp", "file": "bank-otp-genuine.png", "style": "sms", "sender": "HDFCBK", "time": "3:15 PM"},
]
STYLES = {
    "sms": {"bg": (242, 243, 247), "bar": (255, 255, 255), "bubble": (255, 255, 255), "ink": (28, 28, 30)},
    "whatsapp": {"bg": (236, 229, 221), "bar": (7, 94, 84), "bubble": (255, 255, 255), "ink": (17, 27, 33)},
}
W, PAD = 1080, 48


def load_font(paths: list[str], size: int) -> ImageFont.FreeTypeFont:
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    sys.exit(f"No suitable font found; tried: {paths}")


def wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=font) <= max_w or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def render(text: str, style: str, sender: str, time: str) -> Image.Image:
    s = STYLES[style]
    fonts = DEVANAGARI_FONTS if DEVANAGARI.search(text) else LATIN_FONTS
    body, small, head = load_font(fonts, 40), load_font(fonts, 28), load_font(LATIN_FONTS, 38)
    probe = ImageDraw.Draw(Image.new("RGB", (W, 10)))
    lines = wrap(probe, text, body, W - 2 * PAD - 80)
    line_h = 56
    bubble_h = len(lines) * line_h + 90
    top = 190
    img = Image.new("RGB", (W, top + bubble_h + 160), s["bg"])
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, 150], fill=s["bar"])                     # header bar
    hc = (255, 255, 255) if style == "whatsapp" else (28, 28, 30)
    d.text((PAD, 26), "9:41", font=small, fill=hc)
    d.text((W - PAD - 150, 26), "5G", font=small, fill=hc)
    for i in range(3):                                              # signal bars (drawn: fonts lack the glyph)
        d.rectangle([W - PAD - 80 + i * 22, 52 - i * 9, W - PAD - 66 + i * 22, 60], fill=hc)
    d.text((PAD, 78), sender, font=head, fill=hc)

    d.rounded_rectangle([PAD, top, W - PAD, top + bubble_h], radius=28, fill=s["bubble"])
    y = top + 30
    for ln in lines:
        d.text((PAD + 40, y), ln, font=body, fill=s["ink"])
        y += line_h
    d.text((W - PAD - 40 - d.textlength(time, font=small), top + bubble_h - 48), time, font=small, fill=(130, 130, 135))
    return img


def main() -> None:
    samples = {x["id"]: x for x in json.loads((ROOT / "samples" / "messages.json").read_text("utf-8"))}
    OUT.mkdir(parents=True, exist_ok=True)
    for shot in SHOTS:
        img = render(samples[shot["id"]]["text"], shot["style"], shot["sender"], shot["time"])
        img.save(OUT / shot["file"], optimize=True)
        print(f"wrote samples/screenshots/{shot['file']}  ({img.width}x{img.height})")


if __name__ == "__main__":
    main()
