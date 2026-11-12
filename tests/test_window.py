"""Window behaviour that needs Tk (a desktop session); kept small and fast."""
import os

import pytest

from svg2pes import gui

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
    proj = str(tmp_path / "p.svg2pes")
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename", lambda **k: proj)
    assert app.save_project()
    assert not app.doc.dirty
    app.v_pes.set("6")                         # saved in the project too
    assert app.doc.dirty
    app.open(proj)                             # opening a project sets the settings ...
    assert not app.doc.dirty                       # ... but is not itself a change
    assert app.v_margin.get() == "4"


def test_ctrl_enter_in_the_text_dialog_presses_ok_without_a_new_line(window):
    from conftest import any_font
    from svg2pes.dialogs import TextDialog
    from svg2pes.text import TextSpec
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
