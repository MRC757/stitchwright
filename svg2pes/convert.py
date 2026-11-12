"""Top-level SVG -> PES conversion used by both the CLI and (later) the GUI."""
from __future__ import annotations

import copy
import dataclasses
import math
import os
from dataclasses import dataclass, field

import pyembroidery
from pyembroidery import EmbPattern, EmbThread

from .palette import Thread, nearest_brother, parse_color, thread_for_rgb, to_hex
from .preview import render_preview
from .stitching import ColorBlock, Settings, plan_blocks, stitch_points
from .svg_import import Design, load_svg

RGB = tuple[int, int, int]

MAX_COLOR_BLOCKS = 255      # PEC stores the colour count in one byte
MAX_STITCH_LEN = 12.0       # mm; PEC stitches longer than ~12.7 mm need splitting


class ConversionError(Exception):
    pass


@dataclass
class ThreadUse:
    object_ids: list[str]   # objects sewn in this colour block
    chosen: list[RGB]       # colours the operator / SVG asked for
    thread: Thread | None   # Brother chart thread (None with --no-snap)
    rgb: RGB                # colour written to the file


@dataclass
class Report:
    out_path: str
    stitch_count: int
    jump_count: int
    trim_count: int
    color_blocks: int
    width_mm: float
    height_mm: float
    scale: float
    thread_map: list[ThreadUse] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def apply_color_choices(design: Design, overrides: dict[str, str] | None,
                        color_map: dict[str, str] | None, skip: list[str] | None) -> None:
    """Apply the operator's colour picks. Per-object overrides beat source-colour maps."""
    by_id = {o.obj_id: o for o in design.objects}
    by_index = {str(o.index): o for o in design.objects}

    def find(key: str):
        a, b = by_id.get(key), by_index.get(key)
        if a is not None and b is not None and a is not b:
            raise ConversionError(
                f"{key!r} is ambiguous: it is the id of object {a.index} and the number of "
                f"object {b.obj_id!r}. Use the other object's id instead.")
        o = a or b
        if o is None:
            ids = ", ".join(x.obj_id for x in design.objects)
            raise ConversionError(f"No object {key!r}. Objects in this file: {ids}")
        return o

    if color_map:
        for src, dst in color_map.items():
            src_rgb, dst_rgb = parse_color(src), parse_color(dst)
            hits = [o for o in design.objects if o.source_color == src_rgb]
            if not hits:
                raise ConversionError(f"No object uses colour {src}")
            for o in hits:
                o.color = dst_rgb
    for key, col in (overrides or {}).items():
        find(key).color = parse_color(col)
    for key in skip or []:
        find(key).skipped = True


def snap_colors(design: Design) -> dict[str, RGB]:
    """Replace every object's colour with its nearest Brother thread, so that
    colours which end up as the same thread are sewn as one colour block.
    Returns the colours chosen before snapping, by object id."""
    chosen = {}
    for o in design.objects:
        chosen[o.obj_id] = o.color
        o.color = nearest_brother(o.color).rgb
    return chosen


def _scale_design(design: Design, scale: float) -> None:
    for o in design.objects:
        for pl in o.polylines:
            pl.points = [(x * scale, y * scale) for x, y in pl.points]


def _center_design(design: Design) -> None:
    x0, y0, x1, y1 = design.bounds()
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    for o in design.objects:
        for pl in o.polylines:
            pl.points = [(x - cx, y - cy) for x, y in pl.points]


def _check_inputs(hoop_mm, margin_mm, width_mm, s: Settings) -> None:
    if hoop_mm[0] <= 0 or hoop_mm[1] <= 0:
        raise ConversionError(f"Hoop size must be positive, got {hoop_mm[0]:g} x {hoop_mm[1]:g} mm")
    if margin_mm < 0:
        raise ConversionError(f"Margin cannot be negative, got {margin_mm:g} mm")
    if min(hoop_mm) - 2 * margin_mm <= 0:
        raise ConversionError(f"A {margin_mm:g} mm margin leaves no room in a "
                              f"{hoop_mm[0]:g} x {hoop_mm[1]:g} mm hoop")
    if width_mm is not None and width_mm <= 0:
        raise ConversionError(f"Width must be positive, got {width_mm:g} mm")
    if not (0.5 <= s.stitch_len <= MAX_STITCH_LEN):
        raise ConversionError(f"Stitch length must be between 0.5 and {MAX_STITCH_LEN:g} mm, "
                              f"got {s.stitch_len:g} mm")
    if s.min_stitch >= s.stitch_len:
        raise ConversionError("Minimum stitch length must be shorter than the stitch length")


def build_pattern(blocks: list[ColorBlock], s: Settings) -> EmbPattern:
    """Assemble an EmbPattern. Coordinates are millimetres in, 0.1 mm units out."""
    pattern = EmbPattern()
    here = (0, 0)

    def u(v: float) -> int:
        return int(round(v * 10))

    for bi, blk in enumerate(blocks):
        t = EmbThread()
        t.set_color(*blk.color)
        bt = thread_for_rgb(blk.color)
        if bt is not None:
            t.description, t.catalog_number, t.brand = bt.name, bt.number, "Brother"
        else:
            t.description = "custom"
        pattern.add_thread(t)

        if bi > 0:
            pattern.color_change()
        for run in blk.runs:
            pts = [(u(x), u(y)) for x, y in stitch_points(run, s)]
            # remove consecutive duplicates created by rounding
            clean = [pts[0]]
            for p in pts[1:]:
                if p != clean[-1]:
                    clean.append(p)
            if len(clean) < 2:
                continue
            if math.dist(clean[0], here) / 10.0 > s.trim_dist:
                pattern.trim()
            pattern.move_abs(*clean[0])
            for p in clean:
                pattern.stitch_abs(*p)
            here = clean[-1]
    pattern.end()
    return pattern


def _fit_scale(w: float, h: float, avail_w: float, avail_h: float) -> float:
    limits = [a / d for a, d in ((avail_w, w), (avail_h, h)) if d > 0]
    return min(limits) if limits else 1.0


@dataclass
class Plan:
    """A finished stitch plan, ready to preview or write."""
    pattern: EmbPattern
    report: Report      # out_path is empty until the plan is written


def plan_design(design: Design, *,
                overrides: dict[str, str] | None = None,
                color_map: dict[str, str] | None = None,
                skip: list[str] | None = None,
                width_mm: float | None = None,
                fit: bool = False,
                hoop_mm: tuple[float, float] = (100.0, 100.0),
                margin_mm: float = 2.0,
                settings: Settings | None = None,
                snap: bool = True) -> Plan:
    """Turn a loaded design into stitches without writing anything.
    The design passed in is not modified."""
    s = settings or Settings()
    _check_inputs(hoop_mm, margin_mm, width_mm, s)
    design = copy.deepcopy(design)
    apply_color_choices(design, overrides, color_map, skip)
    if not design.active():
        raise ConversionError("Nothing to stitch: " + "; ".join(design.warnings or ["all objects skipped"]))
    warnings = list(design.warnings)
    if snap:
        chosen = snap_colors(design)
    else:
        chosen = {o.obj_id: o.color for o in design.objects}
        warnings.append("Colours not snapped: the machine will show the nearest Brother "
                        "colour name for each thread")

    x0, y0, x1, y1 = design.bounds()
    w, h = x1 - x0, y1 - y0
    if w <= 0 and h <= 0:
        raise ConversionError("Nothing to stitch: the design has no size")
    avail_w, avail_h = hoop_mm[0] - 2 * margin_mm, hoop_mm[1] - 2 * margin_mm
    scale = 1.0
    if width_mm is not None:
        if w <= 0:
            raise ConversionError("The design has no width (a vertical line), so --width "
                                  "cannot size it")
        scale = width_mm / w
    if w * scale > avail_w or h * scale > avail_h:
        fit_scale = _fit_scale(w, h, avail_w, avail_h)
        if fit:
            warnings.append(f"Design scaled to {fit_scale:.0%} to fit the hoop")
            scale = fit_scale
        else:
            raise ConversionError(
                f"Design is {w * scale:.1f} x {h * scale:.1f} mm but the usable area is "
                f"{avail_w:.0f} x {avail_h:.0f} mm (hoop {hoop_mm[0]:.0f} x {hoop_mm[1]:.0f} mm "
                f"minus {margin_mm:g} mm margin). Turn on shrink-to-fit (--fit) or set a "
                f"smaller width (--width).")
    if scale != 1.0:
        _scale_design(design, scale)
    _center_design(design)

    blocks = plan_blocks(design.active(), s)
    if not blocks:
        raise ConversionError("No outlines long enough to stitch")
    if len(blocks) > MAX_COLOR_BLOCKS:
        raise ConversionError(f"{len(blocks)} colour blocks: PES allows at most "
                              f"{MAX_COLOR_BLOCKS}. Drop --keep-order or use fewer colours.")
    pattern = build_pattern(blocks, s)

    stitches = sum(1 for _, _, c in pattern.stitches if (c & 0xFF) == pyembroidery.STITCH)
    jumps = sum(1 for _, _, c in pattern.stitches if (c & 0xFF) == pyembroidery.JUMP)
    trims = sum(1 for _, _, c in pattern.stitches if (c & 0xFF) == pyembroidery.TRIM)
    bx0, by0, bx1, by1 = pattern.bounds()

    tmap = []
    for blk in blocks:
        ids = list(dict.fromkeys(r.object_id for r in blk.runs))
        picked = list(dict.fromkeys(chosen[i] for i in ids))
        tmap.append(ThreadUse(ids, picked, thread_for_rgb(blk.color), blk.color))
    report = Report("", stitches, jumps, trims, len(blocks),
                    (bx1 - bx0) / 10.0, (by1 - by0) / 10.0, scale, tmap, warnings)
    return Plan(pattern, report)


def write_pes_file(plan: Plan, out_path: str, pes_version: int = 1) -> Report:
    """Write the plan as PES. The file is written to a temporary name and read
    back before it replaces out_path, so a failure never leaves a broken file."""
    tmp = out_path + ".part"
    try:
        pyembroidery.write_pes(plan.pattern, tmp, {"version": float(pes_version)})
        back = pyembroidery.read_pes(tmp)
        if back is None or not back.stitches:
            raise ConversionError("Written PES could not be read back")
        if len(back.threadlist) != plan.report.color_blocks:
            raise ConversionError(f"Written PES has {len(back.threadlist)} colours, "
                                  f"expected {plan.report.color_blocks}")
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return dataclasses.replace(plan.report, out_path=out_path)


def convert(svg_path: str, out_path: str, *,
            overrides: dict[str, str] | None = None,
            color_map: dict[str, str] | None = None,
            skip: list[str] | None = None,
            width_mm: float | None = None,
            fit: bool = False,
            hoop_mm: tuple[float, float] = (100.0, 100.0),
            margin_mm: float = 2.0,
            settings: Settings | None = None,
            pes_version: int = 1,
            snap: bool = True,
            preview_path: str | None = None) -> Report:
    """Load an SVG, plan it and write a PES file (what the CLI uses)."""
    s = settings or Settings()
    _check_inputs(hoop_mm, margin_mm, width_mm, s)
    plan = plan_design(load_svg(svg_path), overrides=overrides, color_map=color_map, skip=skip,
                       width_mm=width_mm, fit=fit, hoop_mm=hoop_mm, margin_mm=margin_mm,
                       settings=s, snap=snap)
    report = write_pes_file(plan, out_path, pes_version)
    if preview_path:
        render_preview(plan.pattern, preview_path, hoop_mm)
    return report


def describe_objects(design: Design) -> list[str]:
    lines = []
    for o in design.objects:
        x0, y0, x1, y1 = o.bounds()
        t = nearest_brother(o.source_color)
        lines.append(f"{o.index:>3}  {o.obj_id:<16} {o.kind:<9} {to_hex(o.source_color)}  "
                     f"{x1 - x0:5.1f} x {y1 - y0:5.1f} mm   -> {t.number} {t.name}")
    return lines
