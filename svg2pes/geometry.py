"""2-D affine transforms for placing objects in the hoop (millimetres, y down).

A transform is a tuple (a, b, c, d, e, f), the same layout as an SVG matrix:
    x' = a*x + c*y + e
    y' = b*x + d*y + f
"""
from __future__ import annotations

import math

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
