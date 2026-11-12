"""Read an SVG file into a list of design objects measured in millimetres.

Milestone 1 keeps this simple: every shape becomes one or more polylines,
and each object carries the colour it will be stitched in (stroke colour if
the shape has one, otherwise its fill colour).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from xml.etree import ElementTree

if TYPE_CHECKING:
    from .fill import FillSpec

from svgelements import SVG, Close, Path, Shape

PX_TO_MM = 25.4 / 96.0  # svgelements reports user units as CSS pixels at 96 ppi

Point = tuple[float, float]


@dataclass
class Polyline:
    points: list[Point]
    closed: bool


@dataclass
class DesignObject:
    index: int
    obj_id: str
    kind: str
    source_color: tuple[int, int, int]
    polylines: list[Polyline] = field(default_factory=list)
    color: tuple[int, int, int] = (0, 0, 0)  # colour actually used (operator may change it)
    skipped: bool = False
    fill: "FillSpec | None" = None           # fill the inside of this (closed) shape
    outline: bool = True                     # sew the outline as a run stitch

    def bounds(self) -> tuple[float, float, float, float]:
        xs = [p[0] for pl in self.polylines for p in pl.points]
        ys = [p[1] for pl in self.polylines for p in pl.points]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass
class Design:
    objects: list[DesignObject]
    warnings: list[str]

    def active(self) -> list[DesignObject]:
        return [o for o in self.objects if not o.skipped and o.polylines]

    def bounds(self) -> tuple[float, float, float, float]:
        bs = [o.bounds() for o in self.active()]
        return (min(b[0] for b in bs), min(b[1] for b in bs),
                max(b[2] for b in bs), max(b[3] for b in bs))


def _color_of(value) -> tuple[int, int, int] | None:
    """Return (r, g, b) for a svgelements Color, or None for 'none'/transparent."""
    if value is None or getattr(value, "value", None) is None:
        return None
    alpha = getattr(value, "alpha", 255)
    if alpha is not None and alpha == 0:
        return None
    return int(value.red), int(value.green), int(value.blue)


def _flatten(path: Path, step_mm: float = 0.4) -> list[Polyline]:
    """Turn a (possibly multi-subpath) Path into polylines in millimetres."""
    out: list[Polyline] = []
    for sub in path.as_subpaths():
        sp = Path(sub)
        pts: list[Point] = []
        closed = False
        for seg in sp:
            if isinstance(seg, Close):
                closed = True
            kind = type(seg).__name__
            if kind == "Move":
                # A Move's *start* is the previous pen position; the new subpath
                # begins at its *end*.
                if seg.end is not None:
                    pts = [(seg.end.x * PX_TO_MM, seg.end.y * PX_TO_MM)]
                continue
            if seg.start is None or seg.end is None:
                continue
            if not pts:
                pts.append((seg.start.x * PX_TO_MM, seg.start.y * PX_TO_MM))
            if kind in ("Line", "Close"):
                pts.append((seg.end.x * PX_TO_MM, seg.end.y * PX_TO_MM))
            else:  # curves: sample by length
                length_mm = seg.length(error=1e-3) * PX_TO_MM
                n = max(4, math.ceil(length_mm / step_mm))
                for i in range(1, n + 1):
                    p = seg.point(i / n)
                    pts.append((p.x * PX_TO_MM, p.y * PX_TO_MM))
        if len(pts) >= 2:
            if not closed and math.dist(pts[0], pts[-1]) < 1e-6:
                closed = True
            # Drop consecutive duplicates
            clean = [pts[0]]
            for p in pts[1:]:
                if math.dist(p, clean[-1]) > 1e-6:
                    clean.append(p)
            if len(clean) >= 2:
                out.append(Polyline(clean, closed))
    return out


def load_svg(path: str) -> Design:
    try:
        svg = SVG.parse(path, reify=True, ppi=96)
    except ElementTree.ParseError as e:
        raise ValueError(f"{path} is not a valid SVG file ({e})") from e
    objects: list[DesignObject] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    elements = list(svg.elements())
    # ids written in the file, so generated names ("obj3", "a_2") never clash with them
    all_ids = {e.id for e in elements if getattr(e, "id", None)}

    for el in elements:
        name = type(el).__name__
        if name in ("Text", "Image"):
            warnings.append(
                f"{name} element ignored (only vector shapes are stitched; convert text "
                "to paths, or add it in the window with Add text)")
            continue
        if not isinstance(el, Shape):
            continue

        stroke = _color_of(getattr(el, "stroke", None))
        fill = _color_of(getattr(el, "fill", None))
        color = stroke or fill
        if color is None:
            continue

        idx = len(objects) + 1
        p = el if isinstance(el, Path) else Path(el)
        polys = _flatten(p)
        if not polys:
            continue

        base = el.id or f"obj{idx}"
        obj_id, n = base, 2
        while obj_id in seen_ids or obj_id in all_ids - {el.id}:
            obj_id = f"{base}_{n}"
            n += 1
        seen_ids.add(obj_id)
        obj = DesignObject(idx, obj_id, name, color, polys, color=color)
        objects.append(obj)

    if not objects:
        warnings.append("No stitchable shapes found in the SVG")
    return Design(objects, warnings)
