# PyInstaller build file for the Stitchwright window.  Build with:  .\build_exe.ps1
# (or: pyinstaller --clean --noconfirm stitchwright.spec)  ->  dist\stitchwright.exe
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["stitchwright.pyw"],
    pathex=["."],
    datas=[("stitchwright/assets", "stitchwright/assets")],
    # fontTools imports its font-table modules by name at run time
    hiddenimports=collect_submodules("fontTools.ttLib.tables") + ["HersheyFonts"],
    excludes=["pytest", "matplotlib", "scipy", "pandas", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

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
