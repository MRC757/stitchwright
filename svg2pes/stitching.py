"""Turn design objects into ordered stitch paths.

Outlines are sewn as run stitches or satin columns. Objects with a fill also
get fill rows (see fill.py), sewn before the outlines so the outlines sit on
top and cover the edges of the fill. Imported stitches (from a PES file) are
sewn exactly as they are.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

from .fill import closed_rings, fill_paths, satin_path
from .geometry import drop_close, simplify
from .svg_import import DesignObject, Point

RGB = tuple[int, int, int]


@dataclass
class Settings:
    stitch_len: float = 2.5     # mm between needle drops on a run stitch
    tolerance: float = 0.05     # mm, outline simplification tolerance
    corner_deg: float = 35.0    # turns sharper than this always get a needle drop
    trim_dist: float = 8.0      # mm, jumps longer than this are trimmed
    min_stitch: float = 0.4     # mm, shorter needle drops are merged (avoids thread breaks)
    lock_len: float = 0.5       # mm, tie-in / tie-off stitch length (0 = off)
    lock_passes: int = 2        # back-and-forth passes per tie-in / tie-off
    min_run: float = 0.6        # mm, outlines shorter than this are dropped
    keep_order: bool = False    # True = keep SVG paint order instead of grouping by colour
    knockout: bool = True       # leave out fill under filled shapes stacked above
    machine_spm: int = 650      # machine speed for the sewing-time estimate
    color_order: tuple = ()     # preferred block order: ((phase, rgb), ...), phase 0 = fill


@dataclass
class Run:
    points: list[Point]
    closed: bool
    object_id: str
    kind: str = "run"       # run | fill | satin | stitches (all but run are final needle points)

    @property
    def fill(self) -> bool:
        return self.kind == "fill"

    @property
    def prestitched(self) -> bool:
        return self.kind != "run"

    @property
    def label(self) -> str:
        return self.object_id if self.kind == "run" else f"{self.object_id} {self.kind}"


@dataclass
class ColorBlock:
    color: RGB
    runs: list[Run] = field(default_factory=list)


def resample(points: np.ndarray, spacing: float, corner_deg: float,
             min_len: float = 0.0) -> list[Point]:
    """Place needle drops at roughly even spacing, keeping sharp corners, and
    never closer together than min_len."""
    n = len(points)
    anchors = [0]
    for i in range(1, n - 1):
        v1 = points[i] - points[i - 1]
        v2 = points[i + 1] - points[i]
        l1, l2 = np.hypot(*v1), np.hypot(*v2)
        if l1 < 1e-9 or l2 < 1e-9:
            continue
        cosang = float(np.dot(v1, v2) / (l1 * l2))
        turn = math.degrees(math.acos(max(-1.0, min(1.0, cosang))))
        if turn > corner_deg:
            anchors.append(i)
    anchors.append(n - 1)

    out: list[Point] = [tuple(points[0])]
    for a, b in zip(anchors[:-1], anchors[1:]):
        seg = points[a:b + 1]
        d = np.hypot(*np.diff(seg, axis=0).T)
        cum = np.concatenate([[0.0], np.cumsum(d)])
        total = cum[-1]
        if total < 1e-9:
            continue
        k = max(1, math.ceil(total / spacing))
        targets = np.linspace(total / k, total, k)
        xs = np.interp(targets, cum, seg[:, 0])
        ys = np.interp(targets, cum, seg[:, 1])
        out.extend(zip(xs.tolist(), ys.tolist()))
    return drop_close(out, min_len)


def _path_length(pts: list[Point]) -> float:
    return sum(math.dist(a, b) for a, b in zip(pts[:-1], pts[1:]))


def _make_runs(obj: DesignObject, s: Settings) -> list[Run]:
    runs = []
    for pl in obj.polylines:
        pts = list(pl.points)
        if pl.closed and math.dist(pts[0], pts[-1]) > 1e-9:
            pts.append(pts[0])
        simp = simplify(pts, s.tolerance)
        if len(simp) < 2 or _path_length(simp) < s.min_run:
            continue
        runs.append(Run(simp, pl.closed, obj.obj_id))
    return runs


def _orient(run: Run, here: Point) -> tuple[Run, float]:
    """Choose where this run starts, given the needle position. Returns (run, distance)."""
    pts = run.points
    if run.closed:
        ring = pts[:-1]
        k = min(range(len(ring)), key=lambda i: math.dist(ring[i], here))
        rot = ring[k:] + ring[:k]
        rot.append(rot[0])
        return replace(run, points=rot), math.dist(rot[0], here)
    d0, d1 = math.dist(pts[0], here), math.dist(pts[-1], here)
    if d1 < d0:
        return replace(run, points=pts[::-1]), d1
    return run, d0


def _bbox(rings):
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _fill_runs(obj: DesignObject, s: Settings, cutouts=None) -> list[Run]:
    paths = fill_paths(obj.polylines, obj.fill, s.min_stitch, cutouts)
    return [Run(p, False, obj.obj_id, kind="fill") for p in paths if len(p) >= 2]


def _satin_runs(obj: DesignObject, s: Settings) -> list[Run]:
    runs = []
    for pl in obj.polylines:
        pts = satin_path(pl.points, pl.closed, obj.satin, s.min_stitch)
        if len(pts) >= 2:
            runs.append(Run(pts, False, obj.obj_id, kind="satin"))
    return runs


def _stitch_runs(obj: DesignObject) -> list[Run]:
    return [Run(list(pl.points), False, obj.obj_id, kind="stitches")
            for pl in obj.polylines if len(pl.points) >= 2]


def _order_units(units: list[list[Run]], start: Point) -> list[Run]:
    """Order units by nearest start. A unit's runs keep their order (a fill's
    underlay comes first); a unit that is one plain outline may start anywhere."""
    remaining = list(units)
    out: list[Run] = []
    here = start
    while remaining:
        best = None
        for i, unit in enumerate(remaining):
            if len(unit) == 1 and not unit[0].prestitched:
                oriented, d = _orient(unit[0], here)
                cand = [oriented]
            else:
                cand, d = unit, math.dist(unit[0].points[0], here)
            if best is None or d < best[0]:
                best = (d, i, cand)
        _, i, cand = best
        remaining.pop(i)
        out.extend(cand)
        here = cand[-1].points[-1]
    return out


def plan_blocks(objects: list[DesignObject], s: Settings) -> list[ColorBlock]:
    """Group the runs into colour blocks, fills before outlines, ordered to keep jumps short.

    Each object may contribute fill runs (in obj.fill.color), outline runs (in
    obj.color: run stitch or satin) or imported stitches. Normally all fills come
    first, grouped by colour, then all outlines; with keep_order each object's fill
    and outline follow the SVG paint order. s.color_order can reorder the groups."""
    filled = []                     # rings of filled shapes, for cutting the fills below them
    for i, o in enumerate(objects):
        if o.fill is not None:
            rings = closed_rings(o.polylines)
            if rings:
                filled.append((i, rings, _bbox(rings)))

    parts: list[tuple] = []         # (colour, units, 0 = fill / 1 = outline)
    for idx, obj in enumerate(objects):
        if obj.stitches:
            runs = _stitch_runs(obj)
            if runs:
                parts.append((obj.color, [runs], 1))
            continue
        if obj.fill is not None:
            cut = None
            if s.knockout:
                mine = closed_rings(obj.polylines)
                box = _bbox(mine) if mine else None
                cut = [rings for j, rings, b in filled if j > idx and box and _overlap(box, b)]
            runs = _fill_runs(obj, s, cut)
            if runs:
                parts.append((obj.fill.color or obj.color, [runs], 0))
        if obj.outline:
            if obj.satin is not None:
                units = [[r] for r in _satin_runs(obj, s)]
            else:
                units = [[r] for r in _make_runs(obj, s)]
            if units:
                parts.append((obj.color, units, 1))

    if not s.keep_order:
        grouped: list[list] = []
        for phase in (0, 1):
            by_color: dict = {}
            for color, units, ph in parts:
                if ph == phase:
                    if color not in by_color:
                        by_color[color] = [color, [], phase]
                        grouped.append(by_color[color])
                    by_color[color][1].extend(units)
        fills = [g for g in grouped if g[2] == 0]
        outlines = [g for g in grouped if g[2] == 1]
        order = {(ph, tuple(c)): n for n, (ph, c) in enumerate(s.color_order)}
        if order:
            big = len(order)
            fills.sort(key=lambda g: order.get((0, tuple(g[0])), big))
            outlines.sort(key=lambda g: order.get((1, tuple(g[0])), big))
        elif fills:
            # let the outlines in the last fill colour follow straight on (no colour change)
            match = [g for g in outlines if g[0] == fills[-1][0]]
            if match:
                outlines.remove(match[0])
                outlines.insert(0, match[0])
        parts = [tuple(g) for g in fills + outlines]

    blocks: list[ColorBlock] = []
    here: Point = (0.0, 0.0)
    for color, units, _ in parts:
        ordered = _order_units(units, here)      # fills stay before outlines in a shared block
        if not ordered:
            continue
        here = ordered[-1].points[-1]
        if blocks and blocks[-1].color == color:
            blocks[-1].runs.extend(ordered)
        else:
            blocks.append(ColorBlock(color, ordered))
    return blocks


def stitch_points(run: Run, s: Settings) -> list[Point]:
    """Needle drops for one run, including tie-in and tie-off stitches.

    Each tie is `lock_passes` back-and-forth stitches of up to `lock_len`
    along the first / last stitch, so they sit on the line being sewn."""
    if run.kind == "stitches":                  # imported: sew exactly as given
        return list(run.points)
    if run.prestitched:
        pts = list(run.points)
    else:
        pts = resample(np.asarray(run.points), s.stitch_len, s.corner_deg, s.min_stitch)
    if s.lock_len <= 0 or s.lock_passes <= 0 or len(pts) < 2:
        return pts

    def toward(a: Point, b: Point) -> Point:
        d = math.dist(a, b)
        if d < 1e-9:
            return a
        t = min(s.lock_len, d) / d
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    p0, pn = pts[0], pts[-1]
    a = toward(p0, pts[1])
    b = toward(pn, pts[-2])
    tie_in = [p0, a] * s.lock_passes            # p0 a p0 a ... then on to pts[1]
    tie_out = [b, pn] * s.lock_passes           # ... pn b pn b pn
    return tie_in + pts[1:] + tie_out
