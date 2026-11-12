import math
import os
import sys

import pyembroidery
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from svg2pes.cli import main  # noqa: E402
from svg2pes.convert import ConversionError, convert, plan_design, write_pes_file  # noqa: E402
from svg2pes.palette import brother_threads, nearest_brother, parse_color  # noqa: E402
from svg2pes.stitching import Run, Settings, resample, stitch_points  # noqa: E402
from svg2pes.svg_import import load_svg  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "samples", "sample.svg")
RED = (0xED, 0x17, 0x1F)


def write_svg(tmp_path, body, w=60, h=40, name="t.svg"):
    p = tmp_path / name
    p.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" '
                 f'viewBox="0 0 {w} {h}">{body}</svg>')
    return str(p)


def stitch_lengths(pattern):
    """Lengths (mm) of every needle-to-needle stitch, skipping zero-length ones."""
    out, last = [], None
    for x, y, c in pattern.stitches:
        c &= 0xFF
        if c == pyembroidery.STITCH:
            if last is not None and (x, y) != last:
                out.append(math.dist((x, y), last) / 10)
            last = (x, y)
        elif c in (pyembroidery.JUMP, pyembroidery.TRIM, pyembroidery.COLOR_CHANGE):
            last = None if c != pyembroidery.JUMP else (x, y)
    return out


# --- SVG import ------------------------------------------------------------

def test_reads_objects_in_mm():
    d = load_svg(SAMPLE)
    ids = [o.obj_id for o in d.objects]
    assert ids == ["badge", "sun", "letterF", "star", "wave"]
    x0, y0, x1, y1 = d.bounds()
    assert (x1 - x0) == pytest.approx(56.0, abs=0.05)
    assert (y1 - y0) == pytest.approx(36.0, abs=0.05)


def test_multi_subpath_strokes_stay_separate():
    """The 'F' path has three strokes; each must start at its own M point."""
    d = load_svg(SAMPLE)
    f = next(o for o in d.objects if o.obj_id == "letterF")
    assert len(f.polylines) == 3
    got = [tuple(round(c, 1) for c in (pl.points[0] + pl.points[-1])) for pl in f.polylines]
    assert got == [(10.0, 10.0, 22.0, 10.0), (10.0, 10.0, 10.0, 30.0), (10.0, 20.0, 19.0, 20.0)]


def test_generated_ids_never_clash_with_real_ids(tmp_path):
    svg = write_svg(tmp_path,
                    '<rect id="a" x="1" y="1" width="5" height="5" stroke="#000" fill="none"/>'
                    '<rect id="a" x="10" y="1" width="5" height="5" stroke="#000" fill="none"/>'
                    '<rect id="a_2" x="20" y="1" width="5" height="5" stroke="#000" fill="none"/>'
                    '<rect x="30" y="1" width="5" height="5" stroke="#000" fill="none"/>'
                    '<rect id="obj4" x="40" y="1" width="5" height="5" stroke="#000" fill="none"/>')
    ids = [o.obj_id for o in load_svg(svg).objects]
    assert len(set(ids)) == len(ids)
    assert ids[0] == "a" and ids[2] == "a_2" and ids[4] == "obj4"


def test_bad_svg_is_a_clear_error(tmp_path):
    p = tmp_path / "bad.svg"
    p.write_text("<svg><rect")
    with pytest.raises(ValueError):
        load_svg(str(p))


# --- palette (Thread Colors.pdf) --------------------------------------------

def test_palette_is_the_61_chart_colours():
    threads = brother_threads()
    assert len(threads) == 61
    assert [t.chart for t in threads] == list(range(1, 62))
    assert len({t.pec_index for t in threads}) == 61
    assert len({t.rgb for t in threads}) == 61
    assert len({t.number for t in threads}) == 61
    assert threads[0].name == "Black" and threads[0].number == "900"
    assert threads[24].name == "Linen" and threads[24].number == "307"
    assert threads[60].name == "Gray" and threads[60].number == "817"


def test_color_parsing():
    assert parse_color("#fff") == (255, 255, 255)
    assert parse_color("brother:800") == RED
    assert parse_color("chart:3") == RED
    assert parse_color("Red") == RED
    assert parse_color("brother:5") == parse_color("brother:005") == parse_color("Silver")
    assert parse_color("Ultramarine") == parse_color("Ultra Marine")
    for bad in ("not-a-colour", "brother:999", "chart:62", "brother:x"):
        with pytest.raises(ValueError):
            parse_color(bad)


def test_snapped_threads_land_on_the_right_pec_slot(tmp_path):
    """The machine shows the name for the PEC index, so each chart colour must
    be written to its own PEC slot."""
    body = "".join(
        f'<rect x="{1 + (i % 8) * 7}" y="{1 + (i // 8) * 7}" width="5" height="5" '
        f'fill="none" stroke="#{t.rgb[0]:02x}{t.rgb[1]:02x}{t.rgb[2]:02x}"/>'
        for i, t in enumerate(brother_threads()[:30]))
    out = str(tmp_path / "o.pes")
    rep = convert(write_svg(tmp_path, body, 60, 60), out, fit=True)
    assert [tu.thread.chart for tu in rep.thread_map] == list(range(1, 31))
    with open(out, "rb") as f:
        data = f.read()
    pec = int.from_bytes(data[8:12], "little")
    count = data[pec + 48] + 1
    assert list(data[pec + 49:pec + 49 + count]) == [t.pec_index for t in brother_threads()[:30]]


# --- conversion -------------------------------------------------------------

def test_convert_roundtrip_and_colors(tmp_path):
    out = str(tmp_path / "o.pes")
    rep = convert(SAMPLE, out, overrides={"sun": "brother:900"}, skip=["wave"])
    back = pyembroidery.read_pes(out)
    assert back is not None
    assert len(back.threadlist) == rep.color_blocks == 4
    bx0, by0, bx1, by1 = back.bounds()
    assert (bx1 - bx0) / 10 == pytest.approx(56.0, abs=0.5)
    assert abs(bx0 + bx1) < 20 and abs(by0 + by1) < 20
    assert not os.path.exists(out + ".part")


def test_operator_color_pick_changes_thread(tmp_path):
    out = str(tmp_path / "o.pes")
    rep = convert(SAMPLE, out, overrides={"badge": "#ff0000"}, snap=False)
    assert (255, 0, 0) in {tu.rgb for tu in rep.thread_map}


def test_colours_that_snap_to_one_thread_share_a_block(tmp_path):
    svg = write_svg(tmp_path,
                    '<rect id="a" x="2" y="2" width="10" height="10" fill="none" stroke="#ff0000"/>'
                    '<rect id="b" x="20" y="2" width="10" height="10" fill="none" stroke="#ee1111"/>'
                    '<rect id="c" x="2" y="20" width="10" height="10" fill="none" stroke="#ff0000"/>')
    for keep in (False, True):
        rep = convert(svg, str(tmp_path / "o.pes"), settings=Settings(keep_order=keep))
        assert rep.color_blocks == 1
        tu = rep.thread_map[0]
        assert tu.thread.name == "Red" and sorted(tu.object_ids) == ["a", "b", "c"]
        assert set(tu.chosen) == {(255, 0, 0), (0xEE, 0x11, 0x11)}


def test_keep_order_lists_each_blocks_own_objects(tmp_path):
    svg = write_svg(tmp_path,
                    '<rect id="a" x="2" y="2" width="10" height="10" fill="none" stroke="#ff0000"/>'
                    '<rect id="b" x="20" y="2" width="10" height="10" fill="none" stroke="#0000ff"/>'
                    '<rect id="c" x="2" y="20" width="10" height="10" fill="none" stroke="#ff0000"/>')
    rep = convert(svg, str(tmp_path / "o.pes"), settings=Settings(keep_order=True))
    assert [tu.object_ids for tu in rep.thread_map] == [["a"], ["b"], ["c"]]


def test_too_big_is_refused_unless_fit(tmp_path):
    out = str(tmp_path / "o.pes")
    with pytest.raises(ConversionError):
        convert(SAMPLE, out, width_mm=150)
    assert not os.path.exists(out)
    rep = convert(SAMPLE, out, width_mm=150, fit=True)
    assert rep.width_mm <= 96.5


def test_straight_lines_can_be_fitted(tmp_path):
    out = str(tmp_path / "o.pes")
    h = write_svg(tmp_path, '<line x1="0" y1="5" x2="150" y2="5" stroke="#000"/>', 150, 10)
    assert convert(h, out, fit=True).width_mm == pytest.approx(96, abs=0.2)
    v = write_svg(tmp_path, '<line x1="5" y1="0" x2="5" y2="150" stroke="#000"/>', 10, 150, "v.svg")
    assert convert(v, out, fit=True).height_mm == pytest.approx(96, abs=0.2)
    with pytest.raises(ConversionError):
        convert(v, out, width_mm=20)


@pytest.mark.parametrize("kw", [
    {"settings": Settings(stitch_len=0)},
    {"settings": Settings(stitch_len=-2)},
    {"settings": Settings(stitch_len=20)},
    {"width_mm": 0},
    {"width_mm": -40},
    {"margin_mm": -1},
    {"margin_mm": 60},
    {"hoop_mm": (0, 100)},
])
def test_bad_numbers_are_refused(tmp_path, kw):
    with pytest.raises(ConversionError):
        convert(SAMPLE, str(tmp_path / "o.pes"), **kw)


def test_unknown_object_reports_valid_ids(tmp_path):
    with pytest.raises(ConversionError) as e:
        convert(SAMPLE, str(tmp_path / "o.pes"), overrides={"nope": "#000"})
    assert "badge" in str(e.value)


def test_numeric_id_that_shadows_an_object_number_is_ambiguous(tmp_path):
    svg = write_svg(tmp_path,
                    '<rect id="x" x="2" y="2" width="10" height="10" fill="none" stroke="#000"/>'
                    '<rect id="1" x="20" y="2" width="10" height="10" fill="none" stroke="#000"/>')
    with pytest.raises(ConversionError, match="ambiguous"):
        convert(svg, str(tmp_path / "o.pes"), overrides={"1": "Red"})


def test_pes_version_6_also_reads_back(tmp_path):
    out = str(tmp_path / "o6.pes")
    convert(SAMPLE, out, pes_version=6)
    assert pyembroidery.read_pes(out).stitches


def test_plan_then_write_leaves_design_untouched(tmp_path):
    """The GUI plans many times from one loaded design, then writes once."""
    d = load_svg(SAMPLE)
    before = [(o.color, o.skipped, [list(pl.points) for pl in o.polylines]) for o in d.objects]
    plan = plan_design(d, overrides={"sun": "Red"}, skip=["wave"], width_mm=40)
    assert [(o.color, o.skipped, [list(pl.points) for pl in o.polylines])
            for o in d.objects] == before
    assert plan.report.out_path == "" and plan.report.color_blocks == 3
    out = str(tmp_path / "g.pes")
    rep = write_pes_file(plan, out)
    assert rep.out_path == out and len(pyembroidery.read_pes(out).threadlist) == 3
    write_pes_file(plan, out, pes_version=6)       # a plan can be saved again


# --- stitching --------------------------------------------------------------

def test_no_stitch_is_too_short_or_too_long(tmp_path):
    svg = write_svg(tmp_path,
                    '<circle cx="10" cy="10" r="0.6" fill="none" stroke="#f00"/>'
                    '<circle cx="30" cy="10" r="8" fill="none" stroke="#00f"/>'
                    '<path d="M2 30 l0.3 0.2 l0.3 -0.2 l0.3 0.2 l0.3 -0.2 L40 30" fill="none" stroke="#0f0"/>')
    out = str(tmp_path / "o.pes")
    convert(svg, out)
    lengths = stitch_lengths(pyembroidery.read_pes(out))
    assert min(lengths) >= 0.3          # 0.4 mm minimum, less 0.1 mm rounding
    assert max(lengths) <= 2.5 + 0.15


def test_resample_keeps_ends_and_spacing():
    import numpy as np
    pts = resample(np.array([(0, 0), (10, 0)], float), 2.5, 35, 0.4)
    assert pts[0] == (0, 0) and pts[-1] == pytest.approx((10, 0))
    assert all(math.dist(a, b) == pytest.approx(2.5) for a, b in zip(pts, pts[1:]))


def test_tie_stitches_lock_both_ends():
    run = Run([(0.0, 0.0), (10.0, 0.0)], False, "x")
    pts = stitch_points(run, Settings(lock_len=0.5, lock_passes=2))
    assert pts[:4] == [(0, 0), (0.5, 0), (0, 0), (0.5, 0)]
    assert pts[-4:] == [pytest.approx((9.5, 0)), pytest.approx((10, 0)),
                        pytest.approx((9.5, 0)), pytest.approx((10, 0))]
    assert stitch_points(run, Settings(lock_len=0))[0] == (0, 0)


# --- command line -----------------------------------------------------------

def test_cli_reports_missing_file_without_traceback(tmp_path, capsys):
    assert main(["list", str(tmp_path / "missing.svg")]) == 2
    assert main(["convert", str(tmp_path / "missing.svg"), "-o", str(tmp_path / "o.pes")]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_rejects_bad_hoop(tmp_path):
    with pytest.raises(SystemExit):
        main(["convert", SAMPLE, "-o", str(tmp_path / "o.pes"), "--hoop", "100"])


def test_cli_palette_lists_chart(capsys):
    assert main(["palette"]) == 0
    out = capsys.readouterr().out
    assert "900" in out and "Black" in out and "Linen" in out


def test_nearest_brother_returns_chart_thread():
    assert nearest_brother((250, 20, 30)).name == "Red"


# --- geometry (moving, resizing, rotating) ------------------------------------

def test_transforms_compose_in_order():
    from svg2pes.geometry import apply, chain, rotate, scale, translate
    p = [(10.0, 0.0)]
    assert apply(rotate(90), p)[0] == pytest.approx((0.0, 10.0))       # clockwise on screen
    assert apply(rotate(90, 10, 10), p)[0] == pytest.approx((20.0, 10.0))
    assert apply(scale(2, 3, 5, 5), p)[0] == pytest.approx((15.0, -10.0))
    m = chain(translate(1, 2), scale(2), rotate(180))
    assert apply(m, p)[0] == pytest.approx((-22.0, -4.0))


def test_placed_designs_are_not_moved_and_outside_is_reported(tmp_path):
    from svg2pes.convert import outside_hoop
    from svg2pes.geometry import apply, translate
    from svg2pes.svg_import import Design, Polyline
    d = load_svg(SAMPLE)
    # the sample spans x 2..58, y 2..38: place it 25 mm right of the hoop centre
    for o in d.objects:
        o.polylines = [Polyline(apply(translate(-5, -20), pl.points), pl.closed) for pl in o.polylines]
    plan = plan_design(d, placed=True)
    bx0, by0, bx1, by1 = plan.pattern.bounds()
    assert (bx0 / 10, bx1 / 10) == pytest.approx((-3.0, 53.0), abs=0.2)   # not re-centred
    assert outside_hoop(d, (100, 100), 2) == ["badge"]                  # reaches x = 53 > 48
    assert outside_hoop(d, (120, 100), 2) == []


# --- text -------------------------------------------------------------------

def _font():
    from svg2pes.text import default_font
    f = default_font()
    if f is None:
        pytest.skip("no fonts installed")
    return f


def test_text_outlines_have_the_requested_height():
    from svg2pes.geometry import bounds
    from svg2pes.text import TextSpec, text_polylines
    name, path, idx = _font()
    polys, missing = text_polylines(TextSpec("HI", path, name, idx, height_mm=12))
    x0, y0, x1, y1 = bounds([p for pl in polys for p in pl.points])
    assert y1 - y0 == pytest.approx(12, abs=0.3)          # capitals are the letter height
    assert (x0 + x1) / 2 == pytest.approx(0, abs=1e-6) and (y0 + y1) / 2 == pytest.approx(0, abs=1e-6)
    assert all(pl.closed for pl in polys) and missing == ""


def test_text_two_lines_and_missing_characters():
    from svg2pes.geometry import bounds
    from svg2pes.text import TextSpec, text_polylines
    name, path, idx = _font()
    one, _ = text_polylines(TextSpec("Ab", path, name, idx, 10))
    two, missing = text_polylines(TextSpec("Ab\nAb\u2603", path, name, idx, 10))
    h1 = bounds([p for pl in one for p in pl.points])
    h2 = bounds([p for pl in two for p in pl.points])
    assert (h2[3] - h2[1]) > 1.8 * (h1[3] - h1[1])
    assert missing == "\u2603" and len(two) == 2 * len(one)


def test_text_converts_and_sews(tmp_path):
    from svg2pes.svg_import import Design, DesignObject
    from svg2pes.text import TextSpec, text_polylines
    name, path, idx = _font()
    polys, _ = text_polylines(TextSpec("Sew", path, name, idx, 10))
    d = Design([DesignObject(1, "text1", "Text", (0, 0, 0), polys, color=(0, 0, 0))], [])
    plan = plan_design(d, placed=True)
    assert plan.report.thread_map[0].thread.number == "900"
    rep = write_pes_file(plan, str(tmp_path / "t.pes"))
    assert rep.stitch_count > 50


def test_bad_font_is_a_clear_error(tmp_path):
    from svg2pes.text import TextSpec, text_polylines
    bad = tmp_path / "bad.ttf"
    bad.write_bytes(b"not a font")
    with pytest.raises(ValueError, match="Cannot read font"):
        text_polylines(TextSpec("A", str(bad)))


# --- stitch preview and sew-out ----------------------------------------------

def test_track_follows_the_sewing_order():
    from svg2pes.preview import CHANGE, SEW, Track
    plan = plan_design(load_svg(SAMPLE))
    t = Track(plan.pattern)
    assert t.total_stitches == plan.report.stitch_count
    assert t.kind.count(CHANGE) == plan.report.color_blocks - 1
    assert t.stitch_no[-1] == t.total_stitches
    assert t.colour[-1] == plan.report.color_blocks - 1
    i = t.kind.index(SEW, 5)                       # a sewn entry starts where the needle was
    assert (t.fx[i], t.fy[i]) == (t.x[i - 1], t.y[i - 1])


def test_render_view_zoomed_and_partly_sewn():
    from svg2pes.preview import BACKGROUND, GHOST, Style, Track, View, render_view
    plan = plan_design(load_svg(SAMPLE))
    t = Track(plan.pattern)
    whole = View(-50, -50, 4, 400, 400)
    full = render_view(t, whole, (100, 100))
    half = render_view(t, whole, (100, 100), upto=len(t) // 2)
    colours_full = {c for _, c in full.getcolors(1 << 20)}
    colours_half = {c for _, c in half.getcolors(1 << 20)}
    assert GHOST in colours_half and GHOST not in colours_full
    # a 20x zoom on a 10 mm square still renders at the requested size, with needle dots
    zoomed = render_view(t, View(-30, -20, 80, 800, 800), (100, 100), style=Style())
    assert zoomed.size == (800, 800)
    assert BACKGROUND in {c for _, c in zoomed.getcolors(1 << 20)}


# --- fill stitching -------------------------------------------------------------

def _square(x0, y0, x1, y1):
    from svg2pes.svg_import import Polyline
    return Polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], True)


def test_fill_covers_a_rectangle_with_rows():
    from svg2pes.fill import FillSpec, fill_paths
    paths = fill_paths([_square(0, 0, 20, 10)], FillSpec(angle=0, spacing=0.5, stitch_len=3.5,
                                                        underlay=False, pull_comp=0))
    assert len(paths) == 1                                   # convex: one serpentine run
    pts = paths[0]
    assert all(-1e-6 <= x <= 20 + 1e-6 and -1e-6 <= y <= 10 + 1e-6 for x, y in pts)
    rows = sorted({round(y, 3) for _, y in pts})
    assert len(rows) == 20 and rows[0] == 0.25               # 10 mm / 0.5 mm, half a row in
    steps = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
    assert max(steps) <= 3.5 + 0.4 + 1e-6 and min(steps) >= 0.4 - 1e-6


def test_fill_leaves_holes_empty_and_follows_the_angle():
    from svg2pes.fill import FillSpec, fill_paths
    ring = [_square(0, 0, 20, 20), _square(6, 6, 14, 14)]
    paths = fill_paths(ring, FillSpec(angle=30, spacing=0.4, underlay=False))
    pts = [p for path in paths for p in path]
    inside_hole = [p for p in pts if 6.3 < p[0] < 13.7 and 6.3 < p[1] < 13.7]
    assert pts and not inside_hole
    assert len(paths) >= 2                                   # the hole splits the rows


def test_fills_sew_before_outlines_and_share_a_block(tmp_path):
    from svg2pes.svg_import import Design, DesignObject
    a = DesignObject(1, "a", "Rect", (255, 0, 0), [_square(-20, -10, 0, 10)], color=(255, 0, 0))
    b = DesignObject(2, "b", "Rect", (0, 0, 255), [_square(5, -10, 25, 10)], color=(0, 0, 255))
    d = Design([a, b], [])
    plan = plan_design(d, placed=True, fills={"a": None, "b": "Yellow"})
    labels = [tu.object_ids for tu in plan.report.thread_map]
    # red fill, yellow fill, then outlines: blue, and red sharing nothing with yellow
    assert labels[0] == ["a fill"] and labels[1] == ["b fill"]
    assert sorted(sum(labels[2:], [])) == ["a", "b"]
    assert plan.report.thread_map[1].thread.name == "Yellow"
    # fill in the outline's own thread shares a block with that outline
    plan2 = plan_design(Design([a], []), placed=True, fills={"a": None})
    assert [tu.object_ids for tu in plan2.report.thread_map] == [["a fill", "a"]]
    plan3 = plan_design(Design([a], []), placed=True, fills={"a": None}, no_outline=["a"])
    assert [tu.object_ids for tu in plan3.report.thread_map] == [["a fill"]]
    assert plan2.report.stitch_count > plan3.report.stitch_count > 300   # outline adds stitches


def test_open_shapes_cannot_be_filled(tmp_path):
    with pytest.raises(ConversionError, match="not a closed shape"):
        convert(SAMPLE, str(tmp_path / "o.pes"), fills={"wave": None})
    rep = convert(SAMPLE, str(tmp_path / "o.pes"), fills={"star": "Red", "sun": None})
    assert rep.stitch_count > 400          # 200 without the fills


# --- sewing quality ---------------------------------------------------------------

def test_underlay_comes_first_and_pull_comp_widens():
    from svg2pes.fill import FillSpec, fill_paths
    sq = [_square(0, 0, 20, 10)]
    plain = fill_paths(sq, FillSpec(angle=0, underlay=False, pull_comp=0))
    under = fill_paths(sq, FillSpec(angle=0, underlay=True, pull_comp=0))
    assert sum(map(len, under)) > sum(map(len, plain))
    # the underlay runs at right angles: its first rows are vertical (x constant)
    first = under[0][:2]
    assert abs(first[0][0] - first[1][0]) < 1e-6
    # underlay is kept 0.6 mm in from the edge
    wide = fill_paths(sq, FillSpec(angle=0, underlay=False, pull_comp=0.3))
    xs = [p[0] for path in wide for p in path]
    assert min(xs) == pytest.approx(-0.3) and max(xs) == pytest.approx(20.3)


def test_fill_under_a_stacked_shape_is_cut_out():
    from svg2pes.svg_import import Design, DesignObject
    from svg2pes.fill import FillSpec
    lower = DesignObject(1, "low", "Rect", (255, 0, 0), [_square(0, 0, 30, 30)], color=(255, 0, 0),
                         fill=FillSpec(underlay=False))
    upper = DesignObject(2, "up", "Rect", (0, 0, 255), [_square(10, 10, 20, 20)], color=(0, 0, 255),
                         fill=FillSpec(underlay=False))
    from svg2pes.stitching import Settings, plan_blocks
    blocks = plan_blocks([lower, upper], Settings())
    low_pts = [p for b in blocks for r in b.runs if r.object_id == "low" and r.fill for p in r.points]
    inside = [p for p in low_pts if 10.5 < p[0] < 19.5 and 10.5 < p[1] < 19.5]
    assert low_pts and not inside
    kept = plan_blocks([lower, upper], Settings(knockout=False))
    low2 = [p for b in kept for r in b.runs if r.object_id == "low" and r.fill for p in r.points]
    assert any(10.5 < p[0] < 19.5 and 10.5 < p[1] < 19.5 for p in low2)


def test_travel_joins_fill_pieces_instead_of_jumping():
    from svg2pes.fill import FillSpec, closed_rings, fill_paths, travel_join
    from svg2pes.svg_import import Polyline
    # a U shape: the rows split into two arms, which travel along the edge joins up
    u = Polyline([(0, 0), (9, 0), (9, 9), (6, 9), (6, 3), (3, 3), (3, 9), (0, 9), (0, 0)], True)
    spec = FillSpec(angle=0, underlay=False, pull_comp=0)
    paths = fill_paths([u], spec)
    assert len(paths) == 1                         # joined by travel stitches
    pts = paths[0]
    assert max(math.dist(a, b) for a, b in zip(pts, pts[1:])) <= 3.9 + 1e-6


def test_satin_column_has_the_right_width_and_underlay():
    from svg2pes.fill import SatinSpec, satin_path
    pts = satin_path([(0, 0), (20, 0)], False, SatinSpec(width=3, underlay=True, pull_comp=0))
    ys = [p[1] for p in pts]
    assert max(ys) == pytest.approx(1.5) and min(ys) == pytest.approx(-1.5)
    walk = [p for p in pts if abs(p[1]) < 1e-9]
    assert walk[0] == (0, 0)                        # the centre walk goes out first ...
    assert pts[-1][0] == pytest.approx(0, abs=0.01) # ... and the satin comes back
    no_under = satin_path([(0, 0), (20, 0)], False, SatinSpec(width=3, underlay=False))
    assert len(no_under) < len(pts)


def test_satin_outline_and_colour_order_in_a_plan(tmp_path):
    from svg2pes.fill import SatinSpec
    from svg2pes.stitching import Settings
    from svg2pes.svg_import import Design, DesignObject
    a = DesignObject(1, "a", "Rect", (255, 0, 0), [_square(-20, -10, 0, 10)], color=(255, 0, 0),
                     satin=SatinSpec(width=2))
    b = DesignObject(2, "b", "Rect", (0, 0, 255), [_square(5, -10, 25, 10)], color=(0, 0, 255))
    plan = plan_design(Design([a, b], []), placed=True)
    assert plan.report.thread_map[0].object_ids == ["a satin"]
    red, blue = plan.report.thread_map[0].rgb, plan.report.thread_map[1].rgb
    flipped = plan_design(Design([a, b], []), placed=True,
                          settings=Settings(color_order=((1, blue), (1, red))))
    assert [tu.object_ids for tu in flipped.report.thread_map] == [["b"], ["a satin"]]


def test_time_estimate():
    from svg2pes.convert import estimate_minutes, format_minutes
    sew, change = estimate_minutes(5525, 0, 3, spm=650)
    assert sew == pytest.approx(10.0) and change == 3.0
    assert format_minutes(0.5) == "under a minute" and format_minutes(75) == "1 h 15 min"
    plan = plan_design(load_svg(SAMPLE))
    rep = plan.report
    assert rep.total_minutes > rep.sew_minutes > 0
    assert sum(tu.stitches for tu in rep.thread_map) == rep.stitch_count


# --- projects and opening embroidery files -----------------------------------------

def test_project_round_trip_is_exact(tmp_path):
    from svg2pes.fill import FillSpec, SatinSpec
    from svg2pes.geometry import rotate
    from svg2pes.project import Project, load_project, save_project
    d = load_svg(SAMPLE)
    mats = {o.obj_id: rotate(10) for o in d.objects}
    p = Project(d.objects, mats, dict(mats), {"sun": (0, 0, 0)}, {"wave"},
                {"star": FillSpec(color=(237, 23, 31), angle=20)}, {"star"},
                {"badge": SatinSpec(width=2.5)}, {}, {"sun": "group 1", "star": "group 1"},
                [(0, (237, 23, 31))], {"hoop": ["100", "100"], "stitch_len": "3"}, SAMPLE, ["w"])
    path = str(tmp_path / "x.svg2pes")
    save_project(path, p)
    q = load_project(path)
    assert [o.obj_id for o in q.objects] == [o.obj_id for o in d.objects]
    assert q.objects[0].polylines[0].points == d.objects[0].polylines[0].points
    assert q.mats == p.mats and q.fills == p.fills and q.satins == p.satins
    assert q.skipped == {"wave"} and q.no_outline == {"star"} and q.groups == p.groups
    assert q.color_order == p.color_order and q.settings == p.settings and q.source == SAMPLE


def test_bad_project_file_is_refused(tmp_path):
    from svg2pes.project import load_project
    f = tmp_path / "bad.svg2pes"
    f.write_text('{"format": "something else"}')
    with pytest.raises(ValueError, match="not an svg2pes project"):
        load_project(str(f))
    f.write_text("{not json")
    with pytest.raises(ValueError):
        load_project(str(f))


def test_open_pes_keeps_every_stitch(tmp_path):
    from svg2pes.pes_import import load_stitches
    out = str(tmp_path / "o.pes")
    rep = convert(SAMPLE, out, fills={"star": None})
    d = load_stitches(out)
    assert all(o.stitches for o in d.objects)
    assert len(d.objects) == rep.color_blocks
    again = plan_design(d, placed=True)
    assert again.report.stitch_count == rep.stitch_count
    with pytest.raises(ValueError):
        load_stitches(str(tmp_path / "x.txt"))


# --- lettering --------------------------------------------------------------------

TEXT_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="60mm" height="40mm" viewBox="0 0 60 40">'
            '<text x="10" y="20" font-family="Arial" font-size="10" fill="#d62828">Hi</text>'
            '<text x="30" y="35" font-family="NoSuchFont" font-size="6" text-anchor="middle">Mid</text>'
            '</svg>')


def test_svg_text_is_stitched_with_the_matching_font(tmp_path):
    from svg2pes.geometry import bounds
    _font()
    p = tmp_path / "t.svg"
    p.write_text(TEXT_SVG)
    d = load_svg(str(p))
    assert [o.kind for o in d.objects] == ["Text", "Text"]
    hi, mid = d.objects
    x0, y0, x1, y1 = bounds([q for pl in hi.polylines for q in pl.points])
    assert x0 == pytest.approx(10, abs=1.2)            # starts at x = 10 (plus the H's bearing)
    assert y1 == pytest.approx(20, abs=0.05)           # sits on the baseline y = 20
    assert y1 - y0 == pytest.approx(10 * 0.716, abs=0.3)   # Arial's capitals are 0.716 em
    assert d.texts[hi.obj_id].font_name == "Arial"
    mx0, _, mx1, _ = bounds([q for pl in mid.polylines for q in pl.points])
    assert (mx0 + mx1) / 2 == pytest.approx(30, abs=0.6)   # text-anchor: middle
    assert any("NoSuchFont" in w for w in d.warnings)


def test_single_line_fonts_are_open_strokes():
    from svg2pes.geometry import bounds
    from svg2pes.text import TextSpec, installed_fonts, text_polylines
    path, idx = installed_fonts()["Single-line Sans"]
    polys, missing = text_polylines(TextSpec("HELLO", path, "Single-line Sans", idx, 8))
    assert polys and not any(pl.closed for pl in polys) and missing == ""
    x0, y0, x1, y1 = bounds([q for pl in polys for q in pl.points])
    assert y1 - y0 == pytest.approx(8, abs=0.2)


def test_monogram_and_arc_layouts():
    from svg2pes.geometry import bounds
    from svg2pes.text import TextSpec, text_polylines
    name, path, idx = _font()
    mono, _ = text_polylines(TextSpec("ABC", path, name, idx, 10, monogram=True))
    _, y0, _, y1 = bounds([q for pl in mono for q in pl.points])
    assert y1 - y0 == pytest.approx(16, abs=0.3)       # the middle letter is 1.6 times taller
    flat, _ = text_polylines(TextSpec("ARCHED", path, name, idx, 8))
    arch, _ = text_polylines(TextSpec("ARCHED", path, name, idx, 8, arc=90))
    smile, _ = text_polylines(TextSpec("ARCHED", path, name, idx, 8, arc=-90))
    hf = bounds([q for pl in flat for q in pl.points])
    ha = bounds([q for pl in arch for q in pl.points])
    assert ha[3] - ha[1] > (hf[3] - hf[1]) * 1.5       # bending makes it taller
    # arch: the middle letters are highest; smile: lowest
    def mid_top(polys):
        xs = [q for pl in polys for q in pl.points if abs(q[0]) < 2]
        return min(q[1] for q in xs)
    assert mid_top(arch) < mid_top(smile)


# --- picture import ---------------------------------------------------------------

def test_picture_is_traced_into_coloured_shapes(tmp_path):
    from PIL import Image, ImageDraw
    from svg2pes.fill import closed_rings
    from svg2pes.geometry import bounds
    from svg2pes.image_import import TraceSettings, trace_image
    img = Image.new("RGB", (200, 100), (240, 240, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 90, 90], fill=(200, 30, 40))
    d.rectangle([40, 40, 60, 60], fill=(240, 240, 240))          # a hole
    d.ellipse([110, 10, 190, 90], fill=(30, 80, 170))
    p = tmp_path / "p.png"
    img.save(p)
    objs, warnings = trace_image(str(p), TraceSettings(colours=2, width_mm=100))
    assert not warnings and len(objs) == 2
    red = next(o for o in objs if o.source_color[0] > 150)
    assert len(closed_rings(red.polylines)) == 2                  # outside and the hole
    x0, y0, x1, y1 = bounds([q for pl in red.polylines for q in pl.points])
    assert (x1 - x0) == pytest.approx(40.5, abs=1.5)              # 81 of 200 px at 100 mm
    blue = next(o for o in objs if o.source_color[2] > 150)
    assert all(pl.closed for pl in blue.polylines)


def test_illustrator_72dpi_exports_are_spotted_and_scaled(tmp_path):
    p = tmp_path / "ai.svg"
    p.write_text('<?xml version="1.0"?>\n<!-- Generator: Adobe Illustrator 27.0.0 -->\n'
                 '<svg xmlns="http://www.w3.org/2000/svg" width="144px" height="72px" '
                 'viewBox="0 0 144 72"><rect width="144" height="72" fill="none" stroke="#000"/></svg>')
    d = load_svg(str(p))
    assert d.dpi_hint == 72 and any("72 dpi" in w for w in d.warnings)
    rep = convert(str(p), str(tmp_path / "o.pes"), svg_dpi=72)
    assert rep.width_mm == pytest.approx(50.8, abs=0.2)          # 2 inches
    assert load_svg(SAMPLE).dpi_hint is None                     # sized in mm: fine


# --- hoop check from the planned stitches ---------------------------------------------

def test_satin_reaching_past_the_margin_is_flagged():
    from svg2pes.convert import outside_hoop
    from svg2pes.fill import SatinSpec
    from svg2pes.svg_import import Design, DesignObject
    # 100 mm hoop, 2 mm margin: the sewing area ends at +-48 mm; the outline sits 1 mm inside
    def rect(satin):
        return DesignObject(1, "r", "Rect", (0, 0, 0), [_square(-47, -30, 47, 30)],
                            color=(0, 0, 0), satin=satin)
    run = Design([rect(None)], [])
    sat = Design([rect(SatinSpec(width=2.0))], [])
    plan_run = plan_design(run, placed=True)
    plan_sat = plan_design(sat, placed=True)
    assert outside_hoop(run, (100, 100), 2, plan_run.report) == []
    assert outside_hoop(sat, (100, 100), 2) == []                     # the outline alone fits
    assert outside_hoop(sat, (100, 100), 2, plan_sat.report) == ["r"]  # the satin doesn't
    assert plan_sat.report.object_bounds["r"][2] == pytest.approx(48.1, abs=0.01)


def test_cli_path_refuses_or_shrinks_stitches_past_the_area(tmp_path):
    p = tmp_path / "wide.svg"
    p.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="96mm" height="40mm" '
                 'viewBox="0 0 96 40"><rect id="b" x="0" y="0" width="96" height="40" '
                 'fill="none" stroke="#000"/></svg>')
    out = str(tmp_path / "o.pes")
    assert convert(str(p), out).width_mm == pytest.approx(96, abs=0.15)    # run stitch fits
    with pytest.raises(ConversionError, match="stitches reach"):
        convert(str(p), out, satins={"b": 3.0})
    rep = convert(str(p), out, satins={"b": 3.0}, fit=True)
    assert rep.width_mm <= 96.1
    assert rep.bounds_mm[2] - rep.bounds_mm[0] <= 96.1
