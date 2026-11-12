"""Tests: run stitches, fill, satin, sewing quality and the stitch preview."""
import math

import pyembroidery
import pytest

from conftest import SAMPLE, hole, square, stitch_lengths, write_svg
from stitchwright.convert import ConversionError, convert, plan_design
from stitchwright.stitching import Run, Settings, resample, stitch_points
from stitchwright.svg_import import load_svg


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


def _walk_at(line, s):
    """The original, walk-from-the-start version of _Line.at, as a reference."""
    s = max(0.0, min(line.total, s))
    i = 0
    while i < len(line.seg) - 1 and line.cum[i + 1] < s:
        i += 1
    t = 0.0 if line.seg[i] == 0 else (s - line.cum[i]) / line.seg[i]
    a, b = line.pts[i], line.pts[i + 1]
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def test_satin_is_unchanged_by_the_faster_point_lookup(monkeypatch):
    from stitchwright import fill
    pts = [(i * 0.5, 4 * math.sin(i * 0.2)) for i in range(120)]
    pts[30:30] = [pts[30]]                                    # a zero-length segment
    line = fill._Line(pts)
    for s in [-1, 0, line.cum[1], line.cum[30], line.cum[31], line.total / 3, line.total, 1e9]:
        assert line.at(s) == _walk_at(line, s)
    fast = fill.satin_path(pts, False, fill.SatinSpec())
    monkeypatch.setattr(fill._Line, "at", _walk_at)
    assert fast == fill.satin_path(pts, False, fill.SatinSpec())


def test_fill_covers_a_rectangle_with_rows():
    from stitchwright.fill import FillSpec, fill_paths
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
    from stitchwright.fill import FillSpec, fill_paths
    ring = [square(0, 0, 20, 20), hole(6, 6, 14, 14)]
    paths = fill_paths(ring, FillSpec(angle=30, spacing=0.4, underlay=False))
    pts = [p for path in paths for p in path]
    inside_hole = [p for p in pts if 6.3 < p[0] < 13.7 and 6.3 < p[1] < 13.7]
    assert pts and not inside_hole
    assert len(paths) >= 2                                   # the hole splits the rows


def _points_in(paths, x0, y0, x1, y1):
    return [p for path in paths for p in path if x0 < p[0] < x1 and y0 < p[1] < y1]


def test_overlapping_contours_drawn_the_same_way_are_filled_unless_svg_says_evenodd(tmp_path):
    """SVG's default fill rule is nonzero: where two parts of one path overlap and wind
    the same way, the overlap is solid. fill-rule="evenodd" makes it a hole."""
    from stitchwright.fill import FillSpec, fill_paths
    d = 'd="M0,0 H20 V20 H0 Z M10,10 H30 V30 H10 Z"'
    svg = write_svg(tmp_path, f'<path id="n" fill="#f00" {d}/>'
                              f'<g style="fill-rule:evenodd"><path id="e" fill="#00f" {d}/></g>')
    nonzero, evenodd = load_svg(svg).objects
    assert nonzero.fill_rule == "nonzero" and evenodd.fill_rule == "evenodd"
    spec = FillSpec(angle=0, underlay=False)
    assert _points_in(fill_paths(nonzero.polylines, spec, rule=nonzero.fill_rule), 12, 12, 18, 18)
    assert not _points_in(fill_paths(evenodd.polylines, spec, rule=evenodd.fill_rule),
                          10.5, 10.5, 19.5, 19.5)


@pytest.mark.parametrize("rule", ["nonzero", "evenodd"])
def test_a_hole_wound_the_other_way_is_empty_under_either_rule(rule):
    from stitchwright.fill import FillSpec, fill_paths
    paths = fill_paths([square(0, 0, 20, 20), hole(6, 6, 14, 14)],
                       FillSpec(angle=0, underlay=True), rule=rule, hidden=False)
    assert _points_in(paths, 1, 1, 5, 19)
    assert not _points_in(paths, 6.5, 6.5, 13.5, 13.5)


def test_a_same_way_inner_square_is_solid_under_nonzero_and_a_hole_under_evenodd():
    from stitchwright.fill import FillSpec, fill_paths
    shape = [square(0, 0, 20, 20), square(6, 6, 14, 14)]
    spec = FillSpec(angle=0, underlay=False)
    assert _points_in(fill_paths(shape, spec, rule="nonzero"), 7, 7, 13, 13)
    assert not _points_in(fill_paths(shape, spec, rule="evenodd"), 6.5, 6.5, 13.5, 13.5)


def test_flipping_an_object_does_not_change_what_is_filled():
    """Mirroring reverses every contour together, so the nonzero rule fills the same areas."""
    from stitchwright.document import Document
    from stitchwright.fill import FillSpec
    from stitchwright.svg_import import Design, DesignObject
    from stitchwright.stitching import plan_blocks
    # two overlapping squares wound the same way, with a hole in the second; the
    # document centres it in the hoop: squares at -15..5 and -5..15, hole at 0..10
    # (the hole's corner over the first square is still sewn: wound +1 +1 -1)
    shape = DesignObject(1, "s", "Path", (255, 0, 0),
                         [square(-20, -20, 0, 0), square(-10, -10, 10, 10), hole(-5, -5, 5, 5)],
                         color=(255, 0, 0))
    doc = Document()
    doc.load_design(Design([shape], []), "x.svg")
    doc.set_stitches(["s"], FillSpec(angle=0, underlay=False), True, None)

    def fill_points():
        blocks = plan_blocks(doc.build_design()[0].active(), Settings())
        return [[p for b in blocks for r in b.runs if r.fill for p in r.points]]

    assert _points_in(fill_points(), -4.5, -4.5, -0.5, -0.5)      # the overlap is sewn
    assert not _points_in(fill_points(), 5.5, 5.5, 9.5, 9.5)      # the hole is not
    doc.flip(["s"], horizontal=True)                              # about x = 0
    assert _points_in(fill_points(), 0.5, -4.5, 4.5, -0.5)
    assert not _points_in(fill_points(), -9.5, 5.5, -5.5, 9.5)


def test_fills_sew_before_outlines_and_share_a_block(tmp_path):
    from stitchwright.svg_import import Design, DesignObject
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
    from stitchwright.fill import FillSpec, fill_paths
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
    from stitchwright.svg_import import DesignObject
    from stitchwright.fill import FillSpec
    lower = DesignObject(1, "low", "Rect", (255, 0, 0), [square(0, 0, 30, 30)], color=(255, 0, 0),
                         fill=FillSpec(underlay=False))
    upper = DesignObject(2, "up", "Rect", (0, 0, 255), [square(10, 10, 20, 20)], color=(0, 0, 255),
                         fill=FillSpec(underlay=False))
    from stitchwright.stitching import Settings, plan_blocks
    blocks = plan_blocks([lower, upper], Settings())
    low_pts = [p for b in blocks for r in b.runs if r.object_id == "low" and r.fill for p in r.points]
    inside = [p for p in low_pts if 10.5 < p[0] < 19.5 and 10.5 < p[1] < 19.5]
    assert low_pts and not inside
    kept = plan_blocks([lower, upper], Settings(knockout=False))
    low2 = [p for b in kept for r in b.runs if r.object_id == "low" and r.fill for p in r.points]
    assert any(10.5 < p[0] < 19.5 and 10.5 < p[1] < 19.5 for p in low2)


def test_cut_out_fill_reaches_a_little_under_the_shape_on_top():
    from stitchwright.fill import KNOCKOUT_OVERLAP_MM, FillSpec
    from stitchwright.stitching import Settings, plan_blocks
    from stitchwright.svg_import import DesignObject
    lower = DesignObject(1, "low", "Rect", (255, 0, 0), [square(0, 0, 30, 30)], color=(255, 0, 0),
                         fill=FillSpec(angle=0, underlay=False))
    upper = DesignObject(2, "up", "Rect", (0, 0, 255), [square(10, 10, 20, 20)], color=(0, 0, 255),
                         fill=FillSpec(angle=0, underlay=False))
    blocks = plan_blocks([lower, upper], Settings())
    beside = [p[0] for b in blocks for r in b.runs if r.object_id == "low" and r.fill
              for p in r.points if 11 < p[1] < 19]      # rows level with the upper square
    left = max(x for x in beside if x < 15)
    right = min(x for x in beside if x > 15)
    assert left == pytest.approx(10 + KNOCKOUT_OVERLAP_MM)
    assert right == pytest.approx(20 - KNOCKOUT_OVERLAP_MM)


def test_travel_joins_fill_pieces_instead_of_jumping():
    from stitchwright.fill import FillSpec, fill_paths
    from stitchwright.svg_import import Polyline
    # a U shape: the rows split into two arms, which travel along the edge joins up
    u = Polyline([(0, 0), (9, 0), (9, 9), (6, 9), (6, 3), (3, 3), (3, 9), (0, 9), (0, 0)], True)
    spec = FillSpec(angle=0, underlay=False, pull_comp=0)
    paths = fill_paths([u], spec)
    assert len(paths) == 1                         # joined by travel stitches
    pts = paths[0]
    assert max(math.dist(a, b) for a, b in zip(pts, pts[1:])) <= 3.9 + 1e-6


def test_satin_column_has_the_right_width_and_underlay():
    from stitchwright.fill import SatinSpec, satin_path
    pts = satin_path([(0, 0), (20, 0)], False, SatinSpec(width=3, underlay=True, pull_comp=0))
    ys = [p[1] for p in pts]
    assert max(ys) == pytest.approx(1.5) and min(ys) == pytest.approx(-1.5)
    walk = [p for p in pts if abs(p[1]) < 1e-9]
    assert walk[0] == (0, 0)                        # the centre walk goes out first ...
    assert pts[-1][0] == pytest.approx(0, abs=0.01) # ... and the satin comes back
    no_under = satin_path([(0, 0), (20, 0)], False, SatinSpec(width=3, underlay=False))
    assert len(no_under) < len(pts)


def test_satin_outline_and_colour_order_in_a_plan(tmp_path):
    from stitchwright.fill import SatinSpec
    from stitchwright.stitching import Settings
    from stitchwright.svg_import import Design, DesignObject
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
    from stitchwright.convert import estimate_minutes, format_minutes
    sew, change = estimate_minutes(5525, 0, 3, spm=650)
    assert sew == pytest.approx(10.0) and change == 3.0
    assert format_minutes(0.5) == "under a minute" and format_minutes(75) == "1 h 15 min"
    plan = plan_design(load_svg(SAMPLE))
    rep = plan.report
    assert rep.total_minutes > rep.sew_minutes > 0
    assert sum(tu.stitches for tu in rep.thread_map) == rep.stitch_count


# --- stitch preview and sew-out ----------------------------------------------

def test_track_follows_the_sewing_order():
    from stitchwright.preview import CHANGE, SEW, Track
    plan = plan_design(load_svg(SAMPLE))
    t = Track(plan.pattern)
    assert t.total_stitches == plan.report.stitch_count
    assert t.kind.count(CHANGE) == plan.report.color_blocks - 1
    assert t.stitch_no[-1] == t.total_stitches
    assert t.colour[-1] == plan.report.color_blocks - 1
    i = t.kind.index(SEW, 5)                       # a sewn entry starts where the needle was
    assert (t.fx[i], t.fy[i]) == (t.x[i - 1], t.y[i - 1])


def test_render_view_zoomed_and_partly_sewn():
    from stitchwright.preview import BACKGROUND, GHOST, Style, Track, View, render_view
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
    from stitchwright.geometry import simplify
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
    from stitchwright.fill import FillSpec, closed_rings, fill_paths
    from stitchwright.geometry import segment_distance
    from stitchwright.svg_import import Polyline
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
