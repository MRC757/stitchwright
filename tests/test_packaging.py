"""Tests: release notes and the Linux package's files."""
import configparser
import importlib.util
import os
import xml.etree.ElementTree as ET

ROOT = os.path.join(os.path.dirname(__file__), "..")
LINUX = os.path.join(ROOT, "packaging", "linux")


def _release_notes():
    spec = importlib.util.spec_from_file_location(
        "release_notes", os.path.join(ROOT, "packaging", "release_notes.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_release_notes_explain_both_downloads_and_carry_the_changelog():
    rn = _release_notes()
    log = "# Changelog\n\n## Unreleased\n\n- soon\n\n## 1.2.3 (2027-01-01)\n\n- **New.** Things.\n\n## 1.2.2\n\n- old\n"
    text = rn.notes("1.2.3", log)
    assert "stitchwright.exe" in text
    assert "sudo apt install ./Downloads/stitchwright_1.2.3_amd64.deb" in text
    assert "- **New.** Things." in text and "old" not in text and "soon" not in text
    assert "blob/v1.2.3/CHANGELOG.md" in text
    assert "What's new" not in rn.notes("9.9.9", log)          # no section: just install notes


def test_released_versions_in_the_changelog_are_found():
    rn = _release_notes()
    log = open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()
    assert "Far fewer jumps" in rn.changelog_section(log, "0.2.6")


def test_desktop_entry_starts_the_window_and_matches_its_class():
    entry = configparser.ConfigParser(interpolation=None)
    entry.optionxform = str
    entry.read(os.path.join(LINUX, "stitchwright.desktop"), encoding="utf-8")
    d = entry["Desktop Entry"]
    assert d["Exec"] == "stitchwright-gui %f" and d["Icon"] == "stitchwright"
    assert d["StartupWMClass"] == "Stitchwright"           # tk.Tk(className=...) in gui.main
    mime = ET.parse(os.path.join(LINUX, "stitchwright-mime.xml")).getroot()
    types = {t.get("type") for t in mime}
    assert types <= set(d["MimeType"].strip(";").split(";"))


def test_deb_control_file_is_complete():
    text = open(os.path.join(LINUX, "control.in"), encoding="utf-8").read()
    fields = dict(line.split(": ", 1) for line in text.splitlines() if line and not line[0].isspace())
    for f in ("Package", "Version", "Architecture", "Maintainer", "Installed-Size", "Depends",
              "Description"):
        assert f in fields, f
    assert fields["Version"] == "@VERSION@" and fields["Architecture"] == "amd64"


def test_linux_launcher_sends_commands_to_the_command_line_and_files_to_the_window(monkeypatch):
    import sys
    spec = importlib.util.spec_from_file_location("launcher", os.path.join(LINUX, "launcher.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import stitchwright.cli
    import stitchwright.gui
    calls = []
    monkeypatch.setattr(stitchwright.gui, "main", lambda a: calls.append(("gui", a)) or 0)
    monkeypatch.setattr(stitchwright.cli, "main", lambda a: calls.append(("cli", a)) or 0)
    for argv in (["/usr/bin/stitchwright"], ["/usr/bin/stitchwright", "Heart.PES"],
                 ["/usr/bin/stitchwright", "convert", "a.svg", "-o", "a.pes"],
                 ["/usr/bin/stitchwright", "--help"], ["/usr/bin/stitchwright-gui", "list"]):
        monkeypatch.setattr(sys, "argv", argv)
        mod.main()
    assert calls == [("gui", []), ("gui", ["Heart.PES"]), ("cli", ["convert", "a.svg", "-o", "a.pes"]),
                     ("cli", ["--help"]), ("gui", ["list"])]
