"""Tests: fill patterns (contour, diamond, wave, criss-cross) and outline styles (bean,
zigzag, blanket)."""
import math

import pyembroidery
import pytest

from conftest import square, stitch_lengths, write_svg
from stitchwright.convert import plan_design
from stitchwright.svg_import import Design, DesignObject, Polyline


def circle(cx, cy, r, n=180):
    return Polyline([(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
                     for i in range(n + 1)], True)


def steps(path):
    return [math.dist(a, b) for a, b in zip(path, path[1:])]


# --- fill patterns ---------------------------------------------------------------

@pytest.mark.parametrize("pattern", ["rows", "contour", "diamond", "wave", "crosshatch"])
def test_every_fill_pattern_covers_the_shape_with_sound_stitches(pattern):
    from stitchwright.fill import FillSpec, fill_paths
    paths = fill_paths([square(0, 0, 30, 20)], FillSpec(pattern=pattern), min_len=0.4)
    pts = [p for path in paths for p in path]
    assert len(paths) == 1                                  # one piece: no jumps
    assert all(-0.3 <= x <= 30.3 and -0.3 <= y <= 20.3 for x, y in pts)   # stays inside
    for corner in ((2, 2), (28, 2), (28, 18), (2, 18), (15, 10)):         # reaches everywhere
        assert min(math.dist(corner, p) for p in pts) < 1.5
    lengths = [d for path in paths for d in steps(path)]
    assert min(lengths) >= 0.4 - 1e-9
    assert max(lengths) <= 3.5 * (1 + 0.2) + 0.5            # a pattern line may lengthen a stitch


def test_diamond_and_wave_drops_line_up_along_the_pattern():
    """Rows at 0 degrees: a diamond fill's needle drops include points on the lines
    x = k*D +/- 0.6*y, which the plain row fill's drops don't follow."""
    from stitchwright.fill import FillSpec, fill_paths

    def on_lines(paths, D=5.0):
        pts = [p for path in paths for p in path[1:-1]]
        near = 0
        for x, y in pts:
            for sign in (1, -1):
                k = round((x + sign * 0.6 * y) / D)
                if abs(x + sign * 0.6 * y - k * D) < 0.02:
                    near += 1
                    break
        return near / len(pts)

    sq = [square(0, 0, 30, 30)]
    diamond = fill_paths(sq, FillSpec(pattern="diamond", angle=0, underlay=False))
    rows = fill_paths(sq, FillSpec(pattern="rows", angle=0, underlay=False))
    assert on_lines(diamond) > 0.3 > 0.1 > on_lines(rows)
    wave = fill_paths(sq, FillSpec(pattern="wave", angle=0, underlay=False))
    assert wave and len(wave) == 1


def test_contour_fill_is_rings_spiralling_in():
    from stitchwright.fill import FillSpec, fill_paths
    paths = fill_paths([circle(0, 0, 10)], FillSpec(pattern="contour", spacing=0.5,
                                                   underlay=False, pull_comp=0))
    assert len(paths) == 1
    radii = [math.hypot(x, y) for x, y in paths[0]]
    assert max(radii) == pytest.approx(10 - 0.25, abs=0.12)        # first ring: spacing/2 in
    assert min(radii) < 1.0                                        # ... and in to the middle
    # the rings are concentric: radii cluster at 9.75, 9.25, 8.75, ...
    off = [abs((10 - 0.25 - r) / 0.5 - round((10 - 0.25 - r) / 0.5)) for r in radii[:200]]
    assert sum(o < 0.25 for o in off) / len(off) > 0.9


def test_contour_fill_leaves_holes_empty():
    from stitchwright.fill import FillSpec, fill_paths
    hole = Polyline(circle(0, 0, 4).points[::-1], True)
    paths = fill_paths([circle(0, 0, 10), hole], FillSpec(pattern="contour", underlay=False))
    pts = [p for path in paths for p in path]
    assert pts and min(math.hypot(x, y) for x, y in pts) > 4 - 0.05


def test_crosshatch_is_two_layers_at_right_angles():
    from stitchwright.fill import FillSpec, fill_paths
    path = fill_paths([square(0, 0, 20, 20)], FillSpec(pattern="crosshatch", angle=0,
                                                       underlay=False))[0]
    dirs = [round(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))) % 180
            for a, b in zip(path, path[1:]) if math.dist(a, b) > 2]
    assert dirs.count(0) > 50 and dirs.count(90) > 50


# --- outline styles --------------------------------------------------------------

def test_bean_sews_every_stitch_three_times():
    from stitchwright.fill import bean_path
    pts = bean_path([(0, 0), (10, 0)], False, 2.5)
    assert pts[:4] == [(0, 0), pts[1], (0, 0), pts[1]] and pts[1][0] == pytest.approx(2.5)
    assert pts[-1] == pytest.approx((10, 0))
    assert len(pts) == 1 + 3 * 4


@pytest.mark.parametrize("reverse", [False, True])
def test_blanket_prongs_point_inwards_whichever_way_the_shape_runs(reverse):
    from stitchwright.fill import blanket_path
    sq = square(0, 0, 20, 20).points
    pts = blanket_path(sq[::-1] if reverse else sq, True, 2.5, 2.5)
    tips = pts[2:-2:3]                                     # base, tip, base, ... after the start
    assert len(tips) > 20
    assert all(0.5 < x < 19.5 and 0.5 < y < 19.5 for x, y in tips)
    assert min(steps(pts)) >= 0.4


def test_zigzag_is_an_open_satin():
    from stitchwright.fill import outline_path, outline_spec
    spec = outline_spec("zigzag", width=3)
    pts = outline_path([(0, 0), (30, 0)], False, spec)
    assert not spec.underlay
    ys = [y for _, y in pts]
    assert max(ys) == pytest.approx(1.6, abs=0.05) and min(ys) == pytest.approx(-1.6, abs=0.05)
    assert sum(steps(pts)) / len(pts) > 1.5                # open: points 1.5 mm apart along it


@pytest.mark.parametrize("style", ["bean", "satin", "zigzag", "blanket"])
def test_every_outline_style_plans_and_writes(style, tmp_path):
    from stitchwright.fill import outline_spec
    from stitchwright.convert import write_pes_file
    o = DesignObject(1, "s", "Rect", (0, 0, 0), [square(-15, -15, 15, 15)], color=(0, 0, 0),
                     satin=outline_spec(style))
    plan = plan_design(Design([o], []), placed=True)
    labels = [t.object_ids for t in plan.report.thread_map]
    assert labels == [[f"s {style}"]]
    rep = write_pes_file(plan, str(tmp_path / "o.pes"))
    assert rep.stitch_count > 40
    assert min(stitch_lengths(pyembroidery.read_pes(str(tmp_path / "o.pes")))) >= 0.3


# --- files, the command line and the dialog ------------------------------------------

def test_patterns_and_styles_are_kept_in_projects(tmp_path):
    from stitchwright.fill import FillSpec, outline_spec
    from stitchwright.project import Project, load_project, save_project
    o = DesignObject(1, "s", "Rect", (0, 0, 0), [square(0, 0, 10, 10)], color=(0, 0, 0))
    p = Project([o], fills={"s": FillSpec(pattern="wave", pattern_size=7)},
                satins={"s": outline_spec("blanket", width=3)})
    save_project(str(tmp_path / "p.stitchwright"), p)
    q = load_project(str(tmp_path / "p.stitchwright"))
    assert q.fills == p.fills and q.satins == p.satins
    assert q.satins["s"].style == "blanket" and q.fills["s"].pattern == "wave"


def test_command_line_patterns_and_outline_styles(tmp_path):
    from stitchwright.cli import main
    svg = write_svg(tmp_path, '<rect id="a" x="5" y="5" width="20" height="20" fill="#f00" '
                              'stroke="#f00"/><rect id="b" x="35" y="5" width="20" height="20" '
                              'fill="none" stroke="#00f"/>')
    out = str(tmp_path / "o.pes")
    assert main(["convert", svg, "-o", out, "--fill", "a", "--fill-pattern", "contour",
                 "--outline", "a=bean", "--outline", "b=blanket:3"]) == 0
    assert pyembroidery.read_pes(out).count_stitches() > 100
    with pytest.raises(SystemExit, match="--outline expects"):
        main(["convert", svg, "-o", out, "--outline", "b=sparkly"])


def test_fill_dialog_offers_patterns_and_outline_styles(window):
    from stitchwright.dialogs import FillDialog
    from stitchwright.fill import FILL_PATTERNS, FillSpec, SatinSpec
    dlg = FillDialog(window, 1.0, "t", FillSpec(), True, (0, 0, 0), None)
    try:
        dlg.v_pattern.set(FILL_PATTERNS["diamond"])
        dlg._pattern_chosen()
        assert dlg.v_angle.get() == "0"                     # diamonds stand upright
        assert str(dlg.e_psize.cget("state")) == "normal"
        dlg.v_style.set("blanket")
        dlg._style_chosen()
        assert dlg.v_width.get() == "2.5" and dlg.v_ospacing.get() == "2.5"
        dlg._ok()
        res = dlg.result
        assert res["fill"].pattern == "diamond" and res["fill"].angle == 0
        assert res["satin"] == SatinSpec(width=2.5, spacing=2.5, underlay=False, style="blanket")
    finally:
        if dlg.winfo_exists():
            dlg.destroy()
    dlg = FillDialog(window, 1.0, "t", None, True, (0, 0, 0), SatinSpec(width=3))
    try:
        assert dlg.v_style.get() == "satin" and dlg.v_width.get() == "3"
        dlg.v_style.set("run")
        dlg._style_chosen()
        assert str(dlg.e_owidth.cget("state")) == "disabled"
    finally:
        dlg.destroy()
