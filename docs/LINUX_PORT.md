# Linux (Ubuntu) version: plan

Goal: a Linux version of Stitchwright for **Ubuntu (22.04 and later, x86_64)** that installs
as simply as possible, while the Windows app stays exactly as it is. No Raspberry Pi / ARM
builds.

## Decisions

- **One codebase, one repo, `main` branch.** No Linux fork or branch. Every `v*` tag builds
  both downloads and attaches them to the same GitHub Release.
- **Linux download: `stitchwright_<version>_amd64.deb`.** Double-click to install (or
  `sudo apt install ./stitchwright_<version>_amd64.deb`, which always works). It adds a menu
  entry and icon, opens `.stitchwright` and `.pes` files on double-click, and uninstalls
  cleanly with apt.
  - It contains a self-contained PyInstaller build, so it does not depend on Ubuntu's Python
    or `python3-tk`: one `.deb` works on 22.04, 24.04 and later.
  - Built on ubuntu-22.04 (the oldest supported glibc) with `dpkg-deb --build`.
  - Installs to `/opt/stitchwright`, launcher in `/usr/bin/stitchwright-gui`, plus a
    `.desktop` file, icon and MIME types.
- **Not doing:** tarball (an extra download that doesn't help Ubuntu users), Flatpak
  (Ubuntu doesn't include it; Tk isn't in the runtime; the sandbox gets in the way of saving
  to the PE540D's USB stick), AppImage (needs `libfuse2`, which Ubuntu doesn't install).
- **Possibly later:** a Snap in the Snap Store (install from the App Center, automatic
  updates), or an in-app "a newer version is available" check against GitHub Releases.
- **Embroidery output must not change:** a PES made on Linux must be byte-identical to one
  made on Windows from the same input.

## Development environment

WSL2 with WSLg (Ubuntu) on the Windows 11 PC runs the Tk window directly. Before release,
check once on a real Ubuntu desktop (GNOME, Wayland).

## Steps

### A. Run it on Linux and list what fails (½ day)
Set up WSL2 Ubuntu with `python3-venv`, `python3-tk`, `xvfb` and `fonts-liberation`.
Install with `pip install -e ".[dev]"`, run pyflakes and the tests, start the window, and
record every failure or oddity.

### B. Code fixes (1–2 days)

| # | Issue | Where | Fix |
|---|---|---|---|
| 1 | Linux fonts are named by file name (`DejaVuSans-Bold`), and only face 0 of `.ttc` files is read, so projects made on Windows can't find their fonts | `text.py` `installed_fonts()` | Read family and style names from each font's `name` table with fontTools, list every face in `.ttc`/`.otc`, use `fc-list` when available, cache |
| 2 | Font fallbacks only know Windows fonts (Arial, Times New Roman, ...) | `text.py` `match_font()`, `default_font()`; `project.py` `_portable_font()` | Metric-compatible substitutes: Arial → Liberation Sans / Arimo, Times New Roman → Liberation Serif, Courier New → Liberation Mono, then DejaVu |
| 3 | File dialogs are case-sensitive on Linux (`*.pes` hides `Heart.PES`) | `gui.py` `OPEN_TYPES` and the other `filetypes` | Helper that gives both cases (`*.pes *.PES`) |
| 4 | Maximise only on Windows (`state("zoomed")`) | `gui.py` window setup | `attributes("-zoomed", True)` on X11 |
| 5 | Log in `~/.stitchwright` | `log.py` | `$XDG_STATE_HOME/stitchwright` (default `~/.local/state/stitchwright`) |
| 6 | Tk 9 on X11 sends `<MouseWheel>`, not Button-4/5 | `gui.py`, `dialogs.py` wheel bindings | Test on Tk 8.6 and 9; one notch never zooms or scrolls twice |
| 7 | `winfo_fpixels("1i")` often reports 96 dpi on Linux even on 4K screens | `gui.py` `self.ui` | Respect `GDK_SCALE` / `Xft.dpi`; manual scale as a fallback |
| 8 | Tk's default X11 theme looks dated | `gui.py` | `clam` ttk theme on Linux |
| 9 | Opening a design by double-click / drag | — | `.desktop` file `Exec=stitchwright-gui %f` (`main(argv)` already takes a path) |

Plus a test that converts `samples/` and compares the result with checked-in reference files,
so output stays byte-identical on both platforms.

### C. CI (½ day)
Add an `ubuntu-latest` job (Python 3.10, 3.12, 3.14) next to the Windows matrix: install
`python3-tk`, `xvfb`, `fonts-liberation`; run `xvfb-run -a python -m pytest`. Make the
Arial-dependent tests accept the metric-compatible substitute (Liberation Sans) or skip when
neither is installed. Tests for font-name parsing and the dialog patterns.

### D. Packaging (1 day)
- `stitchwright.spec` handles both platforms (the `.ico` and `version_info.txt` are Windows
  only).
- `build_deb.sh` (counterpart to `build_exe.ps1`): `.venv-build`, pinned
  `requirements.txt` + PyInstaller 6.22.3, tests under xvfb, PyInstaller build, then
  `dpkg-deb --build` with `/opt/stitchwright`, `/usr/bin/stitchwright-gui`,
  `stitchwright.desktop`, icon, MIME XML for `.stitchwright` and `.pes`.
- Check early that the bundled Tk has Xft (anti-aliased text). If not, build with the
  distro Python plus `python3-tk`.

### E. Release workflow (½ day)
A `build-deb` job on ubuntu-22.04 alongside `build-exe` (same tag-matches-version check).
One `gh release create` attaches both `stitchwright.exe` and the `.deb`, with notes for both.

### F. Real-world check (½ day)
Real Ubuntu desktop (GNOME/Wayland): install the `.deb`, double-click a `.pes`, fonts,
dialogs, scaling, uninstall. One sew-out on the PE540D from a Linux-made PES.

### G. Docs and release (½ day)
README "Download" section with Windows and Linux subsections, Linux install from source
(`python3-tk`), using the PE540D on Linux (the USB stick mounts under `/media/<user>/...`;
eject before unplugging). CHANGELOG, classifiers, release as 0.3.0.

## Step A findings

First run: GitHub Actions `ubuntu-latest` (Ubuntu 24.04), job `test-linux` in `ci.yml`,
Python 3.10, 3.12 and 3.14, under xvfb (2026-11-15). WSL is not installed on the development
PC yet, so the window itself has not been looked at.

- **Everything except fonts already works.** pyflakes is clean; 152–153 of 155 tests pass,
  including all the Tk window tests (`test_window.py`) and the conversion, fill, pattern and
  project tests.
- **1 failure, the expected one (fixes 1 and 2):** `test_text.py::test_svg_text_is_stitched_with_the_matching_font`.
  SVG `font-family="Arial"` fell back to `DejaVuMathTeXGyre`, a maths font. Two causes,
  both as predicted:
  - fonts are listed by file name (`DejaVuSans`, `DejaVuSans-Bold`), so
    `default_font()`'s list ("DejaVu Sans", "Liberation Sans") never matches;
  - it then takes the first outline font alphabetically, which is the maths font.
- **Skipped:** the Wingdings test (not installed: expected), and on Python 3.10 the lint
  test that needs `tomllib` (3.11+; also skipped on Windows 3.10).
- **Environment:** Tk 8.6.14, ttk themes `clam`, `alt`, `default`, `classic` (so `clam` is
  available for fix 8); xvfb reports 100 dpi; 53 installed fonts found (DejaVu, Lato,
  Liberation, ...).
- **Still to check** (needs WSL or a real desktop): how the window looks, mouse wheel,
  file dialogs with `.PES` files, high-DPI scaling, and whether the bundled Tk has Xft
  (anti-aliased text).

Next: step B, starting with fixes 1 and 2 (font names and fallbacks).
