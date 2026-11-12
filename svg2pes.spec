# PyInstaller build file for the svg2pes window.  Build with:  .\build_exe.ps1
# (or: pyinstaller --clean --noconfirm svg2pes.spec)  ->  dist\svg2pes.exe
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["svg2pes_gui.pyw"],
    pathex=["."],
    datas=[("svg2pes/assets", "svg2pes/assets")],
    # fontTools imports its font-table modules by name at run time
    hiddenimports=collect_submodules("fontTools.ttLib.tables"),
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
    name="svg2pes",
    icon="svg2pes/assets/icon.ico",
    console=False,          # a window app: no black console box
    upx=False,              # UPX-packed exes trip more antivirus false alarms
    version="version_info.txt",
)
