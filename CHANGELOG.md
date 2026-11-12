# Changelog

## 0.2.1 (2026-11-12)

- **Renamed to Stitchwright** (it was svg2pes): the package and its commands (`stitchwright`,
  `stitchwright-gui`), the Windows app (`stitchwright.exe`, double-click `stitchwright.pyw`),
  the window title and the GitHub repository. Project files are now `.stitchwright`;
  `.svg2pes` projects saved by earlier versions still open. The log moved to
  `%LOCALAPPDATA%\stitchwright\stitchwright.log`.

## 0.2.0 (2026-11-12)

Everything since the first milestone. Only `samples/sample.pes` (run-stitch outlines, PES
version 1) has been confirmed on a Brother PE540D; the features below are tested in code and
in the preview but have not been sewn yet.

### Stitches
- **Fill (tatami)** for closed shapes, with underlay, pull compensation, cut-outs under filled
  shapes stacked on top, and travel stitches between a fill's pieces instead of jumps. Travel
  tucks 0.3 mm inside a fill that has no outline to cover it.
- **Satin columns** for outlines, with a centre-walk underlay; split at sharp corners.
- **Shorter stitches on curves**: run stitches shorten with curvature (down to 1 mm), so small
  curves no longer sew as polygons. Straight runs are unchanged.
- **Sewing order** can be changed stop by stop (fills always stay before outlines).
- **Sewing-time estimate** for the design and each colour stop.

### Designing
- Window: move, resize, rotate, flip, align and space objects by dragging or typing; box
  selection, copy, paste, duplicate, delete, group, ungroup; undo and redo; a menu bar.
- **Lettering**: text in installed fonts or built-in single-line fonts, on an arc or as a
  monogram; SVG `<text>` is stitched with the matching font.
- **Picture import**: PNG/JPEG reduced to thread colours and traced into filled shapes.
- **Embroidery files** (PES, DST, JEF, EXP, VP3) open to view, simulate, move and recolour;
  files without colours (DST, EXP) get distinct placeholder threads.
- **Project files** (`.svg2pes`) save the whole layout; fonts are found by name when a
  project is opened on another computer.
- Zoomable stitch preview and a sew-out simulation; hoop presets; millimetres or inches;
  Illustrator 72 dpi exports are spotted and can be scaled.

### Reliability
- Errors in the window are logged to `%LOCALAPPDATA%\svg2pes\svg2pes.log` and shown in a
  dialog instead of failing silently (Help → Open log folder).
- The hoop check measures the planned stitches, so satin and fill edges that reach past an
  outline are caught; the command line refuses them or, with `--fit`, shrinks to fit.
- Changing a setting marks the design as modified, so closing offers to save it.
- A design exactly as wide as the sewing area is no longer refused by a rounding error.

### For developers
- `pip install .` installs the `svg2pes` and `svg2pes-gui` commands (`pyproject.toml`).
- The design state lives in `svg2pes/document.py` (no user interface), tested directly;
  dialogs are in `svg2pes/dialogs.py`; shared geometry helpers in `svg2pes/geometry.py`.
- `convert()` and `plan_design()` take a `Choices` value for per-object options (the old
  separate keywords still work in this release).
- Tests are split by topic, include a pyflakes check, and run on GitHub Actions; version tags
  build the Windows `.exe`.

## 0.1.0 (2026-11-11)

First milestone: SVG artwork to Brother PES with run-stitch outlines, the 61-colour Brother
thread chart, a window with a thread picker and stitch preview, and a Windows `.exe`.
