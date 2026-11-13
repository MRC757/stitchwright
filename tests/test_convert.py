"""Tests: threads, conversion, the command line, placement and the hoop check."""
import os

import pyembroidery
import pytest

from conftest import RED, SAMPLE, square, write_svg
from stitchwright.cli import main
from stitchwright.convert import ConversionError, convert, plan_design, write_pes_file
from stitchwright.palette import brother_threads, nearest_brother, parse_color
from stitchwright.stitching import Settings
from stitchwright.svg_import import load_svg


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
    assert (bx0, by0) == (0, 0)                 # the stitch data starts at the top-left corner
    assert not os.path.exists(out + ".part")


def test_pes_starts_at_the_designs_corner_wherever_it_sits_in_the_hoop(tmp_path):
    """Brother's own PES files start their stitch data at the design's top-left corner and
    the machine places the design itself. A design placed low in a 5 x 7 hoop used to be
    written from the hoop centre, a point outside the design, and the PE540D sewed half
    of it squashed against the edge of its sewing area."""
    from stitchwright.svg_import import Design, DesignObject
    low = DesignObject(1, "heart", "Path", RED, [square(-25, 14, 26, 60)], color=RED)
    plan = plan_design(Design([low], []), hoop_mm=(130, 180), placed=True)
    out = str(tmp_path / "heart.pes")
    write_pes_file(plan, out)
    back = pyembroidery.read_pes(out)
    px0, py0, px1, py1 = plan.pattern.bounds()
    bx0, by0, bx1, by1 = back.bounds()
    assert (bx0, by0) == (0, 0)
    assert (bx1, by1) == (px1 - px0, py1 - py0)              # same size ...

    def steps(p):
        pts = [(x, y) for x, y, c in p.stitches if c & 0xFF == pyembroidery.STITCH]
        return [(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    assert steps(back) == steps(plan.pattern)                # ... and the same stitches
    assert plan.pattern.bounds() == (px0, py0, px1, py1)     # the plan itself is untouched
    with open(out, "rb") as f:                               # and the PEC size field agrees
        data = f.read()
    blk = int.from_bytes(data[8:12], "little") + 512
    assert (int.from_bytes(data[blk + 8:blk + 10], "little"),
            int.from_bytes(data[blk + 10:blk + 12], "little")) == (px1 - px0, py1 - py0)


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
    from stitchwright.geometry import apply, chain, rotate, scale, translate
    p = [(10.0, 0.0)]
    assert apply(rotate(90), p)[0] == pytest.approx((0.0, 10.0))       # clockwise on screen
    assert apply(rotate(90, 10, 10), p)[0] == pytest.approx((20.0, 10.0))
    assert apply(scale(2, 3, 5, 5), p)[0] == pytest.approx((15.0, -10.0))
    m = chain(translate(1, 2), scale(2), rotate(180))
    assert apply(m, p)[0] == pytest.approx((-22.0, -4.0))


def test_placed_designs_are_not_moved_and_outside_is_reported(tmp_path):
    from stitchwright.convert import outside_hoop
    from stitchwright.geometry import apply, translate
    from stitchwright.svg_import import Polyline
    d = load_svg(SAMPLE)
    # the sample spans x 2..58, y 2..38: place it 25 mm right of the hoop centre
    for o in d.objects:
        o.polylines = [Polyline(apply(translate(-5, -20), pl.points), pl.closed) for pl in o.polylines]
    plan = plan_design(d, placed=True)
    bx0, by0, bx1, by1 = plan.pattern.bounds()
    assert (bx0 / 10, bx1 / 10) == pytest.approx((-3.0, 53.0), abs=0.2)   # not re-centred
    assert outside_hoop(d, (100, 100), 2) == ["badge"]                  # reaches x = 53 > 48
    assert outside_hoop(d, (120, 100), 2) == []


def test_cli_refuses_a_zero_machine_speed_without_a_traceback(tmp_path, capsys):
    assert main(["convert", SAMPLE, "-o", str(tmp_path / "o.pes"), "--speed", "0"]) == 2
    assert "Machine speed" in capsys.readouterr().err


@pytest.mark.parametrize("pull", [-1.0, 5.0])
def test_pull_compensation_outside_0_to_1_mm_is_refused(pull):
    from stitchwright.fill import FillSpec
    with pytest.raises(ConversionError, match="Pull compensation"):
        plan_design(load_svg(SAMPLE), fills={"star": None}, fill_spec=FillSpec(pull_comp=pull),
                    fit=True)


def test_long_imported_stitches_are_warned_about():
    from stitchwright.svg_import import Design, DesignObject, Polyline

    def plan(step):
        line = Polyline([(-40.0 + i * step, 0.0) for i in range(4)], False)
        obj = DesignObject(1, "stitches1", "Stitches", (0, 0, 0), [line], stitches=True)
        return plan_design(Design([obj], []), placed=True).report.warnings

    assert not [w for w in plan(4.0) if "mm long" in w]
    (w,) = [w for w in plan(20.0) if "mm long" in w]
    assert "20.0 mm" in w and "stitches1" in w


# --- hoop check from the planned stitches ---------------------------------------------

def test_satin_reaching_past_the_margin_is_flagged():
    from stitchwright.convert import outside_hoop
    from stitchwright.fill import SatinSpec
    from stitchwright.svg_import import Design, DesignObject
    # 100 mm hoop, 2 mm margin: the sewing area ends at +-48 mm; the outline sits 1 mm inside
    def rect(satin):
        return DesignObject(1, "r", "Rect", (0, 0, 0), [square(-47, -30, 47, 30)],
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


def test_choices_and_the_old_keywords_give_the_same_plan():
    from stitchwright.convert import Choices
    from stitchwright.fill import FillSpec
    d = load_svg(SAMPLE)
    c = Choices(overrides={"sun": "Red"}, skip=["wave"], fills={"star": None},
                satins={"badge": 2.0}, fill_spec=FillSpec(angle=30))
    new = plan_design(d, c).report
    old = plan_design(d, overrides={"sun": "Red"}, skip=["wave"], fills={"star": None},
                      satins={"badge": 2.0}, fill_spec=FillSpec(angle=30)).report
    assert (new.stitch_count, new.color_blocks) == (old.stitch_count, old.color_blocks)
    assert c.overrides == {"sun": "Red"}                      # the caller's Choices is not changed
    with pytest.raises(TypeError, match="overides"):
        plan_design(d, overides={"sun": "Red"})               # a typo is still an error
