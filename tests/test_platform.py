"""Tests: the parts that differ between Windows and Linux (file dialogs, log folder,
screen scale). Each runs on either system by asking for the other one's behaviour."""
import os


def test_file_dialogs_match_upper_case_extensions_on_linux():
    from stitchwright.dialogs import file_types
    types = [("Embroidery files", "*.pes *.dst"), ("All files", "*.*")]
    assert file_types(types, platform="win32") == types        # Windows ignores case anyway
    assert file_types(types, platform="linux") == [("Embroidery files", "*.pes *.PES *.dst *.DST"),
                                                   ("All files", "*")]
    assert file_types([("Project", "*.stitchwright")], platform="linux") == [
        ("Project", "*.stitchwright *.STITCHWRIGHT")]


def test_log_lives_in_the_usual_place_on_each_system(monkeypatch, tmp_path):
    from stitchwright import log
    monkeypatch.setattr(log.sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert log.default_log_dir() == str(tmp_path / "state" / "stitchwright")
    monkeypatch.delenv("XDG_STATE_HOME")
    assert log.default_log_dir() == os.path.join(os.path.expanduser("~"), ".local", "state",
                                                 "stitchwright")
    monkeypatch.setattr(log.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    assert log.default_log_dir() == str(tmp_path / "AppData" / "stitchwright")


def test_screen_scale_can_be_set_for_high_resolution_linux_screens():
    from stitchwright.gui import ui_scale_override
    assert ui_scale_override({}) is None
    assert ui_scale_override({"GDK_SCALE": "2"}) == 2
    assert ui_scale_override({"STITCHWRIGHT_SCALE": "1.5", "GDK_SCALE": "2"}) == 1.5
    assert ui_scale_override({"STITCHWRIGHT_SCALE": "huge", "GDK_SCALE": "2"}) == 2
    assert ui_scale_override({"STITCHWRIGHT_SCALE": "40"}) is None


def test_linux_look_uses_the_clam_theme_and_the_asked_for_scale(tk_root, monkeypatch):
    from tkinter import ttk
    from stitchwright.gui import setup_linux_look
    root = tk_root
    from tkinter import font as tkfont
    from stitchwright.gui import LINUX_UI_FONTS, TK_NAMED_FONTS
    style = ttk.Style(root)
    theme, scaling = style.theme_use(), root.tk.call("tk", "scaling")
    families = {n: tkfont.nametofont(n, root=root).cget("family") for n in TK_NAMED_FONTS}
    monkeypatch.setenv("STITCHWRIGHT_SCALE", "2")
    try:
        setup_linux_look(root)
        assert style.theme_use() == "clam"
        assert abs(float(root.tk.call("tk", "scaling")) - 2 * 96 / 72) < 0.02   # Tk rounds it
        installed = [f for f in LINUX_UI_FONTS if f in tkfont.families(root)]
        family = tkfont.nametofont("TkDefaultFont", root=root).cget("family")
        assert family == (installed[0] if installed else families["TkDefaultFont"])
    finally:
        style.theme_use(theme)
        root.tk.call("tk", "scaling", scaling)
        for n, f in families.items():
            tkfont.nametofont(n, root=root).configure(family=f)
