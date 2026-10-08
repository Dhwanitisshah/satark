"""Render the PWA icons into frontend/icons/ (192, 512, and a full-bleed maskable 512). Needs Pillow.

    python scripts/make_icons.py

The icon is a cream shield with an orange "S" on the app's accent colour. Run it again after changing the design.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "frontend" / "icons"
ACCENT, PAPER = (194, 65, 12), (246, 242, 234)   # --accent and --paper in frontend/index.html
SCALE = 4                                         # draw large, then shrink for smooth edges
FONTS = ["georgiab.ttf", "Georgia Bold.ttf", "DejaVuSerif-Bold.ttf", "arialbd.ttf"]


def load_font(px: int) -> ImageFont.ImageFont:
    for name in FONTS:
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default(px)


def shield(cx: float, cy: float, w: float, h: float) -> list[tuple[float, float]]:
    """Polygon for a shield: flat top, straight sides, curved down to a point at the bottom."""
    top = cy - h / 2
    steps = 24
    curve = [(1 - (i / steps) ** 1.7, top + h * (0.45 + 0.55 * i / steps)) for i in range(steps + 1)]
    # Straight sides down to 45% of the height, then a curve to the tip.
    right = [(cx + w / 2 * k, y) for k, y in curve]
    left = [(cx - w / 2 * k, y) for k, y in reversed(curve)]
    return [(cx - w / 2, top), (cx + w / 2, top), *right, *left]


def render(size: int, maskable: bool) -> Image.Image:
    s = size * SCALE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if maskable:                                   # the OS crops this to a circle or squircle: fill everything
        d.rectangle([0, 0, s, s], fill=ACCENT)
        inner = 0.62                               # keep the mark inside the central safe zone (~80%)
    else:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=s * 0.22, fill=ACCENT)
        inner = 0.70
    w, h = s * inner * 0.82, s * inner
    cx, cy = s / 2, s / 2 + s * 0.01
    d.polygon(shield(cx, cy, w, h), fill=PAPER)
    font = load_font(int(h * 0.62))
    d.text((cx, cy - h * 0.06), "S", font=font, fill=ACCENT, anchor="mm")
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, maskable in [("icon-192.png", 192, False), ("icon-512.png", 512, False),
                                 ("icon-maskable-512.png", 512, True)]:
        render(size, maskable).save(OUT / name, optimize=True)
        print("wrote", OUT / name)


if __name__ == "__main__":
    main()
