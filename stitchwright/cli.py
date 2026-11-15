"""Command-line front end for milestone 1."""
from __future__ import annotations

import argparse
import sys

from .convert import (Choices, ConversionError, Settings, convert, describe_objects,
                      format_minutes)
from .fill import FILL_PATTERNS, OUTLINE_STYLES, FillSpec, SatinSpec, outline_spec
from .palette import brother_threads, to_hex
from .svg_import import load_svg


def _pairs(items: list[str] | None, what: str) -> dict[str, str]:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"{what} expects NAME=COLOUR, got {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _fills(items: list[str] | None) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for it in items or []:
        k, _, v = it.partition("=")
        out[k.strip()] = v.strip() or None
    return out


def _satins(items: list[str] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for it in items or []:
        k, _, v = it.partition("=")
        try:
            out[k.strip()] = float(v) if v.strip() else 2.0
        except ValueError:
            raise SystemExit(f"--satin expects OBJECT or OBJECT=WIDTH, got {it!r}")
    return out


def _outlines(items: list[str] | None) -> dict[str, SatinSpec]:
    """--outline OBJECT=STYLE[:WIDTH] items."""
    out: dict[str, SatinSpec] = {}
    styles = ", ".join(OUTLINE_STYLES)
    for it in items or []:
        k, _, v = it.partition("=")
        style, _, width = v.strip().lower().partition(":")
        if not k.strip() or style not in OUTLINE_STYLES:
            raise SystemExit(f"--outline expects OBJECT=STYLE or OBJECT=STYLE:WIDTH, with STYLE "
                             f"one of {styles}; got {it!r}")
        try:
            out[k.strip()] = outline_spec(style, float(width) if width.strip() else None)
        except ValueError:
            raise SystemExit(f"--outline: the width must be a number, got {it!r}")
    return out


def _fill_angle(a) -> float:
    """The row angle: as given, else 45 degrees (0 for diamonds and waves, which are
    drawn across the rows and stand upright with rows at 0)."""
    if a.fill_angle is not None:
        return a.fill_angle
    return 0.0 if a.fill_pattern in ("diamond", "wave") else 45.0


def _hoop(text: str) -> tuple[float, float]:
    try:
        w, h = (float(v) for v in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"hoop size should look like 100x100, got {text!r}")
    return w, h


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stitchwright", description="Stitchwright: turn artwork into Brother PES embroidery files "
                                 "(or DST, EXP, JEF, VP3 and XXX for other machines)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="show the objects (and their colours) found in an SVG")
    p_list.add_argument("svg")

    sub.add_parser("palette", help="show the Brother thread colours you can choose from")

    p_gui = sub.add_parser("gui", help="open the Stitchwright window")
    p_gui.add_argument("svg", nargs="?", help="SVG to open straight away")

    p = sub.add_parser("convert", help="convert an SVG to PES (or DST, EXP, JEF, VP3, XXX)")
    p.add_argument("svg")
    p.add_argument("-o", "--out", required=True,
                   help="output file; its extension picks the format: .pes (Brother), .dst, .exp, "
                        ".jef, .vp3 or .xxx")
    p.add_argument("--set", action="append", metavar="OBJECT=COLOUR",
                   help="choose the colour of one object (by id or number); repeatable")
    p.add_argument("--map", action="append", metavar="OLD=NEW",
                   help="change every object of one source colour; repeatable")
    p.add_argument("--skip", action="append", metavar="OBJECT", help="leave an object out; repeatable")
    p.add_argument("--fill", action="append", metavar="OBJECT[=COLOUR]",
                   help="fill a closed shape (in COLOUR, else its own thread); repeatable")
    p.add_argument("--no-outline", action="append", metavar="OBJECT",
                   help="don't sew this object's outline (e.g. a filled shape); repeatable")
    p.add_argument("--fill-angle", type=float, default=None,
                   help="fill row angle in degrees (default 45; 0 for diamond and wave patterns)")
    p.add_argument("--fill-pattern", choices=list(FILL_PATTERNS), default="rows",
                   help="fill pattern: rows (default), contour (follows the outline), diamond, "
                        "wave or crosshatch")
    p.add_argument("--pattern-size", type=float, default=5.0,
                   help="size of the diamonds or waves in mm (default 5)")
    p.add_argument("--fill-spacing", type=float, default=0.2,
                   help="mm between fill rows, smaller is denser (default 0.2)")
    p.add_argument("--fill-stitch", type=float, default=3.5, help="fill stitch length in mm (default 3.5)")
    p.add_argument("--no-underlay", action="store_true", help="fills without underlay")
    p.add_argument("--pull-comp", type=float, default=0.2,
                   help="mm added to each end of fill rows (default 0.2)")
    p.add_argument("--no-cutout", action="store_true",
                   help="don't cut fills out under filled shapes stacked above")
    p.add_argument("--satin", action="append", metavar="OBJECT[=WIDTH]",
                   help="sew this outline as a satin column, WIDTH mm (default 2); repeatable")
    p.add_argument("--outline", action="append", metavar="OBJECT=STYLE[:WIDTH]",
                   help="sew this outline as bean, satin, zigzag or blanket stitch (WIDTH: the "
                        "column width or blanket prong length in mm); repeatable")
    p.add_argument("--svg-dpi", type=float, metavar="DPI",
                   help="the SVG's own px per inch, e.g. 72 for Illustrator exports (default 96)")
    p.add_argument("--speed", type=int, default=650,
                   help="machine speed in stitches per minute, for the time estimate (default 650)")
    p.add_argument("--width", type=float, metavar="MM", help="scale the design to this width in mm")
    p.add_argument("--fit", action="store_true", help="scale down to fit the hoop if too big")
    p.add_argument("--hoop", type=_hoop, default=(100.0, 100.0), help="hoop size in mm (default 100x100 for PE540D)")
    p.add_argument("--margin", type=float, default=2.0, help="safety margin in mm (default 2)")
    p.add_argument("--stitch-len", type=float, default=2.5, help="run stitch length in mm (default 2.5)")
    p.add_argument("--keep-order", action="store_true", help="keep SVG paint order (more colour changes)")
    p.add_argument("--pes-version", type=int, choices=(1, 6), default=1, help="PES version (default 1; PES files only)")
    p.add_argument("--no-snap", action="store_true", help="do not snap colours to the Brother palette")
    p.add_argument("--preview", metavar="PNG", help="also write a PNG preview of the stitches")

    a = ap.parse_args(argv)

    if a.cmd == "gui":
        from .gui import main as gui_main
        return gui_main([a.svg] if a.svg else [])

    if a.cmd == "palette":
        print("chart  thread  colour   name")
        for t in brother_threads():
            print(f"{t.chart:>5}  {t.number:>6}  {to_hex(t.rgb)}  {t.name}")
        print("Choose by name, brother:<thread number> or chart:<row>, e.g. brother:900 or chart:1")
        return 0

    try:
        if a.cmd == "list":
            d = load_svg(a.svg)
            print("  #  id               kind      colour   size             nearest thread")
            print("\n".join(describe_objects(d)))
            for w in sorted(set(d.warnings)):
                print("warning:", w)
            return 0

        choices = Choices(
            overrides=_pairs(a.set, "--set"), color_map=_pairs(a.map, "--map"),
            skip=a.skip or [], fills=_fills(a.fill), no_outline=a.no_outline or [],
            satins={**_satins(a.satin), **_outlines(a.outline)},
            fill_spec=FillSpec(angle=_fill_angle(a), spacing=a.fill_spacing,
                               stitch_len=a.fill_stitch, underlay=not a.no_underlay,
                               pull_comp=a.pull_comp, pattern=a.fill_pattern,
                               pattern_size=a.pattern_size))
        rep = convert(
            a.svg, a.out, choices,
            width_mm=a.width, fit=a.fit, hoop_mm=a.hoop, margin_mm=a.margin,
            settings=Settings(stitch_len=a.stitch_len, keep_order=a.keep_order,
                              knockout=not a.no_cutout, machine_spm=a.speed),
            pes_version=a.pes_version, snap=not a.no_snap, preview_path=a.preview,
            svg_dpi=a.svg_dpi)
    except OSError as e:
        print(f"error: {e.strerror or e}: {e.filename or ''}".rstrip(": "), file=sys.stderr)
        return 2
    except (ConversionError, ValueError) as e:
        print("error:", e, file=sys.stderr)
        return 2

    print(f"Wrote {rep.out_path}")
    print(f"  {rep.stitch_count} stitches, {rep.jump_count} jumps, {rep.trim_count} trims, "
          f"{rep.color_blocks} colour blocks")
    print(f"  size {rep.width_mm:.1f} x {rep.height_mm:.1f} mm (scale {rep.scale:.2f})")
    print(f"  time about {format_minutes(rep.total_minutes)} "
          f"({format_minutes(rep.sew_minutes)} sewing + {format_minutes(rep.change_minutes)} "
          f"for thread changes, at {a.speed} stitches/min)")
    for i, tu in enumerate(rep.thread_map, 1):
        picked = ", ".join(to_hex(c) for c in tu.chosen)
        what = f"Brother {tu.thread.number} {tu.thread.name}" if tu.thread else f"custom {to_hex(tu.rgb)}"
        print(f"  colour {i}: {what:<28} {tu.stitches:>6} stitches  from {picked}   "
              f"[{', '.join(tu.object_ids)}]")
    for w in sorted(set(rep.warnings)):
        print("warning:", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
