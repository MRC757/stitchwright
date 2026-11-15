"""2-D affine transforms for placing objects in the hoop (millimetres, y down).

A transform is a tuple (a, b, c, d, e, f), the same layout as an SVG matrix:
    x' = a*x + c*y + e
    y' = b*x + d*y + f
"""
from __future__ import annotations

import math

import numpy as np

Affine = tuple[float, float, float, float, float, float]
Point = tuple[float, float]

IDENTITY: Affine = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def translate(dx: float, dy: float) -> Affine:
    return (1.0, 0.0, 0.0, 1.0, dx, dy)


def scale(sx: float, sy: float | None = None, cx: float = 0.0, cy: float = 0.0) -> Affine:
    """Scale about the point (cx, cy)."""
    sy = sx if sy is None else sy
    return (sx, 0.0, 0.0, sy, cx - sx * cx, cy - sy * cy)


def rotate(degrees: float, cx: float = 0.0, cy: float = 0.0) -> Affine:
    """Rotate about (cx, cy). Positive angles turn clockwise on screen (y points down)."""
    r = math.radians(degrees)
    co, si = math.cos(r), math.sin(r)
    return (co, si, -si, co, cx - co * cx + si * cy, cy - si * cx - co * cy)


def chain(*ms: Affine) -> Affine:
    """One transform that applies each of `ms` in turn, first to last."""
    a, b, c, d, e, f = IDENTITY
    for (a2, b2, c2, d2, e2, f2) in ms:
        a, b, c, d, e, f = (a2 * a + c2 * b, b2 * a + d2 * b,
                            a2 * c + c2 * d, b2 * c + d2 * d,
                            a2 * e + c2 * f + e2, b2 * e + d2 * f + f2)
    return (a, b, c, d, e, f)


def apply(m: Affine, pts) -> list[Point]:
    a, b, c, d, e, f = m
    return [(a * x + c * y + e, b * x + d * y + f) for x, y in pts]


def bounds(pts) -> tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


# --------------------------------------------------------------------- polylines
def simplify(pts, tol: float) -> list[Point]:
    """Ramer-Douglas-Peucker: drop points within tol of the line through their
    neighbours. Iterative, so long outlines can't hit the recursion limit. A closed
    polyline (first point repeated at the end) keeps both copies of that point."""
    pts = [(float(x), float(y)) for x, y in pts]
    if len(pts) < 3 or tol <= 0:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i0, i1 = stack.pop()
        a, b = pts[i0], pts[i1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy)
        best, idx = -1.0, 0
        for i in range(i0 + 1, i1):
            p = pts[i]
            d = (abs(dx * (p[1] - a[1]) - dy * (p[0] - a[0])) / n) if n > 1e-12 else math.dist(p, a)
            if d > best:
                best, idx = d, i
        if best > tol:
            keep[idx] = True
            stack += [(i0, idx), (idx, i1)]
    return [p for p, k in zip(pts, keep) if k]


def chaikin(pts, rounds: int = 2, closed: bool = False) -> list[Point]:
    """Chaikin corner cutting: rounds off corners. An open line keeps its two end
    points; a closed one (first point repeated at the end) stays closed."""
    pts = list(pts)
    for _ in range(rounds):
        if len(pts) < 3:
            return pts
        if closed:
            ring = pts[:-1]
            pairs = zip(ring, ring[1:] + ring[:1])
        else:
            pairs = zip(pts, pts[1:])
        out = [] if closed else [pts[0]]
        for a, b in pairs:
            out.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            out.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        pts = out + [out[0]] if closed else out + [pts[-1]]
    return pts


def drop_close(pts, min_len: float) -> list[Point]:
    """Merge points closer than min_len to the previous kept one, always keeping
    both ends (the last point replaces a too-close predecessor)."""
    pts = list(pts)
    if len(pts) < 3 or min_len <= 0:
        return pts
    out = [pts[0]]
    for p in pts[1:-1]:
        if math.dist(p, out[-1]) >= min_len:
            out.append(p)
    if len(out) > 1 and math.dist(pts[-1], out[-1]) < min_len:
        out[-1] = pts[-1]
    else:
        out.append(pts[-1])
    return out


def subdivide(a: Point, b: Point, max_len: float) -> list[Point]:
    """Points after a on the way to b (b included), no step longer than max_len."""
    d = math.dist(a, b)
    n = max(1, math.ceil(d / max_len))
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(1, n + 1)]


CURVE_MIN_STITCH = 1.0      # mm, the shortest stitch used on tight curves


def resample(points: np.ndarray, spacing: float, corner_deg: float,
             min_len: float = 0.0, curve_turn_deg: float = 11.0) -> list[Point]:
    """Place needle drops along a line: spacing apart on straight runs, closer on
    curves (each stitch turning at most curve_turn_deg; 0 = even spacing), always
    on sharp corners, and never closer together than min_len."""
    points = np.asarray(points, dtype=float)
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
        targets = _curve_targets(seg, d, cum, spacing, curve_turn_deg,
                                 max(2 * min_len, CURVE_MIN_STITCH))
        xs = np.interp(targets, cum, seg[:, 0])
        ys = np.interp(targets, cum, seg[:, 1])
        out.extend(zip(xs.tolist(), ys.tolist()))
    return drop_close(out, min_len)


def _curve_targets(seg, d, cum, spacing: float, turn_deg: float, shortest: float):
    """Distances along one corner-to-corner span where needle points go: stitch_len
    apart on straight runs, shorter where the line curves (so each stitch turns at
    most turn_deg), never below `shortest`. The steps are then stretched slightly so
    the last one lands exactly on the span's end. A straight span gets exactly the
    old even spacing."""
    total = float(cum[-1])
    n = len(seg)
    # curvature (degrees per mm) at each vertex: its turn over the length around it
    kappa = np.zeros(n)
    for i in range(1, n - 1):
        v1, v2 = seg[i] - seg[i - 1], seg[i + 1] - seg[i]
        l1, l2 = d[i - 1], d[i]
        if l1 < 1e-9 or l2 < 1e-9:
            continue
        c = float(np.dot(v1, v2) / (l1 * l2))
        kappa[i] = math.degrees(math.acos(max(-1.0, min(1.0, c)))) / ((l1 + l2) / 2)
    if n > 2:
        kappa[0], kappa[-1] = kappa[1], kappa[-2]
    if turn_deg <= 0 or not kappa.any():
        k = max(1, math.ceil(total / spacing))
        return np.linspace(total / k, total, k)
    steps, s = [], 0.0
    while s < total - 1e-9:
        kv = float(np.interp(s, cum, kappa))
        step = spacing if kv <= 1e-9 else min(spacing, max(shortest, turn_deg / kv))
        steps.append(step)
        s += step
    steps = np.array(steps) * (total / s)                   # land exactly on the end
    return np.cumsum(steps)


def segment_distance(p: Point, a: Point, b: Point) -> float:
    """Distance from p to the segment a-b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def point_in_polygon(p: Point, pts) -> bool:
    """Even-odd test against a closed polyline (first point repeated at the end)."""
    x, y = p
    inside = False
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def winding_number(p: Point, pts) -> int:
    """How many times a closed polyline (first point repeated at the end) winds
    around p, counting direction: 0 means outside under the nonzero rule."""
    x, y = p
    w = 0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            w += 1 if y2 > y1 else -1
    return w


def polygon_area(pts) -> float:
    """Area enclosed by a closed polyline (first point repeated at the end)."""
    return abs(signed_area(pts))


def signed_area(pts) -> float:
    """polygon_area, positive when the points run clockwise on screen (y down)."""
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:])) / 2
