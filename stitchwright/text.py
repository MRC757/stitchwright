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
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

from .geometry import chaikin, subdivide
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
    return cmap, glyphs, upem, hhea, _cap_height(font, cmap, glyphs, upem)


def _cap_height(font, cmap, glyphs, upem) -> float:
    """Height of the capitals in font units: the top of the 'H' as drawn. The OS/2 table's
    sCapHeight is only the fallback, as some fonts get it wrong (Liberation Sans says
    1409 units; its H is 1466 tall, like Arial's)."""
    g = cmap.get(ord("H"))
    if g:
        try:
            pen = BoundsPen(glyphs)
            glyphs[g].draw(pen)
            if pen.bounds and pen.bounds[3] > 0:
                return pen.bounds[3]
        except Exception:                     # an unusual glyph: use the table instead
            pass
    os2 = font["OS/2"] if "OS/2" in font else None
    return getattr(os2, "sCapHeight", 0) or 0.7 * upem


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
                polys.append(Polyline(chaikin(pts, 2), False))
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
            out += subdivide(a, b, step)
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
GENERIC_FAMILIES = {
    "sans-serif": ("Arial", "DejaVu Sans"),
    "serif": ("Times New Roman", "DejaVu Serif"),
    "monospace": ("Courier New", "DejaVu Sans Mono"),
    "cursive": ("Comic Sans MS",),
}


def match_font(family: str | None) -> tuple[str, str, int, str]:
    """(name, path, index, how) of the installed font best matching a CSS family list.
    how is "exact", "similar" (a font with the same letter widths, e.g. Liberation Sans
    for Arial) or "default" (nothing matched: the default font)."""
    fonts = installed_fonts()
    lower = {n.lower(): n for n in fonts}
    similar = None
    for fam in (family or "").split(","):
        fam = fam.strip().strip("'\"")
        if not fam:
            continue
        if fam.lower() in GENERIC_FAMILIES:
            for name in GENERIC_FAMILIES[fam.lower()]:
                found = find_font(name)
                if found:
                    return found[0], found[1], found[2], "exact"
            continue
        found = find_font(fam)
        if found and found[3]:
            return found[0], found[1], found[2], "exact"
        starts = [n for low, n in lower.items() if low.startswith(fam.lower())]
        if starts:
            n = min(starts, key=len)
            return n, *fonts[n], "exact"
        if found and similar is None:          # keep looking: a later family may be installed
            similar = found
    if similar:
        return similar[0], similar[1], similar[2], "similar"
    d = default_font()
    if d is None:
        raise ValueError("No fonts are installed")
    return d[0], d[1], d[2], "default"


def text_from_svg(text: str, x: float, y: float, family: str | None, size: float,
                  anchor: str | None, matrix, px_to_mm: float):
    """Convert one SVG <text>: returns (polylines in mm, TextSpec, warning or None).
    x, y and size are in SVG user units; matrix maps user units to px."""
    name, path, idx, how = match_font(family)
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
    warn = {"exact": None,
            "similar": f"Font '{family}' is not installed; used the close match {name} "
                       f"for \"{text}\"",
            "default": f"Font '{family}' is not installed; used {name} for \"{text}\""}[how]
    if missing:
        warn = (warn + "; " if warn else "") + f"not in {name}: {''.join(missing)}"
    return out, spec_mm, warn


# --------------------------------------------------------------------- font list
def _strip_style_suffix(name: str) -> str:
    return re.sub(r"\s*\((TrueType|OpenType|All res)\)\s*$", "", name, flags=re.I).strip()


def _clean_face_name(name: str) -> str:
    """'Lato Regular' -> 'Lato', as Windows names its fonts ('Arial', 'Arial Bold')."""
    name = " ".join(name.split())
    if name.lower().endswith(" regular"):
        name = name[:-len(" regular")]
    return name


def font_faces(path: str) -> list[tuple[str, int]]:
    """(display name, face index) of each font in a font file, read from its name table:
    'DejaVu Sans Bold', not the file name 'DejaVuSans-Bold'. A collection (.ttc, .otc)
    gives one entry per face. Empty if the file can't be read as a font."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
        count = int.from_bytes(head[8:12], "big") if head[:4] == b"ttcf" else 1
        out = []
        for i in range(min(count, 64)):
            with TTFont(path, fontNumber=i, lazy=True) as font:
                names = font["name"]
                full = names.getDebugName(4)
                if not full:
                    family = names.getDebugName(16) or names.getDebugName(1) or ""
                    style = names.getDebugName(17) or names.getDebugName(2) or ""
                    full = f"{family} {style}"
            full = _clean_face_name(full or "")
            if full and not full.startswith("."):       # '.LastResort' etc. are hidden fonts
                out.append((full, i))
        return out
    except Exception:                                    # damaged or not a font
        return []


def _parse_fc_list(output: str) -> dict[str, tuple[str, int]]:
    """Fonts from `fc-list` output in the format _FC_FORMAT asks for."""
    fonts: dict[str, tuple[str, int]] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        path, index, full, family, style = parts
        if not path.lower().endswith(FONT_EXTENSIONS):  # Type 1, bitmap fonts: no outlines we read
            continue
        try:
            idx = int(index or 0)
        except ValueError:
            continue
        if idx >= 0x10000:                   # a named instance of a variable font: list it once
            continue
        name = _clean_face_name(full or f"{family} {style}")
        if name and not name.startswith("."):
            fonts.setdefault(name, (path, idx))
    return fonts


_FC_FORMAT = "%{file}\t%{index}\t%{fullname[0]}\t%{family[0]}\t%{style[0]}\n"


def _fontconfig_fonts() -> dict[str, tuple[str, int]]:
    """The fonts fontconfig knows (Linux desktops), or {} if fc-list isn't available."""
    import subprocess
    try:
        out = subprocess.run(["fc-list", "--format", _FC_FORMAT], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return {}
    return _parse_fc_list(out.stdout) if out.returncode == 0 else {}


FONT_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", "~/.fonts", "~/.local/share/fonts",
             "/Library/Fonts", "/System/Library/Fonts", "~/Library/Fonts"]


def _scan_font_dirs(dirs) -> dict[str, tuple[str, int]]:
    """Fonts found by reading every font file under dirs (when fc-list isn't available)."""
    fonts: dict[str, tuple[str, int]] = {}
    for d in dirs:
        for root, _, files in os.walk(os.path.expanduser(d)):
            for fn in sorted(files):
                if fn.lower().endswith(FONT_EXTENSIONS):
                    path = os.path.join(root, fn)
                    for name, idx in font_faces(path):
                        fonts.setdefault(name, (path, idx))
    return fonts


def _windows_fonts() -> dict[str, tuple[str, int]]:
    import winreg
    fonts: dict[str, tuple[str, int]] = {}
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
    return fonts


@functools.lru_cache(maxsize=1)
def installed_fonts() -> dict[str, tuple[str, int]]:
    """Font display name -> (file path, face index): the built-in single-line fonts,
    then the fonts installed on this computer, by their real names ('DejaVu Sans Bold')."""
    if sys.platform == "win32":
        fonts = _windows_fonts()
    else:
        fonts = _fontconfig_fonts() or _scan_font_dirs(FONT_DIRS)
    single = {n: (HERSHEY_PREFIX + fid, 0) for n, fid in SINGLE_LINE_FONTS.items()}
    return {**single, **dict(sorted(fonts.items(), key=lambda kv: kv[0].lower()))}


# Fonts with the same letter widths, so text set in one keeps its size in another:
# Windows fonts and the free fonts Linux has in their place.
METRIC_EQUIVALENTS = [
    ("Arial", "Liberation Sans", "Arimo", "Helvetica"),
    ("Times New Roman", "Liberation Serif", "Tinos", "Times"),
    ("Courier New", "Liberation Mono", "Cousine", "Courier"),
    ("Calibri", "Carlito"),
    ("Cambria", "Caladea"),
    ("Arial Narrow", "Liberation Sans Narrow"),
]
# Close in look, not in widths: tried after the equivalents
LOOKALIKES = {
    "segoe ui": ("Noto Sans", "Ubuntu", "DejaVu Sans"),
    "verdana": ("DejaVu Sans",),
    "tahoma": ("DejaVu Sans",),
    "georgia": ("Gelasio", "DejaVu Serif"),
    "comic sans ms": ("Comic Neue",),
    "consolas": ("DejaVu Sans Mono", "Ubuntu Mono"),
}
_STYLE_SUFFIX = re.compile(
    r"\s+((?:(?:bold|italic|oblique|light|medium|semibold|black)\s*)+)$", re.I)


def font_alternatives(name: str) -> list[str]:
    """Other names to try when the font `name` isn't installed, best first:
    'Arial Bold' -> ['Liberation Sans Bold', 'Arimo Bold', 'Helvetica Bold']."""
    m = _STYLE_SUFFIX.search(name)
    base, style = (name[:m.start()], " " + m.group(1).strip()) if m else (name, "")
    low = base.strip().lower()
    out: list[str] = []
    for group in METRIC_EQUIVALENTS:
        if low in (g.lower() for g in group):
            out += [g + style for g in group if g.lower() != low]
    out += [g + style for g in LOOKALIKES.get(low, ())]
    return out


def find_font(name: str) -> tuple[str, str, int, bool] | None:
    """(installed name, path, index, exact) for the font called `name`: that font, the
    same name in other capitals, or a font with the same letter widths or look
    (exact=False). None if none of these is installed."""
    fonts = installed_fonts()
    if name in fonts:
        return name, *fonts[name], True
    lower = {n.lower(): n for n in fonts}
    if name.lower() in lower:
        n = lower[name.lower()]
        return n, *fonts[n], True
    for alt in font_alternatives(name):
        n = lower.get(alt.lower())
        if n:
            return n, *fonts[n], False
    return None


DEFAULT_FONTS = ("Arial", "Liberation Sans", "Arimo", "Helvetica", "Segoe UI", "Noto Sans",
                 "DejaVu Sans", "Ubuntu")


def default_font() -> tuple[str, str, int] | None:
    """(name, path, index) of a sensible default outline font, or None if none are found."""
    fonts = installed_fonts()
    for name in DEFAULT_FONTS:
        if name in fonts:
            return (name, *fonts[name])
    outline = [(n, p, i) for n, (p, i) in fonts.items() if not p.startswith(HERSHEY_PREFIX)]
    # plain text fonts first: not maths, symbol or emoji fonts
    plain = [f for f in outline if not re.search(r"math|symbol|emoji|dings", f[0], re.I)]
    return (plain or outline or [None])[0]
