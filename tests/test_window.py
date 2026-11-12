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
    assert not app.dirty
    app.open(SAMPLE)
    assert not app.dirty                       # opening isn't a change
    app.v_margin.set("4")
    assert app.dirty
    proj = str(tmp_path / "p.svg2pes")
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename", lambda **k: proj)
    assert app.save_project()
    assert not app.dirty
    app.v_pes.set("6")                         # saved in the project too
    assert app.dirty
    app.open(proj)                             # opening a project sets the settings ...
    assert not app.dirty                       # ... but is not itself a change
    assert app.v_margin.get() == "4"
