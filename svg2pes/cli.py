"""Command-line front end for milestone 1."""
from __future__ import annotations

import argparse
import sys

from .convert import ConversionError, Settings, convert, describe_objects
from .fill import FillSpec
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


def _hoop(text: str) -> tuple[float, float]:
    try:
        w, h = (float(v) for v in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"hoop size should look like 100x100, got {text!r}")
    return w, h


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="svg2pes", description="Convert simple SVG artwork to Brother PES embroidery files")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="show the objects (and their colours) found in an SVG")
    p_list.add_argument("svg")

    sub.add_parser("palette", help="show the Brother thread colours you can choose from")

    p_gui = sub.add_parser("gui", help="open the svg2pes window")
    p_gui.add_argument("svg", nargs="?", help="SVG to open straight away")

    p = sub.add_parser("convert", help="convert an SVG to PES")
    p.add_argument("svg")
    p.add_argument("-o", "--out", required=True, help="output .pes file")
    p.add_argument("--set", action="append", metavar="OBJECT=COLOUR",
                   help="choose the colour of one object (by id or number); repeatable")
    p.add_argument("--map", action="append", metavar="OLD=NEW",
                   help="change every object of one source colour; repeatable")
    p.add_argument("--skip", action="append", metavar="OBJECT", help="leave an object out; repeatable")
    p.add_argument("--fill", action="append", metavar="OBJECT[=COLOUR]",
                   help="fill a closed shape (in COLOUR, else its own thread); repeatable")
    p.add_argument("--no-outline", action="append", metavar="OBJECT",
                   help="don't sew this object's outline (e.g. a filled shape); repeatable")
    p.add_argument("--fill-angle", type=float, default=45.0, help="fill row angle in degrees (default 45)")
    p.add_argument("--fill-spacing", type=float, default=0.4,
                   help="mm between fill rows, smaller is denser (default 0.4)")
    p.add_argument("--fill-stitch", type=float, default=3.5, help="fill stitch length in mm (default 3.5)")
    p.add_argument("--width", type=float, metavar="MM", help="scale the design to this width in mm")
    p.add_argument("--fit", action="store_true", help="scale down to fit the hoop if too big")
    p.add_argument("--hoop", type=_hoop, default=(100.0, 100.0), help="hoop size in mm (default 100x100 for PE540D)")
    p.add_argument("--margin", type=float, default=2.0, help="safety margin in mm (default 2)")
    p.add_argument("--stitch-len", type=float, default=2.5, help="run stitch length in mm (default 2.5)")
    p.add_argument("--keep-order", action="store_true", help="keep SVG paint order (more colour changes)")
    p.add_argument("--pes-version", type=int, choices=(1, 6), default=1, help="PES version (default 1)")
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

        rep = convert(
            a.svg, a.out,
            overrides=_pairs(a.set, "--set"), color_map=_pairs(a.map, "--map"), skip=a.skip,
            width_mm=a.width, fit=a.fit, hoop_mm=a.hoop, margin_mm=a.margin,
            settings=Settings(stitch_len=a.stitch_len, keep_order=a.keep_order),
            pes_version=a.pes_version, snap=not a.no_snap, preview_path=a.preview,
            fills=_fills(a.fill), no_outline=a.no_outline,
            fill_spec=FillSpec(angle=a.fill_angle, spacing=a.fill_spacing, stitch_len=a.fill_stitch))
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
    for i, tu in enumerate(rep.thread_map, 1):
        picked = ", ".join(to_hex(c) for c in tu.chosen)
        what = f"Brother {tu.thread.number} {tu.thread.name}" if tu.thread else f"custom {to_hex(tu.rgb)}"
        print(f"  colour {i}: {what:<28} from {picked}   [{', '.join(tu.object_ids)}]")
    for w in sorted(set(rep.warnings)):
        print("warning:", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
