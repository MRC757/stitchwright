"""Contour fill: rows that follow a shape's outline, ring inside ring.

The shape is drawn on a fine grid, each grid point's distance to the shape's edge is
measured, and rings are traced where that distance is spacing/2, 3*spacing/2, ...
(like the contour lines of a map). Each ring is then sewn all the way round, and
the needle steps in to the next ring, so the rings spiral inwards. Rings that can't
be reached by a short step start a new path; fill.py then orders those paths and
joins them where it can.

Only numpy is used (no geometry library): the distance is exact (a two-pass squared
distance transform), and the rings come from marching squares with linear
interpolation, so they are accurate to well under the grid size.
"""
from __future__ import annotations

import math

import numpy as np

from .geometry import drop_close, resample, signed_area, simplify

Point = tuple[float, float]

GRID_MM = 0.1            # grid size for small and medium shapes
MAX_CELLS = 1_500_000    # bigger shapes use a coarser grid
RING_TOL_MM = 0.02       # rings are simplified this much before stitching
CORNER_DEG = 60.0        # contour stitches always drop the needle on sharper turns


def _mask(rings, cutouts, rule, px):
    """The shape on a grid of px-sized cells: (inside mask, x of column 0, y of row 0),
    with a one-cell empty border. Uses fill.py's own row spans, so the fill rule and
    cut-outs behave exactly as they do for row fills."""
    from .fill import _rows                     # here, to avoid a circular import
    simple = [simplify(r, RING_TOL_MM) for r in rings]
    rows, _ = _rows(simple, cutouts, 0.0, px, rule=rule)
    xs = [p[0] for r in simple for p in r]
    x0 = min(xs) - px                           # border column
    width = int(math.ceil((max(xs) - x0) / px)) + 2
    mask = np.zeros((len(rows) + 2, width), dtype=bool)
    centres = x0 + (np.arange(width) + 0.5) * px
    for r, (_, segs) in enumerate(rows, start=1):
        for a, b in segs:
            i0, i1 = np.searchsorted(centres, [a, b])
            mask[r, i0:i1] = True
    y0 = (rows[0][0] - px) if rows else 0.0
    return mask, x0 + 0.5 * px, y0


def _distance(mask: np.ndarray, px: float) -> np.ndarray:
    """Distance (mm) from each cell centre inside the mask to the nearest centre
    outside it: exact Euclidean, by columns first and then along rows."""
    h, w = mask.shape
    big = h + w
    g = np.where(mask, big, 0).astype(np.int64)
    for r in range(1, h):                       # down each column
        g[r] = np.where(mask[r], np.minimum(g[r], g[r - 1] + 1), 0)
    for r in range(h - 2, -1, -1):              # and up
        g[r] = np.minimum(g[r], g[r + 1] + 1)
    d2 = g * g
    g2 = d2.copy()
    k = 1
    while k < w and k * k < d2.max():           # along the rows, as far as it matters
        kk = k * k
        np.minimum(d2[:, :-k], g2[:, k:] + kk, out=d2[:, :-k])
        np.minimum(d2[:, k:], g2[:, :-k] + kk, out=d2[:, k:])
        k += 1
    return np.sqrt(d2) * px


# marching squares: corners a (top left) = 1, b (top right) = 2, c (bottom right) = 4,
# d (bottom left) = 8; edges T(op), R(ight), B(ottom), L(eft). Saddles (5, 10) are
# decided by the cell's middle value.
_SEGMENTS = {1: ("LT",), 2: ("TR",), 3: ("LR",), 4: ("RB",), 6: ("TB",), 7: ("LB",),
             8: ("BL",), 9: ("TB",), 11: ("RB",), 12: ("LR",), 13: ("TR",), 14: ("LT",)}
_SADDLE = {(5, True): ("TR", "BL"), (5, False): ("LT", "RB"),
           (10, True): ("LT", "RB"), (10, False): ("TR", "BL")}


def _iso_rings(F: np.ndarray, t: float) -> list[list[tuple[float, float]]]:
    """Closed rings (in grid units: column, row) where F crosses t."""
    a, b, c, d = F[:-1, :-1], F[:-1, 1:], F[1:, 1:], F[1:, :-1]
    idx = (a >= t) * 1 + (b >= t) * 2 + (c >= t) * 4 + (d >= t) * 8
    rr, cc = np.nonzero((idx > 0) & (idx < 15))
    if len(rr) == 0:
        return []
    w = F.shape[1]
    adj: dict[int, list[int]] = {}

    def key(edge, r, col):
        # horizontal edges (top of row r) are even keys, vertical ones (left of col) odd
        if edge == "T":
            return (r * w + col) * 2
        if edge == "B":
            return ((r + 1) * w + col) * 2
        if edge == "L":
            return (r * w + col) * 2 + 1
        return (r * w + col + 1) * 2 + 1          # R

    for r, col, case in zip(rr.tolist(), cc.tolist(), idx[rr, cc].tolist()):
        if case in (5, 10):
            mid = (F[r, col] + F[r, col + 1] + F[r + 1, col] + F[r + 1, col + 1]) / 4
            segs = _SADDLE[(case, bool(mid >= t))]
        else:
            segs = _SEGMENTS[case]
        for s in segs:
            p, q = key(s[0], r, col), key(s[1], r, col)
            adj.setdefault(p, []).append(q)
            adj.setdefault(q, []).append(p)

    def where(k):
        cell, vertical = divmod(k, 2)
        r, col = divmod(cell, w)
        if vertical:                              # between (r, col) and (r + 1, col)
            f0, f1 = F[r, col], F[r + 1, col]
            s = (t - f0) / (f1 - f0) if f1 != f0 else 0.5
            return (float(col), r + float(s))
        f0, f1 = F[r, col], F[r, col + 1]         # between (r, col) and (r, col + 1)
        s = (t - f0) / (f1 - f0) if f1 != f0 else 0.5
        return (col + float(s), float(r))

    rings = []
    while adj:
        start = next(iter(adj))
        ring, prev, cur = [start], None, start
        while True:
            nbrs = adj.pop(cur, [])
            nxt = next((n for n in nbrs if n != prev and n in adj), None)
            if nxt is None:
                break
            prev, cur = cur, nxt
            if cur == start:
                break
            ring.append(cur)
        if len(ring) >= 3:
            pts = [where(k) for k in ring]
            rings.append(pts + [pts[0]])
    return rings


def contour_rings(rings, cutouts, spacing: float, rule: str, first: float | None = None
                  ) -> list[tuple[int, list[Point]]]:
    """(level, ring) for every contour ring of the shape, outermost first; ring points
    in mm, closed, all running the same way round. Level 0 lies `first` mm inside the
    edge (default spacing/2), each further level `spacing` mm further in."""
    if not rings:
        return []
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    area = (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1)
    px = max(GRID_MM, math.sqrt(area / MAX_CELLS), spacing / 3)
    mask, gx0, gy0 = _mask(rings, cutouts, rule, px)
    if not mask.any():
        return []
    F = _distance(mask, px)
    first = spacing / 2 if first is None else first
    out = []
    level = 0
    deepest = float(F.max())
    while True:
        depth = first + level * spacing
        # cell centres sit half a cell in; nudged off the grid's own distances, which
        # otherwise put whole runs of cells exactly on the level (and break it into bits)
        t = depth + px * 0.5137
        if t >= deepest:
            break
        for ring in _iso_rings(F, t):
            mm = [(gx0 + c * px, gy0 + r * px) for c, r in ring]
            mm = simplify(mm, RING_TOL_MM)
            if len(mm) < 4:
                continue
            if signed_area(mm) < 0:                # all rings one way round
                mm = mm[::-1]
            out.append((level, mm))
        level += 1
    return out


def contour_paths(rings, cutouts, spacing: float, stitch_len: float, rule: str,
                  min_len: float = 0.4, first: float | None = None) -> list[list[Point]]:
    """Needle points that fill the shape with contour rings: paths that start on an
    outer ring and step in ring by ring while the next ring is close by."""
    from .fill import _Edges                    # here, to avoid a circular import
    levels = contour_rings(rings, cutouts, spacing, rule, first)
    shortest = max(4 * min_len, 1.5)
    keep = [(lv, r) for lv, r in levels
            if sum(math.dist(p, q) for p, q in zip(r, r[1:])) >= shortest]
    if not keep:
        return []
    loops = [r for _, r in keep]
    edges = _Edges(loops, max_gap=1.6 * spacing + 0.05)
    todo = set(range(len(loops)))
    paths: list[list[Point]] = []
    while todo:
        k = min(todo, key=lambda i: (keep[i][0], -len(loops[i])))   # outermost, longest
        hit = None                                 # start at the ring's first point
        path: list[Point] = []
        while True:
            todo.discard(k)
            ring = loops[k]
            start = 0 if hit is None else _index_at(edges, k, hit[1])
            here = ring[0] if hit is None else hit[2]
            turn = [here] + ring[start + 1:-1] + ring[:start + 1]
            pts = resample(turn + [here], stitch_len, CORNER_DEG, min_len)
            pts = pts[:-1] if len(pts) > 2 else pts   # stop a stitch short; step in from there
            path.extend(pts)
            nxt = None
            for j, h in edges.near(path[-1]).items():
                if j in todo and (nxt is None or h[0] < nxt[1][0]):
                    nxt = (j, h)
            if nxt is None:
                break
            k, hit = nxt
        path = drop_close(path, min_len)
        if len(path) >= 2:
            paths.append(path)
    return paths


def _index_at(edges, k: int, pos: float) -> int:
    """The index of ring k's point at or just before `pos` mm along it."""
    along = edges._segs[k][3]
    return int(min(max(np.searchsorted(along, pos, side="right") - 1, 0), len(along) - 2))
