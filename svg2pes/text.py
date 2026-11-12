"""Turn text into stitchable outlines using an installed TrueType/OpenType font.

Letters come out as closed outlines (including the holes in letters like 'o'),
sewn as run stitches. Glyphs are placed by their advance widths; kerning and
complex-script shaping are not applied.
"""
from __future__ import annotations

import functools
import math
import os
import re
import sys
from dataclasses import dataclass

from fontTools.pens.basePen import BasePen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

from .svg_import import Polyline

FONT_EXTENSIONS = (".ttf", ".otf", ".ttc", ".otc")


@dataclass(frozen=True)
class TextSpec:
    text: str
    font_path: str
    font_name: str = ""
    font_index: int = 0          # face number inside a .ttc collection
    height_mm: float = 10.0      # height of the capital letters
    align: str = "center"        # left | center | right (for several lines)
    spacing_mm: float = 0.0      # extra space between letters
    line_spacing: float = 1.2    # line pitch, as a multiple of the font's line height


class _Flatten(BasePen):
    """Collects pen drawing as polylines, breaking curves into short lines."""

    def __init__(self, glyphset, step_mm: float):
        super().__init__(glyphset)
        self.step = step_mm
        self.polys: list[Polyline] = []
        self.cur: list[tuple[float, float]] | None = None

    def _moveTo(self, p):
        self._finish(closed=False)
        self.cur = [p]

    def _lineTo(self, p):
        self.cur.append(p)

    def _n(self, *pts) -> int:
        length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        return max(2, math.ceil(length / self.step))

    def _curveToOne(self, p1, p2, p3):
        p0 = self.cur[-1]
        n = self._n(p0, p1, p2, p3)
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            self.cur.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                             u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))

    def _qCurveToOne(self, p1, p2):
        p0 = self.cur[-1]
        n = self._n(p0, p1, p2)
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            self.cur.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                             u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))

    def _closePath(self):
        self._finish(closed=True)

    def _endPath(self):
        self._finish(closed=False)

    def _finish(self, closed: bool):
        pts, self.cur = self.cur, None
        if not pts or len(pts) < 2:
            return
        if closed and math.dist(pts[0], pts[-1]) > 1e-9:
            pts.append(pts[0])
        clean = [pts[0]]
        for p in pts[1:]:
            if math.dist(p, clean[-1]) > 1e-6:
                clean.append(p)
        if len(clean) >= 2:
            self.polys.append(Polyline(clean, closed))


@functools.lru_cache(maxsize=16)
def _load(path: str, index: int) -> TTFont:
    return TTFont(path, fontNumber=index, lazy=True)


def text_polylines(spec: TextSpec, step_mm: float = 0.3) -> tuple[list[Polyline], str]:
    """Outlines for spec.text, centred on (0, 0), in millimetres.
    Returns (polylines, characters the font has no glyph for)."""
    if spec.height_mm <= 0:
        raise ValueError("Letter height must be positive")
    try:
        font = _load(spec.font_path, spec.font_index)
        cmap = font.getBestCmap() or {}
        glyphs = font.getGlyphSet()
        upem = font["head"].unitsPerEm
        hhea = font["hhea"]
    except Exception as e:  # fontTools raises many types for broken fonts
        raise ValueError(f"Cannot read font {os.path.basename(spec.font_path)}: {e}") from e
    os2 = font["OS/2"] if "OS/2" in font else None
    cap = getattr(os2, "sCapHeight", 0) or 0.7 * upem
    s = spec.height_mm / cap                       # font units -> mm
    line_h = (hhea.ascent - hhea.descent + hhea.lineGap) * s * spec.line_spacing
    space_adv = glyphs[cmap[32]].width if 32 in cmap else upem // 3

    pen_out = _Flatten(glyphs, step_mm)
    missing = []
    lines = spec.text.split("\n")
    for li, line in enumerate(lines):
        chars = []
        for ch in line.replace("\t", "    "):
            g = cmap.get(ord(ch))
            if g is None and ch != " ":
                missing.append(ch)
            chars.append(g)
        widths = [(glyphs[g].width if g else space_adv) * s for g in chars]
        width = sum(widths) + spec.spacing_mm * max(0, len(chars) - 1)
        x = {"left": 0.0, "right": -width}.get(spec.align, -width / 2)
        y = li * line_h
        for g, w in zip(chars, widths):
            if g:
                glyphs[g].draw(TransformPen(pen_out, (s, 0, 0, -s, x, y)))
            x += w + spec.spacing_mm
    polys = pen_out.polys
    if polys:
        xs = [p[0] for pl in polys for p in pl.points]
        ys = [p[1] for pl in polys for p in pl.points]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        for pl in polys:
            pl.points = [(px - cx, py - cy) for px, py in pl.points]
    return polys, "".join(dict.fromkeys(missing))


def _strip_style_suffix(name: str) -> str:
    return re.sub(r"\s*\((TrueType|OpenType|All res)\)\s*$", "", name, flags=re.I).strip()


@functools.lru_cache(maxsize=1)
def installed_fonts() -> dict[str, tuple[str, int]]:
    """Font display name -> (file path, face index), for fonts installed on this computer."""
    fonts: dict[str, tuple[str, int]] = {}
    if sys.platform == "win32":
        import winreg
        windir = os.environ.get("WINDIR", r"C:\Windows")
        key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(hive, key_path)
            except OSError:
                continue
            with key:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    i += 1
                    if not isinstance(value, str) or not value.lower().endswith(FONT_EXTENSIONS):
                        continue
                    path = value if os.path.isabs(value) else os.path.join(windir, "Fonts", value)
                    if not os.path.exists(path):
                        continue
                    for idx, face in enumerate(_strip_style_suffix(name).split(" & ")):
                        fonts.setdefault(face.strip(), (path, idx))
    else:
        dirs = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
                os.path.expanduser("~/.local/share/fonts"), "/Library/Fonts",
                "/System/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
        for d in dirs:
            for root, _, files in os.walk(d):
                for fn in files:
                    if fn.lower().endswith(FONT_EXTENSIONS):
                        fonts.setdefault(os.path.splitext(fn)[0], (os.path.join(root, fn), 0))
    return dict(sorted(fonts.items(), key=lambda kv: kv[0].lower()))


def default_font() -> tuple[str, str, int] | None:
    """(name, path, index) of a sensible default font, or None if none are found."""
    fonts = installed_fonts()
    for name in ("Arial", "Segoe UI", "Helvetica", "DejaVu Sans", "Liberation Sans"):
        if name in fonts:
            return (name, *fonts[name])
    if fonts:
        name = next(iter(fonts))
        return (name, *fonts[name])
    return None
