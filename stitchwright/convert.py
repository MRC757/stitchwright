"""Top-level SVG -> PES conversion used by both the CLI and (later) the GUI."""
from __future__ import annotations

import copy
import dataclasses
import math
import os
from dataclasses import dataclass, field

import pyembroidery
from pyembroidery import EmbPattern, EmbThread

from .fill import FILL_PATTERNS, FillSpec, SatinSpec, can_fill
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
    bounds_mm: tuple = (0.0, 0.0, 0.0, 0.0)          # where the stitches reach (mm)
    object_bounds: dict = field(default_factory=dict)  # obj_id -> stitch extent (mm)

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


@dataclass
class Choices:
    """The operator's per-object choices, by object id (or number from `list`).
    Colours are text: '#rrggbb', a chart name, 'brother:<n>' or 'chart:<row>'."""
    overrides: dict[str, str] = field(default_factory=dict)      # object -> thread colour
    color_map: dict[str, str] = field(default_factory=dict)      # SVG colour -> thread colour
    skip: list[str] = field(default_factory=list)                # objects left out
    fills: dict[str, str | None] = field(default_factory=dict)   # object -> fill colour (None: own)
    no_outline: list[str] = field(default_factory=list)          # objects sewn without outline
    satins: dict = field(default_factory=dict)   # object -> satin width (mm), or a SatinSpec
                                                 # for any outline style (bean, zigzag, ...)
    fill_spec: FillSpec = field(default_factory=FillSpec)        # angle, density, ... for fills


def _choices(choices: Choices | None, **legacy) -> Choices:
    """A Choices, with the old separate keyword arguments (still accepted) folded in."""
    c = copy.deepcopy(choices) if choices is not None else Choices()
    for key, value in legacy.items():
        if value is not None:
            setattr(c, key, value)
    return c


_LEGACY = {"overrides", "color_map", "skip", "fills", "no_outline", "satins", "fill_spec"}


def _legacy(kwargs: dict) -> dict:
    """The old per-object keyword arguments (overrides=, fills=, ...), checked."""
    unknown = set(kwargs) - _LEGACY
    if unknown:
        raise TypeError(f"unexpected keyword argument(s): {', '.join(sorted(unknown))}")
    return kwargs


def apply_color_choices(design: Design, choices: Choices) -> None:
    """Apply the operator's choices to the design's objects. Per-object colour
    overrides beat source-colour maps."""
    overrides, color_map, skip = choices.overrides, choices.color_map, choices.skip
    fills, no_outline, fill_spec, satins = (choices.fills, choices.no_outline,
                                            choices.fill_spec, choices.satins)
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
        find(key).satin = width if isinstance(width, SatinSpec) else SatinSpec(width=width)


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
    if s.machine_spm < 1:
        raise ConversionError(f"Machine speed must be at least 1 stitch per minute, "
                              f"got {s.machine_spm:g}")


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


def plan_design(design: Design, choices: Choices | None = None, *,
                width_mm: float | None = None,
                fit: bool = False,
                hoop_mm: tuple[float, float] = (100.0, 100.0),
                margin_mm: float = 2.0,
                settings: Settings | None = None,
                snap: bool = True,
                placed: bool = False,
                _attempt: int = 0,
                **legacy) -> Plan:
    """Turn a loaded design into stitches without writing anything.
    The design passed in is not modified.

    placed=True means the objects are already positioned in hoop coordinates
    (millimetres, hoop centre at 0,0), as the GUI does: no scaling, centring or
    size check is applied and width_mm / fit are ignored. Check the placement
    with outside_hoop()."""
    s = settings or Settings()
    _check_inputs(hoop_mm, margin_mm, width_mm, s)
    choices = _choices(choices, **_legacy(legacy))
    original = design
    design = copy.deepcopy(design)
    apply_color_choices(design, choices)
    if not design.active():
        raise ConversionError("Nothing to stitch: " + "; ".join(design.warnings or ["all objects skipped"]))
    warnings = list(design.warnings)
    for o in design.active():
        if o.satin is not None and not 0.5 <= o.satin.width <= 8:
            raise ConversionError(f"{o.satin.style.capitalize()} width must be 0.5-8 mm "
                                  f"({o.obj_id})")
        if o.satin is not None and not 0.2 <= o.satin.spacing <= 6:
            raise ConversionError(f"{o.satin.style.capitalize()} spacing must be 0.2-6 mm "
                                  f"({o.obj_id})")
        if o.fill is not None:
            if not 0.2 <= o.fill.spacing <= 3:
                raise ConversionError(f"Fill row spacing must be 0.2-3 mm ({o.obj_id})")
            if not 1 <= o.fill.stitch_len <= MAX_STITCH_LEN:
                raise ConversionError(f"Fill stitch length must be 1-{MAX_STITCH_LEN:g} mm ({o.obj_id})")
            if not 0 <= o.fill.pull_comp <= 1:
                raise ConversionError(f"Pull compensation must be 0-1 mm ({o.obj_id})")
            if o.fill.pattern not in FILL_PATTERNS:
                raise ConversionError(f"Unknown fill pattern {o.fill.pattern!r} ({o.obj_id})")
            if not 2 <= o.fill.pattern_size <= 20:
                raise ConversionError(f"Pattern size must be 2-20 mm ({o.obj_id})")
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
    warnings += _long_imported_stitches(blocks)

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
                    sew_min, change_min, (bx0 / 10.0, by0 / 10.0, bx1 / 10.0, by1 / 10.0),
                    _object_bounds(blocks))

    if not placed and _stitches_outside(report.bounds_mm, hoop_mm, margin_mm):
        # Satin width and pull compensation reach past the outlines the sizing used.
        if fit and _attempt < 3:
            sw, sh = bx1 - bx0, by1 - by0
            aw, ah = (hoop_mm[0] - 2 * margin_mm) * 10, (hoop_mm[1] - 2 * margin_mm) * 10
            r = min(aw / sw if sw else 1.0, ah / sh if sh else 1.0) * 0.995
            return plan_design(original, choices, width_mm=w * scale * r, fit=True,
                               hoop_mm=hoop_mm, margin_mm=margin_mm, settings=settings,
                               snap=snap, _attempt=_attempt + 1)
        x0, y0, x1, y1 = report.bounds_mm
        raise ConversionError(
            f"The stitches reach {x1 - x0:.1f} x {y1 - y0:.1f} mm (satin and fill edges go a "
            f"little past the outlines), more than the usable area of "
            f"{hoop_mm[0] - 2 * margin_mm:.0f} x {hoop_mm[1] - 2 * margin_mm:.0f} mm. "
            f"Turn on shrink-to-fit (--fit) or set a smaller width (--width).")
    return Plan(pattern, report)


def _long_imported_stitches(blocks) -> list[str]:
    """A warning if imported stitches (sewn exactly as given, never split) include any
    longer than the machine sews well: a file can have them, or resizing makes them."""
    longest, where = 0.0, ""
    for blk in blocks:
        for run in blk.runs:
            if run.kind != "stitches":
                continue
            for a, b in zip(run.points, run.points[1:]):
                d = math.dist(a, b)
                if d > longest:
                    longest, where = d, run.object_id
    if longest <= MAX_STITCH_LEN + STITCH_TOLERANCE_MM:
        return []
    return [f"Imported stitches in {where} are up to {longest:.1f} mm long; stitches over "
            f"{MAX_STITCH_LEN:g} mm can snag or sew loose. Make the object smaller, or check "
            f"it on a test piece."]


STITCH_TOLERANCE_MM = 0.1      # needle positions are stored to the nearest 0.1 mm


def _object_bounds(blocks) -> dict:
    """Each object's stitch extent (mm), from its planned runs: includes satin width,
    pull compensation and tie stitches, which the outline alone does not."""
    out: dict[str, list[float]] = {}
    for blk in blocks:
        for run in blk.runs:
            xs = [p[0] for p in run.points]
            ys = [p[1] for p in run.points]
            b = out.setdefault(run.object_id, [min(xs), min(ys), max(xs), max(ys)])
            b[0], b[1] = min(b[0], min(xs)), min(b[1], min(ys))
            b[2], b[3] = max(b[2], max(xs)), max(b[3], max(ys))
    return {k: tuple(float(v) for v in b) for k, b in out.items()}


def _stitches_outside(b, hoop_mm, margin_mm) -> bool:
    hx, hy = hoop_mm[0] / 2 - margin_mm, hoop_mm[1] / 2 - margin_mm
    # the CLI centres on the outlines, so compare the stitch extent with the area's size
    return (b[2] - b[0]) > 2 * hx + STITCH_TOLERANCE_MM or (b[3] - b[1]) > 2 * hy + STITCH_TOLERANCE_MM


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
    # 0.01 mm slack: svgelements' px-per-mm factor is rounded, so 96 mm can load as 96.00005
    if w * scale > avail_w + 0.01 or h * scale > avail_h + 0.01:
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


def outside_hoop(design: Design, hoop_mm: tuple[float, float], margin_mm: float,
                 report: Report | None = None) -> list[str]:
    """Ids of active objects that reach outside the sewing area (hoop minus margin),
    for a design placed in hoop coordinates (hoop centre at 0,0). With the plan's
    report, an object also counts if its *stitches* reach outside: a satin column or
    a fill's pull compensation goes a little past the outline."""
    hx, hy = hoop_mm[0] / 2 - margin_mm, hoop_mm[1] / 2 - margin_mm

    def out_of(b, tol):
        return b[0] < -hx - tol or b[2] > hx + tol or b[1] < -hy - tol or b[3] > hy + tol

    stitched = report.object_bounds if report is not None else {}
    out = []
    for o in design.active():
        # object extents come from the planned (unrounded) needle points: no rounding slack
        if out_of(o.bounds(), 1e-6) or (o.obj_id in stitched and out_of(stitched[o.obj_id], 1e-6)):
            out.append(o.obj_id)
    return out


# Embroidery files the window and the command line can write: extension -> (name, whether
# the format stores thread colours). DST and EXP store only the colour stops; the machine
# asks for each thread. JEF stores the nearest Janome threads. Only PES has been sewn here.
MACHINE_FORMATS = {
    ".pes": ("Brother PES", True),
    ".dst": ("Tajima DST (most machines)", False),
    ".exp": ("Melco / Bernina EXP", False),
    ".jef": ("Janome JEF", True),
    ".vp3": ("Pfaff / Husqvarna Viking VP3", True),
    ".xxx": ("Singer XXX", True),
}


def machine_pattern(pattern: EmbPattern, origin: str = "corner") -> EmbPattern:
    """A copy of the pattern moved for writing to a machine file, so where the design
    sits on the canvas doesn't matter; the machine places the design itself.

    origin "corner": its top-left corner at (0, 0), where the PEC stitch data starts, as
    Brother's own software writes PES files. Written as planned, a design placed
    off-centre on the canvas started from a point outside itself, and the PE540D sewed
    part of it squashed against the edge of its sewing area.
    origin "centre": its centre at (0, 0), as DST, EXP, JEF and the other formats expect
    (their headers give the design's reach either side of the starting point)."""
    x0, y0, x1, y1 = pattern.bounds()
    if origin == "centre":
        x0, y0 = (x0 + x1) // 2, (y0 + y1) // 2
    out = EmbPattern()
    out.stitches = [[x - x0, y - y0, c] for x, y, c in pattern.stitches]
    out.threadlist = list(pattern.threadlist)
    out.extras = dict(pattern.extras)
    return out


def write_pes_file(plan: Plan, out_path: str, pes_version: int = 1) -> Report:
    """Write the plan as PES, with the design's top-left corner at the stitch origin
    (see machine_pattern). The file is written to a temporary name and read back
    before it replaces out_path, so a failure never leaves a broken file."""
    tmp = out_path + ".part"
    try:
        pyembroidery.write_pes(machine_pattern(plan.pattern), tmp, {"version": float(pes_version)})
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


def write_machine_file(plan: Plan, out_path: str, pes_version: int = 1) -> Report:
    """Write the plan in the format its extension names (see MACHINE_FORMATS). Like
    write_pes_file, it writes a temporary file and checks it by reading it back."""
    ext = os.path.splitext(out_path)[1].lower()
    if ext not in MACHINE_FORMATS:
        names = ", ".join(e.lstrip(".").upper() for e in MACHINE_FORMATS)
        raise ConversionError(f"Can't write {ext.lstrip('.').upper() or 'a file without an extension'}"
                              f" files. Use one of: {names}.")
    if ext == ".pes":
        return write_pes_file(plan, out_path, pes_version)
    name, keeps_colours = MACHINE_FORMATS[ext]
    tmp = os.path.splitext(out_path)[0] + ".part" + ext       # pyembroidery goes by the extension
    try:
        pyembroidery.write(machine_pattern(plan.pattern, "centre"), tmp)
        back = pyembroidery.read(tmp)
        if back is None or not back.stitches:
            raise ConversionError(f"Written {name} file could not be read back")
        stops = back.count_color_changes() + 1
        if stops != plan.report.color_blocks:
            raise ConversionError(f"Written {name} file has {stops} colour stops, "
                                  f"expected {plan.report.color_blocks}")
        if keeps_colours and len(back.threadlist) != plan.report.color_blocks:
            raise ConversionError(f"Written {name} file has {len(back.threadlist)} colours, "
                                  f"expected {plan.report.color_blocks}")
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return dataclasses.replace(plan.report, out_path=out_path)


def convert(svg_path: str, out_path: str, choices: Choices | None = None, *,
            width_mm: float | None = None,
            fit: bool = False,
            hoop_mm: tuple[float, float] = (100.0, 100.0),
            margin_mm: float = 2.0,
            settings: Settings | None = None,
            pes_version: int = 1,
            snap: bool = True,
            preview_path: str | None = None,
            svg_dpi: float | None = None,
            **legacy) -> Report:
    """Load an SVG, plan it and write an embroidery file, in the format out_path's
    extension names (what the CLI uses)."""
    s = settings or Settings()
    _check_inputs(hoop_mm, margin_mm, width_mm, s)
    design = load_svg(svg_path)
    if svg_dpi:                       # e.g. 72 for Illustrator exports: SVG assumes 96
        _scale_design(design, 96.0 / svg_dpi)
        design.warnings = [w for w in design.warnings if "72 dpi" not in w]
    plan = plan_design(design, _choices(choices, **_legacy(legacy)), width_mm=width_mm,
                       fit=fit, hoop_mm=hoop_mm, margin_mm=margin_mm, settings=s, snap=snap)
    report = write_machine_file(plan, out_path, pes_version)
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
