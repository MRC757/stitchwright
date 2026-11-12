"""Turn design objects into ordered run-stitch paths (milestone 1).

Every polyline is stitched as a run stitch along its outline. Fill and satin
stitching arrive in later milestones; this module is where they will plug in.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

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


@dataclass
class Run:
    points: list[Point]
    closed: bool
    object_id: str


@dataclass
class ColorBlock:
    color: RGB
    runs: list[Run] = field(default_factory=list)


def _simplify(points: list[Point], tol: float) -> np.ndarray:
    """Ramer-Douglas-Peucker on an open polyline (closed ones repeat their first point)."""
    pts = np.asarray(points, dtype=float)
    if len(pts) < 3 or tol <= 0:
        return pts
    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        seg = pts[b] - pts[a]
        norm = np.hypot(*seg)
        rel = pts[a + 1:b] - pts[a]
        if norm < 1e-12:
            d = np.hypot(rel[:, 0], rel[:, 1])
        else:
            d = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / norm
        i = int(np.argmax(d))
        if d[i] > tol:
            k = a + 1 + i
            keep[k] = True
            stack.append((a, k))
            stack.append((k, b))
    return pts[keep]


def _drop_short(pts: list[Point], min_len: float) -> list[Point]:
    """Merge needle drops closer than min_len, always keeping both ends."""
    if min_len <= 0 or len(pts) < 3:
        return pts
    out = [pts[0]]
    for p in pts[1:-1]:
        if math.dist(p, out[-1]) >= min_len:
            out.append(p)
    last = pts[-1]
    if len(out) > 1 and math.dist(last, out[-1]) < min_len:
        out[-1] = last          # move the final drop onto the true end point
    else:
        out.append(last)
    return out


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
    return _drop_short(out, min_len)


def _path_length(pts: list[Point]) -> float:
    return sum(math.dist(a, b) for a, b in zip(pts[:-1], pts[1:]))


def _make_runs(obj: DesignObject, s: Settings) -> list[Run]:
    runs = []
    for pl in obj.polylines:
        pts = list(pl.points)
        if pl.closed and math.dist(pts[0], pts[-1]) > 1e-9:
            pts.append(pts[0])
        simp = _simplify(pts, s.tolerance)
        if len(simp) < 2 or _path_length([tuple(p) for p in simp]) < s.min_run:
            continue
        runs.append(Run([tuple(p) for p in simp], pl.closed, obj.obj_id))
    return runs


def _orient(run: Run, here: Point) -> tuple[Run, float]:
    """Choose where this run starts, given the needle position. Returns (run, distance)."""
    pts = run.points
    if run.closed:
        ring = pts[:-1]
        k = min(range(len(ring)), key=lambda i: math.dist(ring[i], here))
        rot = ring[k:] + ring[:k]
        rot.append(rot[0])
        return Run(rot, True, run.object_id), math.dist(rot[0], here)
    d0, d1 = math.dist(pts[0], here), math.dist(pts[-1], here)
    if d1 < d0:
        return Run(pts[::-1], False, run.object_id), d1
    return run, d0


def _order_runs(runs: list[Run], start: Point) -> list[Run]:
    remaining = list(runs)
    ordered: list[Run] = []
    here = start
    while remaining:
        best = None
        for i, r in enumerate(remaining):
            oriented, dist = _orient(r, here)
            if best is None or dist < best[0]:
                best = (dist, i, oriented)
        _, i, oriented = best
        remaining.pop(i)
        ordered.append(oriented)
        here = oriented.points[-1]
    return ordered


def plan_blocks(objects: list[DesignObject], s: Settings) -> list[ColorBlock]:
    """Group objects into colour blocks and order their runs to keep jumps short."""
    blocks: list[ColorBlock] = []
    if s.keep_order:
        for obj in objects:
            runs = _make_runs(obj, s)
            if not runs:
                continue
            if blocks and blocks[-1].color == obj.color:
                blocks[-1].runs.extend(runs)
            else:
                blocks.append(ColorBlock(obj.color, list(runs)))
    else:
        by_color: dict[RGB, ColorBlock] = {}
        for obj in objects:
            runs = _make_runs(obj, s)
            if not runs:
                continue
            blk = by_color.setdefault(obj.color, ColorBlock(obj.color))
            blk.runs.extend(runs)
        blocks = list(by_color.values())

    here: Point = (0.0, 0.0)
    for blk in blocks:
        blk.runs = _order_runs(blk.runs, here)
        here = blk.runs[-1].points[-1]
    return blocks


def stitch_points(run: Run, s: Settings) -> list[Point]:
    """Needle drops for one run, including tie-in and tie-off stitches.

    Each tie is `lock_passes` back-and-forth stitches of up to `lock_len`
    along the first / last stitch, so they sit on the line being sewn."""
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
