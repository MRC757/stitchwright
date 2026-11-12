"""Tests: the Document (the design being edited), driven directly, with no window."""
import pytest

from conftest import SAMPLE, any_font
from stitchwright.document import DocError, Document
from stitchwright.fill import FillSpec, SatinSpec
from stitchwright.project import load_project, save_project
from stitchwright.stitching import Settings
from stitchwright.svg_import import load_svg
from stitchwright.text import TextSpec


@pytest.fixture
def doc():
    d = Document()
    d.load_design(load_svg(SAMPLE), SAMPLE)
    return d


def centre(doc, ids):
    b = doc.bounds(ids)
    return round((b[0] + b[2]) / 2, 6), round((b[1] + b[3]) / 2, 6)


def size(doc, ids):
    b = doc.bounds(ids)
    return round(b[2] - b[0], 6), round(b[3] - b[1], 6)


def test_load_centres_the_design_and_is_unmodified(doc):
    assert doc.order_ids == ["badge", "sun", "letterF", "star", "wave"]
    assert centre(doc, doc.order_ids) == (0, 0)
    assert size(doc, doc.order_ids) == pytest.approx((56, 36), abs=0.01)
    assert not doc.dirty and not doc.undo_stack


def test_move_resize_keeping_proportions_and_rotate(doc):
    doc.move_to(["sun"], 10, -20)
    assert centre(doc, ["sun"]) == pytest.approx((10, -20))
    doc.resize(["badge"], 28, 999, keep_ratio=True, width_changed=True)
    assert size(doc, ["badge"]) == pytest.approx((28, 18), abs=0.01)          # height follows
    doc.resize(["badge"], 999, 9, keep_ratio=True, width_changed=False)
    assert size(doc, ["badge"]) == pytest.approx((14, 9), abs=0.01)           # width follows
    doc.resize(["badge"], 20, 30, keep_ratio=False)
    assert size(doc, ["badge"]) == pytest.approx((20, 30), abs=0.01)
    doc.rotate(["badge"], 90)
    assert size(doc, ["badge"]) == pytest.approx((30, 20), abs=0.01)
    with pytest.raises(DocError):
        doc.resize(["badge"], 0, 10)
    assert doc.dirty


def test_groups_expand_and_move_together(doc):
    gid = doc.group(["sun", "star"])
    assert doc.expand_groups(["star"]) == ["star", "sun"]
    assert doc.units(["sun", "star", "wave"]) == [["sun", "star"], ["wave"]]
    with pytest.raises(DocError):
        doc.group(["sun"])
    assert doc.ungroup(["sun", "star"]) == 2 and gid not in doc.groups.values()


def test_copy_paste_and_duplicate_give_new_ids(doc):
    doc.group(["sun", "star"])
    doc.copy(["sun", "star"])
    new = doc.paste()
    assert new == ["sun_copy1", "star_copy1"]
    assert centre(doc, ["sun_copy1"]) == pytest.approx(
        (centre(doc, ["sun"])[0] + 5, centre(doc, ["sun"])[1] + 5))
    assert doc.groups["sun_copy1"] == doc.groups["star_copy1"] != doc.groups["sun"]
    assert doc.duplicate(["wave"]) == ["wave_copy1"]
    assert doc.clipboard[0]["obj"].obj_id == "sun"         # duplicate keeps the clipboard
    with pytest.raises(DocError):
        Document().paste()


def test_delete_undo_redo(doc):
    doc.delete(["badge", "wave"])
    assert doc.order_ids == ["sun", "letterF", "star"]
    assert doc.undo() and doc.order_ids == ["badge", "sun", "letterF", "star", "wave"]
    assert doc.redo() and "badge" not in doc.order_ids
    assert doc.undo() and doc.undo() is False               # nothing more to undo


def test_fill_and_satin_settings(doc):
    left_out = doc.set_stitches(["star", "wave"], FillSpec(angle=30), True, SatinSpec(width=2))
    assert left_out == ["wave"]                             # an open line can't be filled
    assert doc.fills["star"].angle == 30 and "wave" not in doc.fills
    assert doc.satins["star"].width == 2 and doc.satins["wave"].width == 2
    doc.set_stitches(["star"], None, False, None)
    assert "star" not in doc.fills and "star" in doc.no_outline and "star" not in doc.satins
    res = doc.plan(Settings())
    assert res.plan is not None and res.outside == []
    assert any(o == ["wave satin"] or "wave satin" in o for o in
               (tu.object_ids for tu in res.plan.report.thread_map))


def test_fill_angle_follows_rotation_and_mirroring(doc):
    doc.set_stitches(["star"], FillSpec(angle=45), True, None)
    doc.rotate(["star"], 30)
    assert doc.placed_fill("star").angle == pytest.approx(75)
    doc.flip(["star"], horizontal=True)
    assert doc.placed_fill("star").angle % 360 == pytest.approx((180 - 30 - 45) % 360)


def test_align_and_distribute(doc):
    doc.align(["sun", "star", "wave"], "left")
    lefts = {round(doc.bounds([i])[0], 6) for i in ("sun", "star", "wave")}
    assert len(lefts) == 1
    doc.align(["sun"], "top")                                # one object: to the sewing area
    assert doc.bounds(["sun"])[1] == pytest.approx(doc.area()[1])
    doc.move_to(["sun"], -30, 0)
    doc.move_to(["star"], 0, 0)
    doc.move_to(["wave"], 25, 0)
    doc.distribute(["sun", "star", "wave"], horizontal=True)
    b = sorted((doc.bounds([i]) for i in ("sun", "star", "wave")), key=lambda x: x[0])
    assert b[1][0] - b[0][2] == pytest.approx(b[2][0] - b[1][2])
    with pytest.raises(DocError):
        doc.distribute(["sun", "star"], horizontal=True)


def test_fit_leaves_room_for_satin(doc):
    doc.set_stitches(["badge"], None, True, SatinSpec(width=3))
    reach = doc.plan(Settings()).plan.report.object_bounds
    doc.fit(doc.order_ids, reach)
    res = doc.plan(Settings())
    assert res.outside == []
    assert res.plan.report.object_bounds["badge"][2] <= doc.area()[2] + 1e-6


def test_text_add_and_edit_keep_place(doc):
    name, path, idx = any_font()
    oid, missing = doc.add_text(TextSpec("Hi", path, name, idx, 8))
    assert oid == "text1" and missing == "" and centre(doc, [oid]) == pytest.approx((0, 0), abs=0.01)
    doc.move_to([oid], 20, 30)
    doc.edit_text(oid, TextSpec("Hello", path, name, idx, 8))
    assert centre(doc, [oid]) == pytest.approx((20, 30), abs=0.01)
    with pytest.raises(DocError):
        doc.add_text(TextSpec("   ", path, name, idx, 8))


def test_move_colour_stops_keeps_fills_first(doc):
    doc.set_stitches(["sun", "star"], FillSpec(), False, None)    # two fill-only stops
    def stops():
        return [tuple(tu.object_ids) for tu in doc.plan(Settings()).plan.report.thread_map]
    before = stops()
    assert before[:2] == [("sun fill",), ("star fill",)]
    doc.move_stop(doc.plan(Settings()).plan.report.thread_map, 1, -1)   # star's fill earlier
    assert stops()[:2] == [("star fill",), ("sun fill",)]
    # the first outline-only stop can't move ahead of the last fill stop
    with pytest.raises(DocError, match="Fills are always sewn before outlines"):
        doc.move_stop(doc.plan(Settings()).plan.report.thread_map, 2, -1)
    doc.auto_order()
    assert doc.color_order == [] and stops() == before


def test_project_round_trip_through_the_document(doc, tmp_path):
    doc.set_stitches(["star"], FillSpec(angle=20), False, None)
    doc.group(["sun", "star"])
    doc.rotate(["sun"], 15)
    before = doc.plan(Settings()).plan.report.stitch_count
    path = str(tmp_path / "d.stitchwright")
    save_project(path, doc.to_project({"margin": "3"}))
    other = Document()
    settings = other.load_project(load_project(path), path)
    assert settings == {"margin": "3"} and not other.dirty
    assert other.order_ids == doc.order_ids and other.groups == doc.groups
    assert other.plan(Settings()).plan.report.stitch_count == before
