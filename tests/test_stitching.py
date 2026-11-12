"""Tests: run stitches, fill, satin, sewing quality and the stitch preview."""
import math

import pyembroidery
import pytest

from conftest import SAMPLE, square, stitch_lengths, write_svg
from svg2pes.convert import ConversionError, convert, plan_design
from svg2pes.stitching import Run, Settings, resample, stitch_points
from svg2pes.svg_import import load_svg


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


# --- fill stitching -------------------------------------------------------------


def test_fill_covers_a_rectangle_with_rows():
    from svg2pes.fill import FillSpec, fill_paths
    paths = fill_paths([square(0, 0, 20, 10)], FillSpec(angle=0, spacing=0.5, stitch_len=3.5,
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
    ring = [square(0, 0, 20, 20), square(6, 6, 14, 14)]
    paths = fill_paths(ring, FillSpec(angle=30, spacing=0.4, underlay=False))
    pts = [p for path in paths for p in path]
    inside_hole = [p for p in pts if 6.3 < p[0] < 13.7 and 6.3 < p[1] < 13.7]
    assert pts and not inside_hole
    assert len(paths) >= 2                                   # the hole splits the rows


def test_fills_sew_before_outlines_and_share_a_block(tmp_path):
    from svg2pes.svg_import import Design, DesignObject
    a = DesignObject(1, "a", "Rect", (255, 0, 0), [square(-20, -10, 0, 10)], color=(255, 0, 0))
    b = DesignObject(2, "b", "Rect", (0, 0, 255), [square(5, -10, 25, 10)], color=(0, 0, 255))
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
    sq = [square(0, 0, 20, 10)]
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
    from svg2pes.svg_import import DesignObject
    from svg2pes.fill import FillSpec
    lower = DesignObject(1, "low", "Rect", (255, 0, 0), [square(0, 0, 30, 30)], color=(255, 0, 0),
                         fill=FillSpec(underlay=False))
    upper = DesignObject(2, "up", "Rect", (0, 0, 255), [square(10, 10, 20, 20)], color=(0, 0, 255),
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
    from svg2pes.fill import FillSpec, fill_paths
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
    a = DesignObject(1, "a", "Rect", (255, 0, 0), [square(-20, -10, 0, 10)], color=(255, 0, 0),
                     satin=SatinSpec(width=2))
    b = DesignObject(2, "b", "Rect", (0, 0, 255), [square(5, -10, 25, 10)], color=(0, 0, 255))
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


def test_curves_get_shorter_stitches_straight_runs_do_not():
    import numpy as np
    from svg2pes.geometry import simplify
    # a 12 mm circle, sampled and simplified the way SVG outlines are
    circle = [(6 * math.cos(t), 6 * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 95)]
    pts = resample(np.array(simplify(circle, 0.05)), 2.5, 35, 0.4)
    assert len(pts) - 1 >= 30                                   # was 16 with even spacing
    assert min(math.dist(a, b) for a, b in zip(pts, pts[1:])) >= 1.0 - 1e-6
    line = resample(np.array([(0, 0), (50, 0)], float), 2.5, 35, 0.4)
    assert len(line) - 1 == 20                                  # unchanged
    even = resample(np.array(simplify(circle, 0.05)), 2.5, 35, 0.4, curve_turn_deg=0)
    assert len(even) - 1 < 20                                   # 0 turns the feature off


def test_travel_runs_inside_the_fill_when_no_outline_covers_it():
    from svg2pes.fill import FillSpec, closed_rings, fill_paths
    from svg2pes.geometry import segment_distance
    from svg2pes.svg_import import Polyline
    u = Polyline([(0, 0), (9, 0), (9, 9), (6, 9), (6, 3), (3, 3), (3, 9), (0, 9), (0, 0)], True)
    ring = closed_rings([u])[0]
    spec = FillSpec(angle=0, spacing=0.4, underlay=False, pull_comp=0)
    row_ys = [0.2 + 0.4 * k for k in range(22)]          # rows at y = 0.2 ... 8.6

    def travel_points(paths):
        """Points that aren't on a fill row: the travel stitches."""
        return [p for path in paths for p in path
                if min(abs(p[1] - y) for y in row_ys) > 1e-6]

    def edge_gap(p):
        return min(segment_distance(p, a, b) for a, b in zip(ring, ring[1:]))

    covered = travel_points(fill_paths([u], spec, hidden=True))
    assert covered and min(edge_gap(p) for p in covered) < 0.05      # on the edge
    shown = travel_points(fill_paths([u], spec, hidden=False))
    assert shown and min(edge_gap(p) for p in shown) >= 0.25         # tucked inside
    assert len(fill_paths([u], spec, hidden=False)) == 1             # still joined, no jump
