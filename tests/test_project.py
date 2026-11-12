"""Tests: project files and opening embroidery files."""
import pyembroidery
import pytest

from conftest import SAMPLE
from svg2pes.convert import convert, plan_design
from svg2pes.svg_import import load_svg


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


def test_files_without_colours_get_distinct_placeholder_threads(tmp_path):
    from svg2pes.pes_import import load_stitches
    p = pyembroidery.EmbPattern()
    for i, c in enumerate(("red", "blue", "green")):
        if i:
            p.color_change()
        p.add_thread(c)
        for k in range(10):
            p.stitch_abs(i * 100 + k * 20, (k % 2) * 30)
    p.end()
    path = str(tmp_path / "three.dst")
    pyembroidery.write_dst(p, path)
    d = load_stitches(path)
    assert len(d.objects) == 3
    assert len({o.source_color for o in d.objects}) == 3          # three different threads
    assert any("DST files carry no colours" in w for w in d.warnings)
    assert plan_design(d, placed=True).report.color_blocks == 3   # they stay separate stops
