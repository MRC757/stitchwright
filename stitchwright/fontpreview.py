"""Pictures of text in a given font, for the font list in the text dialog: each font's
name is shown in that font. Outline fonts (TrueType, OpenType) are drawn by Pillow;
the single-line fonts by their own strokes. No user interface here."""
from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from .text import HERSHEY_PREFIX, TextSpec, text_polylines

MAX_WIDTH = 1200            # px: wider samples are cut off (the list is narrower anyway)


def sample_image(path: str, index: int, text: str, height_px: int,
                 colour=(34, 34, 34)) -> Image.Image | None:
    """text drawn in the font, on a transparent background height_px tall, with the
    capitals about half that height. None if the font can't be read or draws nothing."""
    if not text.strip() or height_px < 6:
        return None
    try:
        if path.startswith(HERSHEY_PREFIX):
            img = _single_line(path, text, height_px, colour)
        else:
            img = _outline(path, index, text, height_px, colour)
    except Exception:                       # a damaged or unusual font file: no picture
        return None
    if img is None or img.getbbox() is None:
        return None
    return img


def _outline(path, index, text, height_px, colour):
    """Sized so the lowercase letters are about a third of the row (script fonts have
    small lowercase for their size, and would look tiny), then shrunk if needed so the
    whole name fits the row."""
    symbol = is_symbol_font(path, index)
    if symbol:                               # draw the pictures the letters map to
        text = "".join(chr(0xF000 + ord(c)) if 0x20 <= ord(c) <= 0xFF else c for c in text)

    def load(size):
        size = max(4, round(size))
        if symbol:
            return ImageFont.truetype(path, size, index=index, encoding="symb")
        return ImageFont.truetype(path, size, index=index)

    size = height_px * 0.62
    font = load(size)
    if not symbol:
        _, top, _, bottom = font.getbbox("x", anchor="ls")
        if bottom - top > 0:
            size = min(height_px, max(height_px * 0.5, size * height_px * 0.3 / (bottom - top)))
            font = load(size)
    left, top, right, bottom = font.getbbox(text, anchor="ls")
    if bottom - top > height_px - 2:
        font = load(size * (height_px - 2) / (bottom - top))
        left, top, right, bottom = font.getbbox(text, anchor="ls")
    base = min(max(height_px * 0.72, 1 - top), height_px - 1 - bottom)
    width = min(MAX_WIDTH, int(right - left) + 4)
    img = Image.new("RGBA", (max(width, 1), height_px), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((2 - left, base), text, font=font, fill=colour + (255,), anchor="ls")
    return img


def _single_line(path, text, height_px, colour, oversample=3):
    """Drawn `oversample` times larger and shrunk, so the lines come out smooth."""
    polys, _ = text_polylines(TextSpec(text, path, "", 0, height_mm=10.0), anchor="baseline")
    if not polys:
        return None
    big = height_px * oversample
    k = big * 0.5 / 10.0                     # capitals half the row's height
    xs = [x for pl in polys for x, _ in pl.points]
    x0 = min(xs)
    width = min(MAX_WIDTH, int((max(xs) - x0) * k / oversample) + 6)
    base = big * 0.76
    img = Image.new("RGBA", (max(width, 1) * oversample, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    line = max(1, round(big / 18))
    for pl in polys:
        pts = [(3 * oversample + (x - x0) * k, base + y * k) for x, y in pl.points]
        if len(pts) > 1:
            draw.line(pts, fill=colour + (255,), width=line, joint="curve")
    return img.resize((max(width, 1), height_px), Image.LANCZOS)


@lru_cache(maxsize=None)
def is_symbol_font(path: str, index: int = 0) -> bool:
    """True for fonts whose letters are pictures (Wingdings, Webdings, Symbol, ...):
    their name drawn in themselves is unreadable, so the list also shows it plainly."""
    if path.startswith(HERSHEY_PREFIX):
        return False
    try:
        from fontTools.ttLib import TTFont
        with TTFont(path, fontNumber=index, lazy=True) as f:
            return any(t.platformID == 3 and t.platEncID == 0 for t in f["cmap"].tables)
    except Exception:
        return False
