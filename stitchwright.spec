# PyInstaller build file for Stitchwright.
#   Windows:  .\build_exe.ps1   ->  dist\stitchwright.exe (one file, no console window)
#   Linux:    ./build_deb.sh    ->  dist/stitchwright/ (a folder, packed into the .deb as
#                                   /opt/stitchwright; starts faster than one file)
# (or: pyinstaller --clean --noconfirm stitchwright.spec)
# -*- mode: python ; coding: utf-8 -*-
import sys

from PyInstaller.utils.hooks import collect_submodules

WINDOWS = sys.platform == "win32"

a = Analysis(
    # Linux: one program for both the window and the command line (see the launcher)
    ["stitchwright.pyw" if WINDOWS else "packaging/linux/launcher.py"],
    pathex=["."],
    datas=[("stitchwright/assets", "stitchwright/assets")],
    # fontTools imports its font-table modules by name at run time; Pillow's ImageTk needs
    # _tkinter_finder on Linux (without it every picture in the window fails)
    hiddenimports=collect_submodules("fontTools.ttLib.tables") + ["HersheyFonts",
                                                                  "PIL._tkinter_finder"],
    excludes=["pytest", "matplotlib", "scipy", "pandas", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if WINDOWS:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="stitchwright",
        icon="stitchwright/assets/icon.ico",
        console=False,          # a window app: no black console box
        upx=False,              # UPX-packed exes trip more antivirus false alarms
        version="version_info.txt",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="stitchwright",
        console=True,           # the command line needs its output; the window is unaffected
        upx=False,
        strip=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, upx=False, strip=False, name="stitchwright")
