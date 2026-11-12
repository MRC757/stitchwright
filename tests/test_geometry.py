"""Tests: the shared polyline helpers in geometry.py."""
import pytest

from stitchwright.geometry import (chaikin, drop_close, point_in_polygon, polygon_area,
                                   segment_distance, simplify, subdivide)

SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]


def test_simplify_drops_points_on_straight_runs_only():
    line = [(0, 0), (1, 0.01), (2, 0), (3, 5), (4, 0)]
    assert simplify(line, 0.1) == [(0, 0), (2, 0), (3, 5), (4, 0)]
    assert simplify(line, 0) == [(float(x), float(y)) for x, y in line]
    long = [(i * 0.01, (i % 2) * 0.001) for i in range(20000)]   # no recursion limit
    assert simplify(long, 0.01) == [long[0], long[-1]]


def test_chaikin_keeps_open_ends_and_closed_rings_closed():
    open_ = chaikin([(0, 0), (10, 0), (10, 10)], rounds=1)
    assert open_[0] == (0, 0) and open_[-1] == (10, 10)
    ring = chaikin(SQUARE, rounds=2, closed=True)
    assert ring[0] == ring[-1] and len(ring) == 4 * 4 + 1
    assert all(0 < x < 10 or 0 < y < 10 for x, y in ring)      # corners are cut


def test_drop_close_keeps_both_ends():
    pts = [(0, 0), (0.1, 0), (1, 0), (1.05, 0)]
    assert drop_close(pts, 0.4) == [(0, 0), (1.05, 0)]
    assert drop_close(pts, 0) == pts


def test_subdivide_and_point_helpers():
    assert subdivide((0, 0), (10, 0), 3) == [(2.5, 0), (5, 0), (7.5, 0), (10, 0)]
    assert segment_distance((5, 3), (0, 0), (10, 0)) == pytest.approx(3)
    assert segment_distance((13, 4), (0, 0), (10, 0)) == pytest.approx(5)
    assert point_in_polygon((5, 5), SQUARE) and not point_in_polygon((15, 5), SQUARE)
    assert polygon_area(SQUARE) == pytest.approx(100)
