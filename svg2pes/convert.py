"""Top-level SVG -> PES conversion used by both the CLI and (later) the GUI."""
from __future__ import annotations

import copy
import dataclasses
import math
import os
from dataclasses import dataclass, field

import pyembroidery
from pyembroidery import EmbPattern, EmbThread

from .fill import FillSpec, SatinSpec, can_fill
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
    stitches: int = 0       # stitches sewn in this colour block


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
    sew_minutes: float = 0.0      # estimated time the machine spends sewing
    change_minutes: float = 0.0   # estimated time for thread changes

    @property
    def total_minutes(self) -> float:
        return self.sew_minutes + self.change_minutes


MACHINE_SPM = 650          # PE540D top speed, stitches per minute (adjust in Settings)
THREAD_CHANGE_MIN = 1.0    # minutes to stop, rethread and restart at a colour change
JUMP_SECONDS = 1.5         # a jump moves the frame without sewing


def estimate_minutes(stitches: int, jumps: int, colour_changes: int,
                     spm: float = MACHINE_SPM) -> tuple[float, float]:
    """(sewing minutes, thread-change minutes). The machine runs below top speed on
    average (starts, stops, long stitches), so 85% of `spm` is used."""
    sew = stitches / (spm * 0.85) + jumps * JUMP_SECONDS / 60
    return sew, colour_changes * THREAD_CHANGE_MIN


def format_minutes(m: float) -> str:
    if m < 1:
        return "under a minute"
    h, mm = divmod(round(m), 60)
    return f"{h} h {mm} min" if h else f"{mm} min"


def apply_color_choices(design: Design, overrides: dict[str, str] | None,
                        color_map: dict[str, str] | None, skip: list[str] | None,
                        fills: dict[str, str | None] | None = None,
                        no_outline: list[str] | None = None,
                        fill_spec: FillSpec | None = None,
                        satins: dict[str, float] | None = None) -> None:
    """Apply the operator's colour picks. Per-object overrides beat source-colour maps.
    fills maps an object to its fill colour (None = the object's own thread)."""
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
    for key, col in (fills or {}).items():
        o = find(key)
        if not can_fill(o.polylines):
            raise ConversionError(f"{o.obj_id} is not a closed shape, so it cannot be filled")
        o.fill = dataclasses.replace(fill_spec or FillSpec(),
                                     color=parse_color(col) if col else None)
    for key in no_outline or []:
        find(key).outline = False
    for key, width in (satins or {}).items():
        find(key).satin = SatinSpec(width=width)


def snap_colors(design: Design, snap: bool = True) -> dict[str, RGB]:
    """Replace every object's outline and fill colour with its nearest Brother
    thread (unless snap is False), so that colours which end up as the same
    thread are sewn as one colour block. Fills without their own colour take the
    object's. Returns the colours chosen before snapping, keyed by object id
    (outline) and "<id> fill"."""
    chosen = {}
    for o in design.objects:
        chosen[o.obj_id] = o.color
        if o.fill is not None:
            fc = o.fill.color or o.color
            chosen[f"{o.obj_id} fill"] = fc
            o.fill = dataclasses.replace(o.fill, color=nearest_brother(fc).rgb if snap else fc)
        if snap:
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
    here = None

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
            # trim long jumps, but not before the first stitch: a trim there would
            # sit at the origin and stretch the design's extent to include it
            if here is not None and math.dist(clean[0], here) / 10.0 > s.trim_dist:
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
                snap: bool = True,
                placed: bool = False,
                fills: dict[str, str | None] | None = None,
                no_outline: list[str] | None = None,
                fill_spec: FillSpec | None = None,
                satins: dict[str, float] | None = None) -> Plan:
    """Turn a loaded design into stitches without writing anything.
    The design passed in is not modified.

    placed=True means the objects are already positioned in hoop coordinates
    (millimetres, hoop centre at 0,0), as the GUI does: no scaling, centring or
    size check is applied and width_mm / fit are ignored. Check the placement
    with outside_hoop()."""
    s = settings or Settings()
    _check_inputs(hoop_mm, margin_mm, width_mm, s)
    design = copy.deepcopy(design)
    apply_color_choices(design, overrides, color_map, skip, fills, no_outline, fill_spec, satins)
    if not design.active():
        raise ConversionError("Nothing to stitch: " + "; ".join(design.warnings or ["all objects skipped"]))
    warnings = list(design.warnings)
    for o in design.active():
        if o.satin is not None and not 0.5 <= o.satin.width <= 8:
            raise ConversionError(f"Satin width must be 0.5-8 mm ({o.obj_id})")
        if o.fill is not None:
            if not 0.2 <= o.fill.spacing <= 3:
                raise ConversionError(f"Fill row spacing must be 0.2-3 mm ({o.obj_id})")
            if not 1 <= o.fill.stitch_len <= MAX_STITCH_LEN:
                raise ConversionError(f"Fill stitch length must be 1-{MAX_STITCH_LEN:g} mm ({o.obj_id})")
    chosen = snap_colors(design, snap)
    if not snap:
        warnings.append("Colours not snapped: the machine will show the nearest Brother "
                        "colour name for each thread")

    x0, y0, x1, y1 = design.bounds()
    w, h = x1 - x0, y1 - y0
    if w <= 0 and h <= 0:
        raise ConversionError("Nothing to stitch: the design has no size")
    scale = 1.0
    if not placed:
        scale = _size_and_centre(design, w, h, width_mm, fit, hoop_mm, margin_mm, warnings)

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
        ids = list(dict.fromkeys(r.label for r in blk.runs))
        # "<id> fill" has its own chosen colour; satin and imported stitches use the object's
        picked = list(dict.fromkeys(chosen[i] if i in chosen else chosen[i.rsplit(" ", 1)[0]]
                                    for i in ids))
        tmap.append(ThreadUse(ids, picked, thread_for_rgb(blk.color), blk.color))
    # stitches per colour block, from the finished pattern
    per_block, bi = [0] * len(blocks), 0
    for _, _, c in pattern.stitches:
        c &= 0xFF
        if c == pyembroidery.COLOR_CHANGE:
            bi += 1
        elif c == pyembroidery.STITCH and bi < len(per_block):
            per_block[bi] += 1
    for tu, n in zip(tmap, per_block):
        tu.stitches = n
    sew_min, change_min = estimate_minutes(stitches, jumps, len(blocks) - 1, s.machine_spm)
    report = Report("", stitches, jumps, trims, len(blocks),
                    (bx1 - bx0) / 10.0, (by1 - by0) / 10.0, scale, tmap, warnings,
                    sew_min, change_min)
    return Plan(pattern, report)


def _size_and_centre(design: Design, w: float, h: float, width_mm, fit, hoop_mm,
                     margin_mm, warnings: list[str]) -> float:
    """Scale (for width_mm / fit) and centre the design on the hoop. Returns the scale."""
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
    return scale


def outside_hoop(design: Design, hoop_mm: tuple[float, float], margin_mm: float) -> list[str]:
    """Ids of active objects that reach outside the sewing area (hoop minus margin),
    for a design placed in hoop coordinates (hoop centre at 0,0)."""
    hx, hy = hoop_mm[0] / 2 - margin_mm, hoop_mm[1] / 2 - margin_mm
    out = []
    for o in design.active():
        x0, y0, x1, y1 = o.bounds()
        if x0 < -hx - 1e-6 or x1 > hx + 1e-6 or y0 < -hy - 1e-6 or y1 > hy + 1e-6:
            out.append(o.obj_id)
    return out


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
            preview_path: str | None = None,
            fills: dict[str, str | None] | None = None,
            no_outline: list[str] | None = None,
            fill_spec: FillSpec | None = None,
            satins: dict[str, float] | None = None) -> Report:
    """Load an SVG, plan it and write a PES file (what the CLI uses)."""
    s = settings or Settings()
    _check_inputs(hoop_mm, margin_mm, width_mm, s)
    plan = plan_design(load_svg(svg_path), overrides=overrides, color_map=color_map, skip=skip,
                       width_mm=width_mm, fit=fit, hoop_mm=hoop_mm, margin_mm=margin_mm,
                       settings=s, snap=snap, fills=fills, no_outline=no_outline,
                       fill_spec=fill_spec, satins=satins)
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
