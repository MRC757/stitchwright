"""Turn text into stitchable lines.

Two kinds of font:
- installed TrueType/OpenType fonts: letters come out as closed outlines
  (including the holes in letters like 'o'), sewn as run stitches or filled;
- single-line ("stroke") fonts, built in from the Hershey font set: each letter
  is a few open strokes, sewn once (or as satin), which stays clean at small
  sizes where outline letters get crowded.

Text can be bent along an arc, laid out as a three-letter monogram, and SVG
<text> elements are converted with the matching installed font. Glyphs are
placed by their advance widths; kerning and complex-script shaping are not applied.
"""
from __future__ import annotations

import functools
import math
import os
import re
import sys
from dataclasses import dataclass, replace

from fontTools.pens.basePen import BasePen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

from .svg_import import Polyline

FONT_EXTENSIONS = (".ttf", ".otf", ".ttc", ".otc")
HERSHEY_PREFIX = "hershey:"

# Single-line fonts offered in the font list: display name -> Hershey font id
SINGLE_LINE_FONTS = {
    "Single-line Sans": "futural",
    "Single-line Sans Bold": "futuram",
    "Single-line Roman": "rowmans",
    "Single-line Roman Bold": "rowmand",
    "Single-line Times": "timesr",
    "Single-line Times Italic": "timesi",
    "Single-line Script": "scripts",
    "Single-line Script Bold": "scriptc",
    "Single-line Cursive": "cursive",
    "Single-line Gothic": "gothiceng",
}


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
    arc: float = 0.0             # bend: degrees of arc the text spans (+ arch, - smile)
    monogram: bool = False       # three letters, the middle one larger


def is_single_line(spec: TextSpec) -> bool:
    return spec.font_path.startswith(HERSHEY_PREFIX)


# --------------------------------------------------------------------- outline fonts
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


def _outline_font(spec: TextSpec):
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
    return cmap, glyphs, upem, hhea, cap


def cap_ratio(path: str, index: int = 0) -> float:
    """Capital-letter height as a fraction of the em (font size)."""
    if path.startswith(HERSHEY_PREFIX):
        return 0.75
    _, _, upem, _, cap = _outline_font(TextSpec("", path, font_index=index))
    return cap / upem


def _outline_line(spec, font, text, height, step, missing):
    """One line in an outline font: (polylines, width), baseline at y=0, x from 0."""
    cmap, glyphs, upem, hhea, cap = font
    s = height / cap
    space_adv = glyphs[cmap[32]].width if 32 in cmap else upem // 3
    pen = _Flatten(glyphs, step)
    x = 0.0
    chars = list(text.replace("\t", "    "))
    for n, ch in enumerate(chars):
        g = cmap.get(ord(ch))
        if g is None and ch != " ":
            missing.append(ch)
        if g:
            glyphs[g].draw(TransformPen(pen, (s, 0, 0, -s, x, 0)))
        x += (glyphs[g].width if g else space_adv) * s
        if n < len(chars) - 1:
            x += spec.spacing_mm
    return pen.polys, x, (hhea.ascent - hhea.descent + hhea.lineGap) * s


# --------------------------------------------------------------------- single-line fonts
@functools.lru_cache(maxsize=12)
def _hershey(font_id: str):
    from HersheyFonts import HersheyFonts
    f = HersheyFonts()
    f.load_default_font(font_id)
    f.normalize_rendering(100)
    ys = [p[1] for st in f.strokes_for_text("H") for p in st] or [25.0, 100.0]
    return f, min(ys), max(ys) - min(ys)            # font, baseline, cap height (font units)


def _smooth(pts, rounds: int = 2):
    """Chaikin corner cutting: rounds off the Hershey fonts' coarse polygons into
    smooth curves (the stroke's two ends stay put)."""
    for _ in range(rounds):
        if len(pts) < 3:
            return pts
        out = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            out.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            out.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        out[1] = pts[0] if math.dist(out[1], pts[0]) < 1e-9 else out[1]
        out.append(pts[-1])
        pts = out
    return pts


def _hershey_line(spec, text, height, missing):
    font_id = spec.font_path[len(HERSHEY_PREFIX):]
    try:
        f, base, cap = _hershey(font_id)
    except Exception as e:
        raise ValueError(f"Cannot load the single-line font {spec.font_name or font_id}: {e}") from e
    k = height / cap
    polys = []
    x = 0.0
    for ch in text.replace("\t", "    "):
        if ch != " " and not 32 <= ord(ch) < 127:
            missing.append(ch)
            continue
        strokes = list(f.strokes_for_text(ch)) if ch != " " else []
        xs = [p[0] for st in strokes for p in st]
        adv = (max(xs) if xs else 0.0)
        if ch == " ":
            adv = cap * 0.45
        for st in strokes:
            pts = [(x + px * k, -(py - base) * k) for px, py in st]
            if len(pts) >= 2:
                polys.append(Polyline(_smooth(pts), False))
        x += adv * k + max(spec.spacing_mm, 0) + cap * 0.08 * k
    return polys, x, 100 * k


# --------------------------------------------------------------------- layout
def _line(spec, font, text, height, step, missing):
    if is_single_line(spec):
        return _hershey_line(spec, text, height, missing)
    return _outline_line(spec, font, text, height, step, missing)


def _shift(polys, dx, dy):
    for pl in polys:
        pl.points = [(x + dx, y + dy) for x, y in pl.points]
    return polys


def _densify(polys, step=0.5):
    for pl in polys:
        out = [pl.points[0]]
        for a, b in zip(pl.points, pl.points[1:]):
            n = max(1, math.ceil(math.dist(a, b) / step))
            out += [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(1, n + 1)]
        pl.points = out


def _bend(polys, arc_deg):
    """Bend text (baseline at y=0) along a circle spanning arc_deg degrees."""
    xs = [p[0] for pl in polys for p in pl.points]
    if not xs or abs(arc_deg) < 0.5:
        return
    width = max(xs) - min(xs)
    xc = (max(xs) + min(xs)) / 2
    R = width / math.radians(min(abs(arc_deg), 359))
    _densify(polys)
    for pl in polys:
        pts = []
        for x, y in pl.points:
            th, h = (x - xc) / R, -y
            if arc_deg > 0:                                 # arch: centre below the text
                r = R + h
                pts.append((r * math.sin(th), R - r * math.cos(th)))
            else:                                           # smile: centre above
                r = R - h
                pts.append((r * math.sin(th), -R + r * math.cos(th)))
        pl.points = pts


def text_polylines(spec: TextSpec, step_mm: float = 0.3,
                   anchor: str = "centre") -> tuple[list[Polyline], str]:
    """Lines for spec.text, in millimetres. Returns (polylines, characters the font
    has no glyph for). anchor "centre" centres the result on (0, 0); "baseline"
    leaves the first line's baseline on y=0 starting at x=0 (no arc or monogram)."""
    if spec.height_mm <= 0:
        raise ValueError("Letter height must be positive")
    font = None if is_single_line(spec) else _outline_font(spec)
    missing: list[str] = []
    polys: list[Polyline] = []
    letters = "".join(spec.text.split())
    if spec.monogram and len(letters) == 3:
        h = spec.height_mm
        gap = 0.12 * h
        x = 0.0
        for i, ch in enumerate(letters):
            size, base = (1.6 * h, 0.0) if i == 1 else (h, -0.3 * h)   # middle larger, centres level
            p, w, _ = _line(spec, font, ch, size, step_mm, missing)
            polys += _shift(p, x, base)
            x += w + gap
    else:
        lines = spec.text.split("\n")
        y = 0.0
        for line in lines:
            p, w, line_h = _line(spec, font, line, spec.height_mm, step_mm, missing)
            x = {"left": 0.0, "right": -w}.get(spec.align, -w / 2)
            if anchor == "baseline" and len(lines) == 1:
                x = 0.0
            polys += _shift(p, x, y)
            y += line_h * spec.line_spacing
        if spec.arc:
            _bend(polys, spec.arc)
    if polys and anchor != "baseline":
        xs = [p[0] for pl in polys for p in pl.points]
        ys = [p[1] for pl in polys for p in pl.points]
        _shift(polys, -(min(xs) + max(xs)) / 2, -(min(ys) + max(ys)) / 2)
    return polys, "".join(dict.fromkeys(missing))


# --------------------------------------------------------------------- SVG text
def match_font(family: str | None) -> tuple[str, str, int, bool]:
    """(name, path, index, exact) of the installed font best matching a CSS family list."""
    fonts = installed_fonts()
    lower = {n.lower(): n for n in fonts}
    for fam in (family or "").split(","):
        fam = fam.strip().strip("'\"").lower()
        if not fam:
            continue
        if fam in lower:
            n = lower[fam]
            return n, *fonts[n], True
        generic = {"sans-serif": "arial", "serif": "times new roman", "monospace": "courier new",
                   "cursive": "comic sans ms"}.get(fam)
        if generic and generic in lower:
            n = lower[generic]
            return n, *fonts[n], True
        starts = [n for low, n in lower.items() if low.startswith(fam)]
        if starts:
            n = min(starts, key=len)
            return n, *fonts[n], True
    d = default_font()
    if d is None:
        raise ValueError("No fonts are installed")
    return d[0], d[1], d[2], False


def text_from_svg(text: str, x: float, y: float, family: str | None, size: float,
                  anchor: str | None, matrix, px_to_mm: float):
    """Convert one SVG <text>: returns (polylines in mm, TextSpec, warning or None).
    x, y and size are in SVG user units; matrix maps user units to px."""
    name, path, idx, exact = match_font(family)
    a, b, c, d, e, f = (float(v) for v in matrix)
    unit_mm = math.sqrt(abs(a * d - b * c)) * px_to_mm or px_to_mm
    cap_user = size * cap_ratio(path, idx)
    spec = TextSpec(text, path, name, idx, cap_user, "left")
    polys, missing = text_polylines(spec, step_mm=0.3 / unit_mm, anchor="baseline")
    xs = [p[0] for pl in polys for p in pl.points]
    width = (max(xs) if xs else 0.0)
    dx = {"middle": -width / 2, "end": -width}.get(anchor or "start", 0.0)
    out = []
    for pl in polys:
        pts = []
        for px, py in pl.points:
            ux, uy = px + dx + x, py + y
            pts.append(((a * ux + c * uy + e) * px_to_mm, (b * ux + d * uy + f) * px_to_mm))
        out.append(Polyline(pts, pl.closed))
    spec_mm = replace(spec, height_mm=round(cap_user * unit_mm, 2), align="center")
    warn = None if exact else f"Font '{family}' is not installed; used {name} for \"{text}\""
    if missing:
        warn = (warn + "; " if warn else "") + f"not in {name}: {''.join(missing)}"
    return out, spec_mm, warn


# --------------------------------------------------------------------- font list
def _strip_style_suffix(name: str) -> str:
    return re.sub(r"\s*\((TrueType|OpenType|All res)\)\s*$", "", name, flags=re.I).strip()


@functools.lru_cache(maxsize=1)
def installed_fonts() -> dict[str, tuple[str, int]]:
    """Font display name -> (file path, face index): the built-in single-line fonts,
    then the fonts installed on this computer."""
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
    single = {n: (HERSHEY_PREFIX + fid, 0) for n, fid in SINGLE_LINE_FONTS.items()}
    return {**single, **dict(sorted(fonts.items(), key=lambda kv: kv[0].lower()))}


def default_font() -> tuple[str, str, int] | None:
    """(name, path, index) of a sensible default outline font, or None if none are found."""
    fonts = installed_fonts()
    for name in ("Arial", "Segoe UI", "Helvetica", "DejaVu Sans", "Liberation Sans"):
        if name in fonts:
            return (name, *fonts[name])
    for name, (path, idx) in fonts.items():
        if not path.startswith(HERSHEY_PREFIX):
            return (name, path, idx)
    return None
