"""Turn a picture (PNG, JPEG, ...) into stitchable shapes.

The picture is reduced to a few colours, the background colour (the one most
common along the edges) is dropped, and each remaining colour's areas are
traced into closed outlines: pixel edges are linked into rings, straightened,
simplified and smoothed. Each colour becomes one object, meant to be filled;
the largest areas come first so smaller details are sewn on top.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter

from .svg_import import DesignObject, Polyline

MAX_PIXELS = 320          # long side, after shrinking: plenty for embroidery-sized shapes


@dataclass
class TraceSettings:
    colours: int = 4            # colours to keep (not counting the background)
    width_mm: float = 60.0      # stitched width of the whole picture
    drop_background: bool = True
    detail: str = "normal"      # fine | normal | coarse


def _prepare(path: str) -> Image.Image:
    img = Image.open(path)
    img.load()
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(white, rgba)
    img = img.convert("RGB")
    w, h = img.size
    k = MAX_PIXELS / max(w, h)
    if k < 1:
        img = img.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    return img


def _kmeans(pixels: np.ndarray, k: int, iters: int = 20) -> np.ndarray:
    """k colour centres for an (N, 3) array, k-means++ start with a fixed seed so the
    same picture always gives the same result."""
    rng = np.random.default_rng(1)
    sample = pixels if len(pixels) <= 20000 else pixels[rng.choice(len(pixels), 20000, replace=False)]
    centres = [sample[rng.integers(len(sample))]]
    for _ in range(1, k):
        d = np.min(((sample[:, None, :] - np.array(centres)[None]) ** 2).sum(-1), axis=1)
        if d.sum() <= 0:
            break
        centres.append(sample[rng.choice(len(sample), p=d / d.sum())])
    c = np.array(centres, dtype=float)
    for _ in range(iters):
        lab = ((sample[:, None, :] - c[None]) ** 2).sum(-1).argmin(1)
        new = np.array([sample[lab == j].mean(0) if np.any(lab == j) else c[j] for j in range(len(c))])
        if np.allclose(new, c, atol=0.5):
            break
        c = new
    return c


def reduce_colours(path: str, s: TraceSettings):
    """(index array H x W, palette [(r, g, b)], background index or None)."""
    img = _prepare(path).filter(ImageFilter.MedianFilter(3))
    n = max(1, min(12, s.colours)) + (1 if s.drop_background else 0)
    arr = np.asarray(img, dtype=float)
    h, w, _ = arr.shape
    flat = arr.reshape(-1, 3)
    centres = _kmeans(flat, n)
    idx = np.empty(len(flat), dtype=np.int32)
    for i in range(0, len(flat), 50000):               # assign in chunks to keep memory small
        idx[i:i + 50000] = ((flat[i:i + 50000, None, :] - centres[None]) ** 2).sum(-1).argmin(1)
    # Colours covering under 2% of the design are usually just the soft edges between
    # areas (anti-aliasing): give their pixels to the nearest real colour.
    counts = np.bincount(idx, minlength=len(centres))
    bg_guess = Counter(np.concatenate([idx.reshape(h, w)[0], idx.reshape(h, w)[-1],
                                       idx.reshape(h, w)[:, 0], idx.reshape(h, w)[:, -1]]).tolist()
                       ).most_common(1)[0][0] if s.drop_background else None
    fg_total = counts.sum() - (counts[bg_guess] if bg_guess is not None else 0)
    keep = [j for j in range(len(centres))
            if j == bg_guess or counts[j] >= 0.02 * max(fg_total, 1)]
    if len(keep) < len(centres):
        kc = centres[keep]
        for i in range(0, len(flat), 50000):
            idx[i:i + 50000] = np.array(keep)[((flat[i:i + 50000, None, :] - kc[None]) ** 2)
                                              .sum(-1).argmin(1)]
    idx = idx.reshape(h, w)
    palette = [tuple(int(round(v)) for v in c) for c in centres]
    background = None
    if s.drop_background:
        border = np.concatenate([idx[0], idx[-1], idx[:, 0], idx[:, -1]])
        background = Counter(border.tolist()).most_common(1)[0][0]
    return idx, palette, background


def preview(path: str, s: TraceSettings) -> Image.Image:
    """The reduced picture, background shown white (what will be stitched)."""
    idx, palette, bg = reduce_colours(path, s)
    lut = np.array([(255, 255, 255) if i == bg else palette[i] for i in range(len(palette))],
                   dtype=np.uint8)
    return Image.fromarray(lut[idx], "RGB")


def _rings(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """Closed outlines (in pixel-corner coordinates) around the True areas of mask."""
    m = np.pad(mask, 1)
    inner = m[1:-1, 1:-1]
    rows, cols = np.nonzero(inner & ~m[:-2, 1:-1])          # top edges
    edges = [((c, r), (c + 1, r)) for r, c in zip(rows.tolist(), cols.tolist())]
    rows, cols = np.nonzero(inner & ~m[1:-1, 2:])           # right edges
    edges += [((c + 1, r), (c + 1, r + 1)) for r, c in zip(rows.tolist(), cols.tolist())]
    rows, cols = np.nonzero(inner & ~m[2:, 1:-1])           # bottom edges
    edges += [((c + 1, r + 1), (c, r + 1)) for r, c in zip(rows.tolist(), cols.tolist())]
    rows, cols = np.nonzero(inner & ~m[1:-1, :-2])          # left edges
    edges += [((c, r + 1), (c, r)) for r, c in zip(rows.tolist(), cols.tolist())]
    nxt: dict = {}
    for a, b in edges:
        nxt.setdefault(a, []).append(b)
    rings = []
    while nxt:
        start = next(iter(nxt))
        ring = [start]
        cur = start
        while True:
            outs = nxt.get(cur)
            if not outs:
                break
            b = outs.pop()
            if not outs:
                del nxt[cur]
            if b == start:
                ring.append(b)
                break
            ring.append(b)
            cur = b
        if len(ring) >= 4 and ring[-1] == ring[0]:
            rings.append(ring)
    return rings


def _straighten(ring):
    """Keep only the corners of a staircase outline."""
    out = [ring[0]]
    for a, b, c in zip(ring, ring[1:], ring[2:]):
        if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
            out.append(b)
    out.append(ring[-1])
    return out


def _rdp(pts, tol):
    """Ramer-Douglas-Peucker simplification (iterative, so long outlines are fine)."""
    if len(pts) < 3:
        return list(pts)
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


def _simplify_ring(ring, tol):
    """RDP for a closed ring: split it at the point farthest from the start."""
    far = max(range(len(ring)), key=lambda i: math.dist(ring[i], ring[0]))
    a = _rdp(ring[:far + 1], tol)
    b = _rdp(ring[far:], tol)
    return a[:-1] + b


def _chaikin_closed(pts, rounds=2):
    for _ in range(rounds):
        ring = pts[:-1]
        out = []
        for a, b in zip(ring, ring[1:] + ring[:1]):
            out.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            out.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        pts = out + [out[0]]
    return pts


def _area(pts) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:]))) / 2


def trace_image(path: str, s: TraceSettings) -> tuple[list[DesignObject], list[str]]:
    """Objects (one per colour, closed outlines in mm, largest area first) and warnings."""
    idx, palette, bg = reduce_colours(path, s)
    h, w = idx.shape
    mm = s.width_mm / w
    tol, rounds, min_px = {"fine": (0.5, 2, 4), "coarse": (1.2, 3, 25)}.get(s.detail, (0.8, 2, 10))
    found = []
    for ci, rgb in enumerate(palette):
        if ci == bg:
            continue
        mask = idx == ci
        if mask.sum() < min_px:
            continue
        polys = []
        for ring in _rings(mask):
            if _area(ring) < min_px:
                continue
            pts = _simplify_ring(_straighten(ring), tol)
            if len(pts) < 4:
                continue
            pts = _chaikin_closed(pts, rounds)
            polys.append(Polyline([(x * mm, y * mm) for x, y in pts], True))
        if polys:
            found.append((int(mask.sum()), rgb, polys))
    found.sort(key=lambda f: -f[0])                    # big areas first, details on top
    objects = [DesignObject(n, f"picture{n}", "Picture", rgb, polys, color=rgb)
               for n, (_, rgb, polys) in enumerate(found, 1)]
    warnings = [] if objects else ["Nothing was left after removing the background"]
    return objects, warnings
