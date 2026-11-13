"""Tests: text and lettering."""
import pytest

from conftest import TEXT_SVG, any_font
from stitchwright.convert import plan_design, write_pes_file
from stitchwright.svg_import import load_svg


# --- text -------------------------------------------------------------------


def test_text_outlines_have_the_requested_height():
    from stitchwright.geometry import bounds
    from stitchwright.text import TextSpec, text_polylines
    name, path, idx = any_font()
    polys, missing = text_polylines(TextSpec("HI", path, name, idx, height_mm=12))
    x0, y0, x1, y1 = bounds([p for pl in polys for p in pl.points])
    assert y1 - y0 == pytest.approx(12, abs=0.3)          # capitals are the letter height
    assert (x0 + x1) / 2 == pytest.approx(0, abs=1e-6) and (y0 + y1) / 2 == pytest.approx(0, abs=1e-6)
    assert all(pl.closed for pl in polys) and missing == ""


def test_text_two_lines_and_missing_characters():
    from stitchwright.geometry import bounds
    from stitchwright.text import TextSpec, text_polylines
    name, path, idx = any_font()
    one, _ = text_polylines(TextSpec("Ab", path, name, idx, 10))
    two, missing = text_polylines(TextSpec("Ab\nAb\u2603", path, name, idx, 10))
    h1 = bounds([p for pl in one for p in pl.points])
    h2 = bounds([p for pl in two for p in pl.points])
    assert (h2[3] - h2[1]) > 1.8 * (h1[3] - h1[1])
    assert missing == "\u2603" and len(two) == 2 * len(one)


def test_text_converts_and_sews(tmp_path):
    from stitchwright.svg_import import Design, DesignObject
    from stitchwright.text import TextSpec, text_polylines
    name, path, idx = any_font()
    polys, _ = text_polylines(TextSpec("Sew", path, name, idx, 10))
    d = Design([DesignObject(1, "text1", "Text", (0, 0, 0), polys, color=(0, 0, 0))], [])
    plan = plan_design(d, placed=True)
    assert plan.report.thread_map[0].thread.number == "900"
    rep = write_pes_file(plan, str(tmp_path / "t.pes"))
    assert rep.stitch_count > 50


def test_bad_font_is_a_clear_error(tmp_path):
    from stitchwright.text import TextSpec, text_polylines
    bad = tmp_path / "bad.ttf"
    bad.write_bytes(b"not a font")
    with pytest.raises(ValueError, match="Cannot read font"):
        text_polylines(TextSpec("A", str(bad)))


# --- lettering --------------------------------------------------------------------


def test_svg_text_is_stitched_with_the_matching_font(tmp_path):
    from stitchwright.geometry import bounds
    any_font()
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
    from stitchwright.geometry import bounds
    from stitchwright.text import TextSpec, installed_fonts, text_polylines
    path, idx = installed_fonts()["Single-line Sans"]
    polys, missing = text_polylines(TextSpec("HELLO", path, "Single-line Sans", idx, 8))
    assert polys and not any(pl.closed for pl in polys) and missing == ""
    x0, y0, x1, y1 = bounds([q for pl in polys for q in pl.points])
    assert y1 - y0 == pytest.approx(8, abs=0.2)


def test_monogram_and_arc_layouts():
    from stitchwright.geometry import bounds
    from stitchwright.text import TextSpec, text_polylines
    name, path, idx = any_font()
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


# --- font previews (each font's name shown in that font) ----------------------------

def test_font_samples_are_drawn_in_the_font(tmp_path):
    from stitchwright.fontpreview import is_symbol_font, sample_image
    from stitchwright.text import SINGLE_LINE_FONTS, HERSHEY_PREFIX
    name, path, idx = any_font()
    img = sample_image(path, idx, name, 30)
    assert img.height == 30 and img.width > 30 and img.getbbox() is not None
    assert not is_symbol_font(path, idx)
    single = HERSHEY_PREFIX + next(iter(SINGLE_LINE_FONTS.values()))
    line = sample_image(single, 0, "Single-line Sans", 30)
    assert line.height == 30 and line.getbbox() is not None
    assert not is_symbol_font(single)
    bad = tmp_path / "broken.ttf"
    bad.write_bytes(b"not a font")
    assert sample_image(str(bad), 0, "Broken", 30) is None        # no picture, no crash
    assert sample_image(path, idx, "   ", 30) is None


def test_symbol_fonts_draw_their_pictures():
    from stitchwright.fontpreview import is_symbol_font, sample_image
    from stitchwright.text import installed_fonts
    fonts = installed_fonts()
    if "Wingdings" not in fonts:
        pytest.skip("Wingdings isn't installed")
    path, idx = fonts["Wingdings"]
    assert is_symbol_font(path, idx)
    img = sample_image(path, idx, "Wingdings", 30)
    assert img is not None and img.getbbox() is not None


def test_text_dialog_lists_fonts_drawn_in_themselves_and_previews_before_choosing(window):
    from stitchwright.dialogs import TextDialog
    from stitchwright.text import TextSpec
    name, path, idx = any_font()
    dlg = TextDialog(window, 1.0, TextSpec("Mia", path, name, idx, 10), editing=False)
    try:
        dlg.update()
        fl = dlg.font_list
        assert fl.selected == name and dlg.v_font.get() == name
        assert any(fl._photos.values())                       # rows on screen are drawn
        assert len(fl._photos) < len(fl.names) or len(fl.names) < 20   # ... and only those
        fl.v_search.set("single-line")                        # search narrows the list
        assert fl.shown and all(n.lower().startswith("single-line") for n in fl.shown)
        other = fl.shown[0]
        fl._set_hover(other)                                  # pointing at a font previews it
        dlg._preview()
        assert dlg.info.cget("text").startswith(f"Preview in {other}")
        assert dlg.v_font.get() == name                       # ... without choosing it
        fl._set_hover(None)
        dlg._preview()
        assert not dlg.info.cget("text").startswith("Preview in")
        fl.select(other)                                      # clicking chooses it
        assert dlg.v_font.get() == other and dlg._spec().font_name == other
        fl._step(1)                                           # so do the arrow keys
        chosen = fl.shown[1]
        assert dlg.v_font.get() == chosen
        fl.v_search.set("no font is called this")             # nothing matches: the choice stays
        assert fl.shown == [] and dlg.v_font.get() == chosen
    finally:
        dlg.destroy()
