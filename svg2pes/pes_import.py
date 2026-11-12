"""Read an existing embroidery file (PES, and others pyembroidery reads) as objects.

Each colour block becomes one object whose polylines are its exact needle
points (split where the machine jumps or trims). Such objects are sewn exactly
as they are: they can be moved, turned, recoloured and skipped, but not refilled.
"""
from __future__ import annotations

import os

import pyembroidery

from .svg_import import Design, DesignObject, Polyline

READ_EXTENSIONS = (".pes", ".pec", ".dst", ".jef", ".exp", ".vp3", ".xxx", ".u01", ".hus")


def load_stitches(path: str) -> Design:
    ext = os.path.splitext(path)[1].lower()
    if ext not in READ_EXTENSIONS:
        raise ValueError(f"Can't read {ext or 'this kind of'} embroidery file")
    try:
        pattern = pyembroidery.read(path)
    except Exception as e:  # pyembroidery raises many types for damaged files
        raise ValueError(f"Could not read {os.path.basename(path)}: {e}") from e
    if pattern is None or not pattern.stitches:
        raise ValueError(f"{os.path.basename(path)} has no stitches")

    threads = pattern.threadlist or []
    objects: list[DesignObject] = []
    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    block = 0

    def finish_block():
        nonlocal runs, current
        if len(current) >= 2:
            runs.append(current)
        current = []
        if runs:
            t = threads[block] if block < len(threads) else None
            rgb = ((t.color >> 16) & 255, (t.color >> 8) & 255, t.color & 255) if t else (0, 0, 0)
            n = len(objects) + 1
            objects.append(DesignObject(n, f"stitches{n}", "Stitches", rgb,
                                        [Polyline(r, False) for r in runs], color=rgb,
                                        stitches=True))
        runs = []

    for x, y, cmd in pattern.stitches:
        c = cmd & 0xFF
        p = (x / 10.0, y / 10.0)
        if c == pyembroidery.STITCH:
            if not current or current[-1] != p:
                current.append(p)
        elif c in (pyembroidery.JUMP, pyembroidery.TRIM):
            if len(current) >= 2:
                runs.append(current)
            current = [p] if c == pyembroidery.JUMP else []
        elif c == pyembroidery.COLOR_CHANGE:
            finish_block()
            block += 1
        elif c == pyembroidery.END:
            break
    finish_block()
    # a jump's landing point alone is not a stitch
    for o in objects:
        o.polylines = [pl for pl in o.polylines if len(pl.points) >= 2]
    warnings = []
    if not objects:
        warnings.append("No stitches found")
    return Design(objects, warnings)
