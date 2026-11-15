"""Tests: project files and opening embroidery files."""
import os

import pyembroidery
import pytest

from conftest import SAMPLE
from stitchwright.convert import convert, plan_design
from stitchwright.svg_import import load_svg


# --- projects and opening embroidery files -----------------------------------------

def test_project_round_trip_is_exact(tmp_path):
    from stitchwright.fill import FillSpec, SatinSpec
    from stitchwright.geometry import rotate
    from stitchwright.project import Project, load_project, save_project
    d = load_svg(SAMPLE)
    mats = {o.obj_id: rotate(10) for o in d.objects}
    p = Project(d.objects, mats, dict(mats), {"sun": (0, 0, 0)}, {"wave"},
                {"star": FillSpec(color=(237, 23, 31), angle=20)}, {"star"},
                {"badge": SatinSpec(width=2.5)}, {}, {"sun": "group 1", "star": "group 1"},
                [(0, (237, 23, 31))], {"hoop": ["100", "100"], "stitch_len": "3"}, SAMPLE, ["w"])
    path = str(tmp_path / "x.stitchwright")
    save_project(path, p)
    q = load_project(path)
    assert [o.obj_id for o in q.objects] == [o.obj_id for o in d.objects]
    assert q.objects[0].polylines[0].points == d.objects[0].polylines[0].points
    assert q.mats == p.mats and q.fills == p.fills and q.satins == p.satins
    assert q.skipped == {"wave"} and q.no_outline == {"star"} and q.groups == p.groups
    assert q.color_order == p.color_order and q.settings == p.settings and q.source == SAMPLE


def test_project_from_a_later_version_with_new_settings_still_opens():
    from conftest import any_font
    from stitchwright.fill import FillSpec, SatinSpec
    from stitchwright.project import Project, from_dict, to_dict
    from stitchwright.text import TextSpec
    name, path, idx = any_font()
    d = load_svg(SAMPLE)
    ids = [o.obj_id for o in d.objects]
    p = Project(d.objects, fills={ids[0]: FillSpec(angle=20)},
                satins={ids[1]: SatinSpec(width=2.5)},
                texts={ids[2]: TextSpec("Hi", path, name, idx, 12.0)})
    data = to_dict(p)
    data["fills"][ids[0]]["sparkle"] = True          # settings a later version might add
    data["satins"][ids[1]]["taper"] = 0.5
    data["texts"][ids[2]]["kerning"] = "auto"
    q = from_dict(data)
    assert q.fills[ids[0]].angle == 20
    assert q.satins[ids[1]].width == 2.5
    assert q.texts[ids[2]].text == "Hi" and q.texts[ids[2]].height_mm == 12.0


def test_project_keeps_each_objects_fill_rule():
    from dataclasses import replace
    from stitchwright.project import Project, from_dict, to_dict
    d = load_svg(SAMPLE)
    objs = [replace(d.objects[0], fill_rule="evenodd")] + d.objects[1:]
    data = to_dict(Project(objs))
    q = from_dict(data)
    assert [o.fill_rule for o in q.objects] == ["evenodd"] + ["nonzero"] * (len(objs) - 1)
    for o in data["objects"]:                 # saved before fill rules were stored
        del o["fill_rule"]
    assert all(o.fill_rule == "nonzero" for o in from_dict(data).objects)


def test_bad_project_file_is_refused(tmp_path):
    from stitchwright.project import load_project
    f = tmp_path / "bad.stitchwright"
    f.write_text('{"format": "something else"}')
    with pytest.raises(ValueError, match="not a Stitchwright project"):
        load_project(str(f))
    f.write_text("{not json")
    with pytest.raises(ValueError):
        load_project(str(f))


def test_open_pes_keeps_every_stitch(tmp_path):
    from stitchwright.pes_import import load_stitches
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
    from stitchwright.pes_import import load_stitches
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


def test_project_fonts_are_found_by_name_on_another_computer(tmp_path):
    import json
    from conftest import any_font
    from stitchwright.document import DocError, Document
    from stitchwright.project import load_project, save_project
    from stitchwright.stitching import Settings
    from stitchwright.text import TextSpec
    name, path, idx = any_font()
    doc = Document()
    doc.add_text(TextSpec("Hi", path, name, idx, 8))
    doc.add_text(TextSpec("Yo", path, "Some Font Nobody Has", idx, 8))
    stitches = doc.plan(Settings()).plan.report.stitch_count
    proj = str(tmp_path / "p.stitchwright")
    save_project(proj, doc.to_project({}))
    # pretend the project was made on a PC whose fonts lived somewhere else
    data = json.load(open(proj, encoding="utf-8"))
    for spec in data["texts"].values():
        spec["font_path"] = "Z:/elsewhere/fonts/" + os.path.basename(path)
    json.dump(data, open(proj, "w", encoding="utf-8"))
    p = load_project(proj)
    assert p.texts["text1"].font_path == path                  # found again by its name
    assert p.texts["text2"].font_path.startswith("Z:/")        # not installed: kept ...
    assert any("Some Font Nobody Has" in w for w in p.warnings)  # ... with a warning
    other = Document()
    other.load_project(p, proj)
    assert other.plan(Settings()).plan.report.stitch_count == stitches   # still sews as saved
    with pytest.raises(DocError, match="Cannot read font"):
        other.edit_text("text2", replace_text(p.texts["text2"], "Yes"))


def test_project_from_windows_uses_a_font_with_the_same_widths_on_linux(tmp_path, monkeypatch):
    from stitchwright import text
    from stitchwright.document import Document
    from stitchwright.project import load_project, save_project
    from stitchwright.text import TextSpec
    name, path, idx = __import__("conftest").any_font()
    doc = Document()
    import json
    doc.add_text(TextSpec("Hi", path, "Arial", idx, 8))
    proj = str(tmp_path / "p.stitchwright")
    save_project(proj, doc.to_project({}))
    data = json.load(open(proj, encoding="utf-8"))          # as saved on a Windows PC
    data["texts"]["text1"]["font_path"] = "Z:/Windows/Fonts/arial.ttf"
    json.dump(data, open(proj, "w", encoding="utf-8"))
    monkeypatch.setattr(text, "installed_fonts", lambda: {"Liberation Sans": (path, idx)})
    p = load_project(proj)
    spec = p.texts["text1"]
    assert (spec.font_name, spec.font_path, spec.font_index) == ("Liberation Sans", path, idx)
    assert any("close match Liberation Sans" in w for w in p.warnings)


def replace_text(spec, text):
    from dataclasses import replace
    return replace(spec, text=text)


def test_text_dialog_falls_back_to_the_default_font(window):
    from conftest import any_font
    from stitchwright.dialogs import TextDialog
    from stitchwright.text import TextSpec
    name, path, idx = any_font()
    dlg = TextDialog(window, 1.0, TextSpec("Hi", "Z:/missing/font.ttf", "Gone Font", 0, 8),
                     editing=True)
    try:
        assert dlg.v_font.get() == name
    finally:
        dlg.destroy()
