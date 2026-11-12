"""Draw a preview of the stitches (in 0.1 mm units) with the hoop outline."""
from __future__ import annotations

from PIL import Image, ImageDraw

from pyembroidery import COLOR_CHANGE, END, JUMP, STITCH, TRIM

BACKGROUND = (250, 250, 247)
GRID = (228, 228, 224)
HOOP = (120, 120, 120)
MARGIN = (200, 200, 214)
JUMP_LINE = (200, 140, 140)


def render_image(pattern, hoop_mm=(100.0, 100.0), px_per_mm: float = 8,
                 margin_mm: float | None = None, show_jumps: bool = True) -> Image.Image:
    """Render the pattern centred in the hoop. Returns a PIL image."""
    w, h = max(1, round(hoop_mm[0] * px_per_mm)), max(1, round(hoop_mm[1] * px_per_mm))
    img = Image.new("RGB", (w, h), BACKGROUND)
    d = ImageDraw.Draw(img)
    # light 10 mm grid
    for mm in range(0, int(hoop_mm[0]) + 1, 10):
        d.line([(mm * px_per_mm, 0), (mm * px_per_mm, h)], fill=GRID)
    for mm in range(0, int(hoop_mm[1]) + 1, 10):
        d.line([(0, mm * px_per_mm), (w, mm * px_per_mm)], fill=GRID)
    if margin_mm:
        m = margin_mm * px_per_mm
        d.rectangle([m, m, w - 1 - m, h - 1 - m], outline=MARGIN)
    d.rectangle([0, 0, w - 1, h - 1], outline=HOOP)

    def to_px(x, y):
        return (w / 2 + x / 10.0 * px_per_mm, h / 2 + y / 10.0 * px_per_mm)

    threads = pattern.threadlist or []
    wd = max(1, round(px_per_mm / 3))
    color_idx = 0
    last = None
    for x, y, cmd in pattern.stitches:
        c = cmd & 0xFF
        if c == COLOR_CHANGE:
            color_idx += 1
            continue
        if c == END:
            break
        if c == TRIM:
            continue
        cur = to_px(x, y)
        if last is not None:
            if c == STITCH:
                t = threads[color_idx] if color_idx < len(threads) else None
                rgb = ((t.color >> 16) & 255, (t.color >> 8) & 255, t.color & 255) if t else (0, 0, 0)
                if sum(rgb) / 3 > 190:  # pale thread: add a grey edge so it shows on the page
                    d.line([last, cur], fill=(150, 150, 150), width=wd + 2)
                d.line([last, cur], fill=rgb, width=wd)
            elif c == JUMP and show_jumps:
                d.line([last, cur], fill=JUMP_LINE, width=1)
        last = cur
    return img


def render_preview(pattern, path: str, hoop_mm=(100.0, 100.0), px_per_mm: int = 8) -> None:
    render_image(pattern, hoop_mm, px_per_mm).save(path)
