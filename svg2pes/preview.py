"""Draw stitch previews: the whole hoop (for PNG files) or any zoomed view of it
(for the window), optionally sewn only up to a point for the sew-out simulation.

Coordinates: the pattern is in 0.1 mm units with the hoop centre at (0, 0);
drawing works in millimetres, and a View maps millimetres to image pixels.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from pyembroidery import COLOR_CHANGE, END, JUMP, STITCH, TRIM

BACKGROUND = (250, 250, 247)    # fabric inside the hoop
OUTSIDE = (233, 233, 229)       # beyond the hoop
GRID = (226, 226, 222)
GRID_MINOR = (241, 241, 237)
HOOP = (120, 120, 120)
MARGIN = (200, 200, 214)
JUMP_LINE = (200, 140, 140)
GHOST = (214, 214, 210)         # not yet sewn, in the sew-out simulation
NEEDLE_POINT = (55, 55, 55)
THREAD_MM = 0.4                 # drawn thread thickness (40 wt embroidery thread)

# kinds of track entries
SEW, JUMPED, TRIMMED, CHANGE = 1, 2, 3, 4


class Track:
    """A pattern flattened for drawing: one entry per machine command, in
    sewing order, each with where the needle came from and went to (mm)."""

    def __init__(self, pattern=None):
        threads = (pattern.threadlist if pattern is not None else None) or []
        self.rgbs = [((t.color >> 16) & 255, (t.color >> 8) & 255, t.color & 255)
                     for t in threads] or [(0, 0, 0)]
        self.kind: list[int] = []
        self.fx: list[float] = []
        self.fy: list[float] = []
        self.x: list[float] = []
        self.y: list[float] = []
        self.colour: list[int] = []      # thread index in use at this entry
        self.stitch_no: list[int] = []   # stitches sewn up to and including this entry
        ci, n, last = 0, 0, None
        for x, y, cmd in (pattern.stitches if pattern is not None else []):
            c = cmd & 0xFF
            if c == END:
                break
            if c == STITCH:
                kind = SEW
                n += 1
            elif c == JUMP:
                kind = JUMPED
            elif c == TRIM:
                kind = TRIMMED
            elif c == COLOR_CHANGE:
                kind = CHANGE
                ci += 1
            else:
                continue
            x, y = x / 10.0, y / 10.0
            fx, fy = last if last is not None else (x, y)
            self.kind.append(kind)
            self.fx.append(fx)
            self.fy.append(fy)
            self.x.append(x)
            self.y.append(y)
            self.colour.append(min(ci, len(self.rgbs) - 1))
            self.stitch_no.append(n)
            if kind in (SEW, JUMPED):
                last = (x, y)
        self.total_stitches = n

    def __len__(self) -> int:
        return len(self.kind)


@dataclass
class View:
    """Image pixel (0, 0) shows (x0, y0) mm; k image pixels per mm.
    ss is the supersampling factor (k is already multiplied by it)."""
    x0: float
    y0: float
    k: float
    w: int
    h: int
    ss: int = 1

    def px(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.x0) * self.k, (y - self.y0) * self.k


@dataclass
class Style:
    show_jumps: bool = True
    needle_points: bool = True    # dots at needle points, when zoomed in enough
    thread_look: bool = True      # shaded thread with rounded ends, when zoomed in enough
    ghost: bool = True            # show the unsewn rest of the design faintly


def _shade(rgb, f):
    return tuple(int(c * f) for c in rgb)


def _tint(rgb, f):
    return tuple(int(c + (255 - c) * f) for c in rgb)


def render_base(view: View, hoop_mm, margin_mm: float | None = None) -> Image.Image:
    """Fabric, grid, hoop and margin outlines for a view."""
    img = Image.new("RGB", (view.w, view.h), OUTSIDE)
    d = ImageDraw.Draw(img)
    hw, hh = hoop_mm[0] / 2, hoop_mm[1] / 2
    ax, ay = view.px(-hw, -hh)
    bx, by = view.px(hw, hh)
    d.rectangle([ax, ay, bx, by], fill=BACKGROUND)
    real_k = view.k / view.ss
    steps = [(10, GRID)]
    if real_k >= 18:
        steps.insert(0, (1, GRID_MINOR))
    for step, colour in steps:
        n0 = int(-hw // step)
        for i in range(n0, int(hw // step) + 1):
            x = view.px(i * step, 0)[0]
            if 0 <= x <= view.w and (step == 10 or i % 10):
                d.line([(x, max(ay, 0)), (x, min(by, view.h))], fill=colour)
        n0 = int(-hh // step)
        for i in range(n0, int(hh // step) + 1):
            y = view.px(0, i * step)[1]
            if 0 <= y <= view.h and (step == 10 or i % 10):
                d.line([(max(ax, 0), y), (min(bx, view.w), y)], fill=colour)
    if margin_mm:
        mx, my = view.px(-hw + margin_mm, -hh + margin_mm)
        nx, ny = view.px(hw - margin_mm, hh - margin_mm)
        d.rectangle([mx, my, nx, ny], outline=MARGIN, width=max(1, view.ss))
    d.rectangle([ax, ay, bx - 1, by - 1], outline=HOOP, width=max(1, view.ss))
    return img


def draw_track(img: Image.Image, track: Track, view: View, start: int, end: int,
               style: Style, ghost: bool = False) -> None:
    """Draw track entries [start, end) onto img."""
    d = ImageDraw.Draw(img)
    k, ox, oy = view.k, view.x0, view.y0
    real_k = k / view.ss
    w = max(1, round(THREAD_MM * k))
    fancy = style.thread_look and real_k >= 7 and not ghost
    dots = style.needle_points and real_k >= 12 and not ghost
    r_dot = max(1.0, 0.09 * k)
    W, H = view.w, view.h
    pad = w + 4
    kinds, fx, fy, xs, ys, cols, rgbs = (track.kind, track.fx, track.fy, track.x, track.y,
                                         track.colour, track.rgbs)
    points = []          # needle points, drawn last so later stitches don't cover them
    for i in range(max(0, start), min(end, len(kinds))):
        kind = kinds[i]
        if kind != SEW and kind != JUMPED:
            continue
        xa, ya = (fx[i] - ox) * k, (fy[i] - oy) * k
        xb, yb = (xs[i] - ox) * k, (ys[i] - oy) * k
        if (xa < -pad and xb < -pad) or (xa > W + pad and xb > W + pad) or \
           (ya < -pad and yb < -pad) or (ya > H + pad and yb > H + pad):
            continue
        if kind == JUMPED:
            if style.show_jumps and not ghost:
                d.line([(xa, ya), (xb, yb)], fill=JUMP_LINE, width=max(1, view.ss))
            continue
        if ghost:
            d.line([(xa, ya), (xb, yb)], fill=GHOST, width=max(1, w // 2))
            continue
        rgb = rgbs[cols[i]]
        if fancy:
            edge = _shade(rgb, 0.55) if sum(rgb) < 600 else (150, 150, 150)
            re, rf = w / 2 + view.ss, w / 2
            d.line([(xa, ya), (xb, yb)], fill=edge, width=w + 2 * view.ss)
            for (px, py) in ((xa, ya), (xb, yb)):
                d.ellipse([px - re, py - re, px + re, py + re], fill=edge)
            d.line([(xa, ya), (xb, yb)], fill=rgb, width=w)
            for (px, py) in ((xa, ya), (xb, yb)):
                d.ellipse([px - rf, py - rf, px + rf, py + rf], fill=rgb)
            if w >= 4:
                d.line([(xa, ya), (xb, yb)], fill=_tint(rgb, 0.45), width=max(1, w // 4))
        else:
            if sum(rgb) / 3 > 190:  # pale thread: add a grey edge so it shows on the page
                d.line([(xa, ya), (xb, yb)], fill=(150, 150, 150), width=w + 2)
            d.line([(xa, ya), (xb, yb)], fill=rgb, width=w)
        if dots:
            points.append((xa, ya))
            points.append((xb, yb))
    for px, py in points:
        d.ellipse([px - r_dot, py - r_dot, px + r_dot, py + r_dot], fill=NEEDLE_POINT)


def render_view(track: Track, view: View, hoop_mm, margin_mm: float | None = None,
                style: Style | None = None, upto: int | None = None) -> Image.Image:
    """Render a view of the stitches. With upto, only entries before it are sewn
    (the rest are ghosted if style.ghost)."""
    style = style or Style()
    img = render_base(view, hoop_mm, margin_mm)
    n = len(track)
    upto = n if upto is None else max(0, min(upto, n))
    if style.ghost and upto < n:
        draw_track(img, track, view, upto, n, style, ghost=True)
    draw_track(img, track, view, 0, upto, style)
    return img


def render_image(pattern, hoop_mm=(100.0, 100.0), px_per_mm: float = 8,
                 margin_mm: float | None = None, show_jumps: bool = True) -> Image.Image:
    """The whole hoop, centred. Returns a PIL image."""
    hw, hh = hoop_mm
    view = View(-hw / 2, -hh / 2, px_per_mm, max(1, round(hw * px_per_mm)),
                max(1, round(hh * px_per_mm)))
    return render_view(Track(pattern), view, hoop_mm, margin_mm,
                       Style(show_jumps=show_jumps, thread_look=False, needle_points=False))


def render_preview(pattern, path: str, hoop_mm=(100.0, 100.0), px_per_mm: int = 8) -> None:
    render_image(pattern, hoop_mm, px_per_mm).save(path)
