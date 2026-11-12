"""Tests: reading SVG artwork and pictures."""
import pytest

from conftest import SAMPLE, write_svg
from stitchwright.convert import convert
from stitchwright.svg_import import load_svg


# --- SVG import ------------------------------------------------------------

def test_reads_objects_in_mm():
    d = load_svg(SAMPLE)
    ids = [o.obj_id for o in d.objects]
    assert ids == ["badge", "sun", "letterF", "star", "wave"]
    x0, y0, x1, y1 = d.bounds()
    assert (x1 - x0) == pytest.approx(56.0, abs=0.05)
    assert (y1 - y0) == pytest.approx(36.0, abs=0.05)


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


# --- picture import ---------------------------------------------------------------

def test_picture_is_traced_into_coloured_shapes(tmp_path):
    from PIL import Image, ImageDraw
    from stitchwright.fill import closed_rings
    from stitchwright.geometry import bounds
    from stitchwright.image_import import TraceSettings, trace_image
    img = Image.new("RGB", (200, 100), (240, 240, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 90, 90], fill=(200, 30, 40))
    d.rectangle([40, 40, 60, 60], fill=(240, 240, 240))          # a hole
    d.ellipse([110, 10, 190, 90], fill=(30, 80, 170))
    p = tmp_path / "p.png"
    img.save(p)
    objs, warnings = trace_image(str(p), TraceSettings(colours=2, width_mm=100))
    assert not warnings and len(objs) == 2
    red = next(o for o in objs if o.source_color[0] > 150)
    assert len(closed_rings(red.polylines)) == 2                  # outside and the hole
    x0, y0, x1, y1 = bounds([q for pl in red.polylines for q in pl.points])
    assert (x1 - x0) == pytest.approx(40.5, abs=1.5)              # 81 of 200 px at 100 mm
    blue = next(o for o in objs if o.source_color[2] > 150)
    assert all(pl.closed for pl in blue.polylines)


def test_photos_stored_sideways_are_turned_upright(tmp_path):
    from PIL import Image
    from stitchwright.image_import import _prepare
    img = Image.new("RGB", (40, 20), "red")
    exif = Image.Exif()
    exif[0x0112] = 6                     # Orientation: turn 90 degrees to view
    p = tmp_path / "phone.jpg"
    img.save(p, exif=exif)
    assert _prepare(str(p)).size == (20, 40)


def test_illustrator_72dpi_exports_are_spotted_and_scaled(tmp_path):
    p = tmp_path / "ai.svg"
    p.write_text('<?xml version="1.0"?>\n<!-- Generator: Adobe Illustrator 27.0.0 -->\n'
                 '<svg xmlns="http://www.w3.org/2000/svg" width="144px" height="72px" '
                 'viewBox="0 0 144 72"><rect width="144" height="72" fill="none" stroke="#000"/></svg>')
    d = load_svg(str(p))
    assert d.dpi_hint == 72 and any("72 dpi" in w for w in d.warnings)
    rep = convert(str(p), str(tmp_path / "o.pes"), svg_dpi=72)
    assert rep.width_mm == pytest.approx(50.8, abs=0.2)          # 2 inches
    assert load_svg(SAMPLE).dpi_hint is None                     # sized in mm: fine
