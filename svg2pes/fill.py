"""Fill stitching ("tatami"): rows of stitches back and forth across a closed shape.

The shape's closed outlines are filled with the even-odd rule, so holes (the
middle of an 'o', a ring) stay empty. Rows run at `angle` degrees, `spacing`
mm apart. Needle points along a row sit on a grid that shifts by a third of a
stitch each row, so they don't line up into visible ridges.

Rows are joined into serpentine runs: from the end of one row the needle steps
to the overlapping piece of the next row and comes back the other way. Where
the shape branches, each branch becomes its own run (joined later by a jump).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float]
RGB = tuple[int, int, int]

CLOSE_GAP_MM = 0.5     # outlines whose ends are this close count as closed


@dataclass(frozen=True)
class FillSpec:
    color: RGB | None = None    # fill thread; None = the object's own thread
    angle: float = 45.0         # row direction, degrees (0 = horizontal, positive = clockwise)
    spacing: float = 0.4        # mm between rows (smaller = denser)
    stitch_len: float = 3.5     # mm, longest stitch along a row


def closed_rings(polylines) -> list[list[Point]]:
    """The closed (or nearly closed) outlines of a shape, each ending on its start."""
    rings = []
    for pl in polylines:
        pts = list(pl.points)
        if len(pts) < 3:
            continue
        if pl.closed or math.dist(pts[0], pts[-1]) <= CLOSE_GAP_MM:
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            rings.append(pts)
    return rings


def can_fill(polylines) -> bool:
    return bool(closed_rings(polylines))


def _row_points(xa: float, xb: float, y: float, grid_off: float, L: float,
                min_len: float) -> list[Point]:
    """Needle points from xa to xb (either direction) along row y."""
    lo, hi = min(xa, xb), max(xa, xb)
    n0 = math.ceil((lo - grid_off) / L)
    n1 = math.floor((hi - grid_off) / L)
    inner = [grid_off + n * L for n in range(n0, n1 + 1)]
    inner = [x for x in inner if x - lo >= min_len and hi - x >= min_len]
    if xa > xb:
        inner.reverse()
    return [(xa, y)] + [(x, y) for x in inner] + [(xb, y)]


def _split(a: Point, b: Point, L: float) -> list[Point]:
    """Points after a on the way to b, so no step is longer than L."""
    d = math.dist(a, b)
    n = max(1, math.ceil(d / L))
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(1, n + 1)]


def fill_paths(polylines, spec: FillSpec, min_len: float = 0.4) -> list[list[Point]]:
    """Needle-point paths that fill the closed outlines in `polylines` (mm)."""
    rings = closed_rings(polylines)
    if not rings or spec.spacing <= 0 or spec.stitch_len <= 0:
        return []
    a = math.radians(spec.angle)
    co, si = math.cos(a), math.sin(a)
    # work in a frame where the rows are horizontal
    rot = [[(x * co + y * si, -x * si + y * co) for x, y in ring] for ring in rings]
    edges = [(p[0], p[1], q[0], q[1]) for ring in rot for p, q in zip(ring, ring[1:]) if p[1] != q[1]]
    if not edges:
        return []
    ymin = min(min(e[1], e[3]) for e in edges)
    ymax = max(max(e[1], e[3]) for e in edges)

    rows: list[tuple[float, list[tuple[float, float]]]] = []
    y = ymin + spec.spacing / 2
    while y < ymax:
        xs = sorted(x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                    for x1, y1, x2, y2 in edges if (y1 <= y < y2) or (y2 <= y < y1))
        segs = [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2) if xs[i + 1] - xs[i] > 1e-6]
        rows.append((y, segs))
        y += spec.spacing

    L = spec.stitch_len
    used = [[False] * len(segs) for _, segs in rows]
    paths: list[list[Point]] = []
    for r0, (_, segs0) in enumerate(rows):
        for j0 in range(len(segs0)):
            if used[r0][j0]:
                continue
            r, j, rightward = r0, j0, True
            path: list[Point] = []
            while True:
                used[r][j] = True
                y, segs = rows[r]
                x0, x1 = segs[j]
                start, end = (x0, x1) if rightward else (x1, x0)
                row = _row_points(start, end, y, (r % 3) * L / 3, L, min_len)
                if path:
                    path.extend(_split(path[-1], row[0], L))   # step down to this row
                    path.extend(row[1:])
                else:
                    path.extend(row)
                if r + 1 >= len(rows):
                    break
                # the overlapping, unused piece of the next row nearest to where we are
                ny, nsegs = rows[r + 1]
                here = path[-1]
                best = None
                for k, (u0, u1) in enumerate(nsegs):
                    if used[r + 1][k] or u0 >= x1 or u1 <= x0:
                        continue
                    nstart = u1 if rightward else u0          # next row runs the other way
                    dist = abs(nstart - here[0])
                    if best is None or dist < best[0]:
                        best = (dist, k)
                if best is None:
                    break
                r, j, rightward = r + 1, best[1], not rightward
            # drop points closer together than min_len (keeping the ends)
            clean = [path[0]]
            for p in path[1:-1]:
                if math.dist(p, clean[-1]) >= min_len:
                    clean.append(p)
            if len(path) > 1:
                if len(clean) > 1 and math.dist(path[-1], clean[-1]) < min_len:
                    clean[-1] = path[-1]
                else:
                    clean.append(path[-1])
            if len(clean) >= 2:
                paths.append([(u * co - v * si, u * si + v * co) for u, v in clean])
    return paths
