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
    assert any("Text" in w for w in d.warnings)


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
