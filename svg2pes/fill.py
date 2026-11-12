"""Fill stitching ("tatami") and satin columns.

Fill: rows of stitches back and forth across a closed shape. The shape's
closed outlines are filled with the even-odd rule, so holes (the middle of an
'o', a ring) stay empty. Rows run at `angle` degrees, `spacing` mm apart.
Needle points along a row sit on a grid that shifts by a third of a stitch each
row, so they don't line up into visible ridges.

Quality features:
- underlay: sparse rows at right angles, kept in from the edge, sewn first so
  the top fill lies flat;
- pull compensation: rows are lengthened slightly at the shape's edge, because
  stitches pull the fabric in along their length;
- cut-outs: areas covered by filled shapes stacked above are left out, so
  fills don't pile up;
- travel: separate pieces of one fill are joined by stitches running along
  the shape's edge (later covered by the outline) instead of by jumps.

Satin: a zig-zag column of a set width following a line, with a centre-walk
underlay, for smooth borders.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .geometry import drop_close, subdivide

Point = tuple[float, float]
RGB = tuple[int, int, int]

CLOSE_GAP_MM = 0.5     # outlines whose ends are this close count as closed
MAX_TRAVEL_MM = 40.0   # longer trips along the edge are left as jumps


@dataclass(frozen=True)
class FillSpec:
    color: RGB | None = None    # fill thread; None = the object's own thread
    angle: float = 45.0         # row direction, degrees (0 = horizontal, positive = clockwise)
    spacing: float = 0.4        # mm between rows (smaller = denser)
    stitch_len: float = 3.5     # mm, longest stitch along a row
    underlay: bool = True       # sparse rows at right angles first
    pull_comp: float = 0.2      # mm added to each end of a row at the shape's edge


@dataclass(frozen=True)
class SatinSpec:
    width: float = 2.0          # mm, column width
    spacing: float = 0.4        # mm between needle points along the column
    underlay: bool = True       # centre walk first
    pull_comp: float = 0.2      # mm added to the width


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


# --------------------------------------------------------------------- rows
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


def _crossings(edges, y) -> list[float]:
    return sorted(x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                  for x1, y1, x2, y2 in edges if (y1 <= y < y2) or (y2 <= y < y1))


def _subtract(segs, cuts):
    """Intervals in segs minus the intervals in cuts (both sorted lists of (a, b))."""
    out = []
    for a, b in segs:
        pieces = [(a, b)]
        for c, d in cuts:
            nxt = []
            for p, q in pieces:
                if d <= p or c >= q:
                    nxt.append((p, q))
                    continue
                if c > p:
                    nxt.append((p, c))
                if d < q:
                    nxt.append((d, q))
            pieces = nxt
        out.extend(pieces)
    return out


def _rows(rings, cutouts, angle, spacing, inset=0.0, extend=0.0):
    """Row segments of the shape in a frame where rows are horizontal.
    Returns (rows, (cos, sin)); rows is a list of (y, [(x0, x1), ...])."""
    a = math.radians(angle)
    co, si = math.cos(a), math.sin(a)

    def rot(ring):
        return [(x * co + y * si, -x * si + y * co) for x, y in ring]

    def edges_of(rs):
        return [(p[0], p[1], q[0], q[1]) for ring in rs for p, q in zip(ring, ring[1:]) if p[1] != q[1]]

    edges = edges_of([rot(r) for r in rings])
    cut_edges = [edges_of([rot(r) for r in obj_rings]) for obj_rings in (cutouts or [])]
    if not edges:
        return [], (co, si)
    ymin = min(min(e[1], e[3]) for e in edges)
    ymax = max(max(e[1], e[3]) for e in edges)
    rows = []
    y = ymin + spacing / 2
    while y < ymax:
        xs = _crossings(edges, y)
        segs = [(xs[i] - extend + inset, xs[i + 1] + extend - inset)
                for i in range(0, len(xs) - 1, 2)]
        segs = [(p, q) for p, q in segs if q - p > 1e-6]
        for ce in cut_edges:
            cx = _crossings(ce, y)
            cuts = [(cx[i], cx[i + 1]) for i in range(0, len(cx) - 1, 2)]
            if cuts:
                segs = _subtract(segs, cuts)
        rows.append((y, [(p, q) for p, q in segs if q - p > 0.3]))
        y += spacing
    return rows, (co, si)


def _serpentine(rows, L: float, min_len: float, stagger: bool = True) -> list[list[Point]]:
    """Join row segments into back-and-forth paths (in the rows' frame)."""
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
                off = (r % 3) * L / 3 if stagger else 0.0
                row = _row_points(start, end, y, off, L, min_len)
                if path:
                    path.extend(subdivide(path[-1], row[0], L))   # step down to this row
                    path.extend(row[1:])
                else:
                    path.extend(row)
                if r + 1 >= len(rows):
                    break
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
            paths.append(drop_close(path, min_len))
    return [p for p in paths if len(p) >= 2]


def _unrotate(paths, cs):
    co, si = cs
    return [[(u * co - v * si, u * si + v * co) for u, v in p] for p in paths]


# --------------------------------------------------------------------- travel
def _nearest_on_ring(ring, p):
    """(distance, position along the ring in mm, point) of the ring point nearest p."""
    best = None
    walked = 0.0
    for a, b in zip(ring, ring[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
        q = (a[0] + t * dx, a[1] + t * dy)
        d = math.dist(p, q)
        if best is None or d < best[0]:
            best = (d, walked + t * math.sqrt(L2), q)
        walked += math.sqrt(L2)
    return best


def _ring_between(ring, s0, s1):
    """Points along a closed ring from position s0 to s1 (mm), the shorter way round."""
    lengths = [math.dist(a, b) for a, b in zip(ring, ring[1:])]
    total = sum(lengths)
    if total <= 0:
        return []
    fwd = (s1 - s0) % total
    if fwd > total / 2:                                  # go backwards instead
        rev = list(reversed(ring))
        return _ring_between(rev, total - s0, total - s1)
    pts, walked = [], 0.0
    for (a, b), L in zip(zip(ring, ring[1:]), lengths):
        walked += L
        # keep the ring's corners between s0 and s0 + fwd (wrapping round once)
        for offset in (0.0, total):
            pos = walked + offset
            if s0 < pos < s0 + fwd:
                pts.append((pos, b))
    pts.sort()
    return [p for _, p in pts]


def travel_join(paths, rings, L: float = 2.5, max_gap: float = 1.0) -> list[list[Point]]:
    """Join consecutive paths whose ends lie near the same ring with stitches
    along that ring, instead of a jump. Paths that can't be joined stay separate."""
    if not paths:
        return []
    out = [list(paths[0])]
    for nxt in paths[1:]:
        a, b = out[-1][-1], nxt[0]
        joined = False
        for ring in rings:
            ea, eb = _nearest_on_ring(ring, a), _nearest_on_ring(ring, b)
            if ea[0] > max_gap or eb[0] > max_gap:
                continue
            route = [ea[2]] + _ring_between(ring, ea[1], eb[1]) + [eb[2]]
            length = sum(math.dist(p, q) for p, q in zip([a] + route, route + [b]))
            if length > MAX_TRAVEL_MM:
                continue
            here = a
            for p in route + [b]:
                if math.dist(here, p) > 1e-6:
                    out[-1].extend(subdivide(here, p, L))
                    here = p
            out[-1].extend(nxt[1:])
            joined = True
            break
        if not joined:
            out.append(list(nxt))
    return out


# --------------------------------------------------------------------- public
def fill_paths(polylines, spec: FillSpec, min_len: float = 0.4,
               cutouts=None) -> list[list[Point]]:
    """Needle-point paths that fill the closed outlines in `polylines` (mm):
    underlay first (if any), then the top fill, joined by travel where possible.
    cutouts: for each filled shape stacked above, its rings (areas left out)."""
    rings = closed_rings(polylines)
    if not rings or spec.spacing <= 0 or spec.stitch_len <= 0:
        return []
    paths = []
    if spec.underlay:
        rows, cs = _rows(rings, cutouts, spec.angle + 90, max(2.0, spec.spacing * 5), inset=0.6)
        paths += _unrotate(_serpentine(rows, min(3.0, spec.stitch_len), min_len, stagger=False), cs)
    rows, cs = _rows(rings, cutouts, spec.angle, spec.spacing, extend=spec.pull_comp)
    paths += _unrotate(_serpentine(rows, spec.stitch_len, min_len), cs)
    return travel_join(paths, rings, L=2.5)


class _Line:
    """A polyline measured by distance along it."""

    def __init__(self, pts):
        self.pts = pts
        self.seg = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
        self.cum = [0.0]
        for d in self.seg:
            self.cum.append(self.cum[-1] + d)
        self.total = self.cum[-1]

    def at(self, s: float) -> Point:
        s = max(0.0, min(self.total, s))
        i = 0
        while i < len(self.seg) - 1 and self.cum[i + 1] < s:
            i += 1
        t = 0.0 if self.seg[i] == 0 else (s - self.cum[i]) / self.seg[i]
        a, b = self.pts[i], self.pts[i + 1]
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    def tangent(self, s: float, h: float) -> Point:
        """Direction at s, averaged over h either side (one-sided at the ends)."""
        p0, p1 = self.at(s - h), self.at(s + h)
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        n = math.hypot(dx, dy) or 1.0
        return dx / n, dy / n


def _split_at_corners(pts, max_turn_deg=100.0, look=0.8):
    """Pieces of a line, cut where it turns more sharply than max_turn_deg, so a
    satin column doesn't fan out into spikes at hairpin bends."""
    line = _Line(pts)
    cuts = []
    for i in range(1, len(pts) - 1):
        s = line.cum[i]
        if s < look or line.total - s < look:
            continue
        a, b, c = line.at(s - look), pts[i], line.at(s + look)
        v1, v2 = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 < 1e-9 or n2 < 1e-9:
            continue
        cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
        if math.degrees(math.acos(cos)) > max_turn_deg and (not cuts or s - line.cum[cuts[-1]] > look):
            cuts.append(i)
    pieces, start = [], 0
    for i in cuts:
        pieces.append(pts[start:i + 1])
        start = i
    pieces.append(pts[start:])
    return [p for p in pieces if len(p) >= 2]


def _zigzag(pts, half: float, spacing: float) -> list[Point]:
    line = _Line(pts)
    if line.total <= 0:
        return []
    n = max(2, math.ceil(line.total / spacing))
    h = max(0.6, half)
    out = []
    for i in range(n + 1):
        s = line.total * i / n
        x, y = line.at(s)
        tx, ty = line.tangent(s, h)
        side = half if i % 2 == 0 else -half
        out.append((x - ty * side, y + tx * side))
    return out


def satin_path(points, closed: bool, spec: SatinSpec, min_len: float = 0.4) -> list[Point]:
    """Needle points for a satin column of spec.width centred on a line. A
    centre-walk underlay goes out first and the satin comes back over it; the
    column is split at sharp corners."""
    pts = list(points)
    if closed and pts[0] != pts[-1]:
        pts.append(pts[0])
    if len(pts) < 2:
        return []
    line = _Line(pts)
    if line.total < min_len:
        return []
    half = (spec.width + spec.pull_comp) / 2
    out: list[Point] = []
    if spec.underlay:                                   # centre walk out ...
        k = max(1, math.ceil(line.total / 2.0))
        out += [line.at(line.total * i / k) for i in range(k + 1)]
        if not closed:                                  # ... and the satin comes back
            pts = pts[::-1]
    for piece in _split_at_corners(pts):
        out += _zigzag(piece, half, spec.spacing)
    return drop_close(out, min_len * 0.5)
