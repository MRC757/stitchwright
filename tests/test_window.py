"""Window behaviour that needs Tk (a desktop session); kept small and fast."""
import os

import pytest

from stitchwright import gui

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "samples", "sample.svg")


@pytest.fixture
def app(window):
    a = gui.App(window)
    window.update()
    return a


def test_settings_changes_mark_the_design_modified(app, tmp_path, monkeypatch):
    app.v_margin.set("3")                      # nothing open yet: nothing to save
    assert not app.doc.dirty
    app.open(SAMPLE)
    assert not app.doc.dirty                       # opening isn't a change
    app.v_margin.set("4")
    assert app.doc.dirty
    proj = str(tmp_path / "p.stitchwright")
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename", lambda **k: proj)
    assert app.save_project()
    assert not app.doc.dirty
    app.v_pes.set("6")                         # saved in the project too
    assert app.doc.dirty
    app.open(proj)                             # opening a project sets the settings ...
    assert not app.doc.dirty                       # ... but is not itself a change
    assert app.v_margin.get() == "4"


def test_ctrl_z_undoes_with_caps_lock_on_and_redoes_with_shift(app):
    from types import SimpleNamespace
    from stitchwright.geometry import translate
    app.open(SAMPLE)
    app.apply_transform(translate(5, 0))
    caps_lock = 0x0002

    def press(key, state):
        letter = key.lower()
        app._shortcut(SimpleNamespace(widget=app.canvas, state=gui.CONTROL | state, keysym=key),
                      letter)

    press("Z", caps_lock)                       # Caps Lock: Tk says "Z", but it is Ctrl+Z
    assert not app.doc.undo_stack and len(app.doc.redo_stack) == 1
    press("Z", caps_lock | gui.SHIFT)           # Ctrl+Shift+Z redoes
    assert len(app.doc.undo_stack) == 1 and not app.doc.redo_stack
    press("z", gui.SHIFT)
    assert len(app.doc.undo_stack) == 1         # nothing left to redo: no change


def test_a_real_capital_z_key_event_without_shift_undoes(app):
    """What Tk delivers for Ctrl+Z with Caps Lock on: the keysym Z, no Shift bit."""
    from stitchwright.geometry import translate
    app.open(SAMPLE)
    app.apply_transform(translate(5, 0))
    app.root.deiconify()                      # Tk only delivers key events to a shown window
    app.root.update()
    app.canvas.focus_force()
    app.root.update()
    # (event_generate("<Control-Z>") would add the Shift bit itself: that is Ctrl+Shift+Z)
    app.canvas.event_generate("<KeyPress>", keysym="Z", state=gui.CONTROL | 0x0002, when="now")
    app.root.update()
    app.root.withdraw()
    assert not app.doc.undo_stack and len(app.doc.redo_stack) == 1


def test_every_shortcut_is_bound_in_both_cases(app):
    for letter in app._shortcuts:
        for key in (letter, letter.upper()):
            assert app.root.bind_all(f"<Control-{key}>"), key


def test_title_marks_unsaved_changes(app, tmp_path, monkeypatch):
    from stitchwright.geometry import translate
    app.open(SAMPLE)
    assert app.root.title() == "sample.svg – Stitchwright"
    app.apply_transform(translate(5, 0))
    assert app.root.title() == "• sample.svg – Stitchwright"
    proj = str(tmp_path / "p.stitchwright")
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename", lambda **k: proj)
    assert app.save_project()
    assert app.root.title() == "p.stitchwright – Stitchwright"
    app.v_margin.set("3")                      # a setting saved in the project
    assert app.root.title().startswith("• ")
    app.open(proj)
    assert not app.root.title().startswith("• ")


def test_opening_a_project_selects_its_hoop_preset(app, tmp_path, monkeypatch):
    app.open(SAMPLE)
    app.v_hoop_w.set("130")
    app.v_hoop_h.set("180")
    proj = str(tmp_path / "p.stitchwright")
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename", lambda **k: proj)
    assert app.save_project()
    app.v_preset.set(next(iter(gui.HOOP_PRESETS)))
    app.open(proj)
    assert app.v_preset.get() == "5 × 7 in (130 × 180 mm)"
    app.v_hoop_w.set("123")
    assert app._preset_for_hoop() == "Custom"


def test_save_pes_says_why_it_cannot_save(app):
    app.save()
    assert "Nothing to save" in app.v_status.get()
    app.open(SAMPLE)
    app.v_stitch.set("0.1")                    # refused by the planner
    app.refresh()
    app.save()
    assert app.v_status.get().startswith("Can't save the PES yet: Stitch length")


def _red_square_png(tmp_path):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (60, 60), "white")
    ImageDraw.Draw(img).rectangle((15, 15, 45, 45), fill="red")
    path = str(tmp_path / "pic.png")
    img.save(path)
    return path


def _fake_image_dialog(result):
    class FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def wait(self):
            return result
    return FakeDialog


def test_cancelling_a_picture_open_keeps_the_current_design(app, tmp_path, monkeypatch):
    app.open(SAMPLE)
    before = list(app.doc.order_ids)
    monkeypatch.setattr(gui, "ImageDialog", _fake_image_dialog(None))
    app.open(_red_square_png(tmp_path))
    assert app.doc.order_ids == before and app.doc.svg_path == SAMPLE


def test_opening_a_picture_replaces_the_design_once_traced(app, tmp_path, monkeypatch):
    from stitchwright.image_import import TraceSettings
    app.open(SAMPLE)
    monkeypatch.setattr(gui, "ImageDialog", _fake_image_dialog((TraceSettings(colours=1), True)))
    pic = _red_square_png(tmp_path)
    app.open(pic)
    assert app.doc.order_ids and all(i.startswith("picture") for i in app.doc.order_ids)
    assert app.doc.svg_path == pic


def test_ctrl_enter_in_the_text_dialog_presses_ok_without_a_new_line(window):
    from conftest import any_font
    from stitchwright.dialogs import TextDialog
    from stitchwright.text import TextSpec
    name, path, idx = any_font()
    window.deiconify()                      # key events only reach a window on screen
    dlg = TextDialog(window, 1.0, TextSpec("", path, name, idx, 8), editing=False)
    dlg.update()
    dlg.t_text.insert("1.0", "Fly")
    dlg.t_text.focus_force()
    dlg.update()
    dlg.t_text.event_generate("<Control-Return>")
    window.update()
    assert dlg.result is not None and dlg.result.text == "Fly"


def test_projects_saved_before_the_rename_still_open(app, tmp_path, monkeypatch):
    """Stitchwright was svg2pes: its .svg2pes projects ('svg2pes-project') still open."""
    import json
    new = str(tmp_path / "p.stitchwright")
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename", lambda **k: new)
    app.open(SAMPLE)
    assert app.save_project()
    stitches = app.plan.report.stitch_count
    data = json.load(open(new, encoding="utf-8"))
    assert data["format"] == "stitchwright-project"
    data["format"] = "svg2pes-project"                 # as an older version wrote it
    old = str(tmp_path / "old.svg2pes")
    json.dump(data, open(old, "w", encoding="utf-8"))
    app.new_design(confirm=False)
    app.open(old)
    assert app.doc.order_ids and app.plan.report.stitch_count == stitches
