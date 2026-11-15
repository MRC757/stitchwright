# Changelog

## Unreleased

- **More stitch patterns.** Fills can now be sewn as **contour** (rings that follow the
  outline, spiralling inwards), **diamonds** or **waves** (rows whose needle drops line up into
  the pattern) or **criss-cross** (two layers of rows at right angles), as well as plain rows.
  Outlines can be **bean** (triple run), **zigzag** or **blanket** (prongs pointing into the
  shape, for appliqué), as well as run stitch and satin. Choose them in *Fill / outline…*, or
  with `--fill-pattern`, `--pattern-size` and `--outline OBJECT=STYLE[:WIDTH]` on the command
  line. Projects keep them; older versions open such projects with plain rows and satin.
- Joins between fill pieces whose ends almost touch no longer make a stitch shorter than
  0.4 mm.

## 0.2.6 (2026-11-14)

- **Far fewer jumps in fills.** A winding fill (script lettering, a traced monogram) is cut
  into many row pieces. They used to be sewn in the order they were made, top to bottom
  across the whole shape, with a jump between most of them: on the PE540D, which has no
  thread cutter, each jump is a thread to snip by hand. Now the nearest piece that can be
  reached by stitching along the edge is sewn next, and travel along the edge can go up to
  150 mm (was 40 mm). A script "MB" in Edwardian Script went from 64 jumps to 10, for about
  7% more stitches. Planning is no slower. Sewn on the PE540D.

## 0.2.5 (2026-11-13)

- **Font list with previews.** In Add text / Edit text, each font's name is shown in that
  font, there is a search box, and pointing at a font shows your text in it in the preview
  before you choose it (click or arrow keys to choose). Before any text is typed the preview
  shows a sample. Symbol fonts show their pictures, with the name beside them.

## 0.2.4 (2026-11-13)

The PES placement fix has been sewn on the PE540D (a filled shape with lettering, placed
and sewn whole). The 0.2 mm fill spacing and the other machines' formats are tested in code
and in the preview, not yet sewn.

- **Export for other machines** (File → Export for another machine…, Ctrl+E): DST (Tajima,
  read by most machines), EXP (Melco, Bernina), JEF (Janome), VP3 (Pfaff, Husqvarna Viking)
  and XXX (Singer). Each file is read back after writing to check every colour stop is
  there. DST and EXP store no thread colours, so the status bar says how many thread changes
  the machine will ask for. On the command line the output's extension picks the format
  (`-o design.dst`). These formats are centred on their starting point, as their machines
  expect; only PES has been sewn so far.
- **Denser fills by default: rows 0.2 mm apart** instead of 0.4 mm. Sewn on the PE540D, 0.4 mm
  let the fabric show through. A fill takes about 60% more stitches and time. Projects saved
  earlier keep the spacing they were saved with; change it in each object's fill settings.
- **PES files start at the design's top-left corner**, as Brother's own software writes them,
  and the machine places the design. Before, a design placed off-centre on the canvas was
  written from the hoop centre, a point outside the design: a heart placed low in the
  5 × 7 in hoop setting sewed on the PE540D with half of it squashed into a line at the edge
  of the sewing area. Where a design sits on the canvas no longer matters to the machine.
  (The hoop was also set to 5 × 7 in, which the PE540D can't use; the README now says to keep
  it on 4 × 4 in, and what a design squashed at one side means.)

## 0.2.3 (2026-11-12)

Fixes from a code review. The tie and travel fix below has been sewn on the PE540D (filled
script lettering) and sews cleanly; the other changes are tested in code and in the preview.

### Stitches
- **No more tiny stitches at the start of a design or inside fills.** Two causes, found when
  the PE540D shredded the thread in the first stitches of filled lettering:
  - The travel stitches that join a fill's pieces followed the shape's outline point for
    point. A font's or a traced picture's outline has a point every 0.2 mm or so, so each
    travel was a run of 0.2 mm stitches, nearly in the same hole, sewn at full speed. Travel is
    now stitched like a run stitch (2.5 mm, shorter on curves, never under 0.4 mm). The filled
    "MEC" test design drops from 3,037 stitches to 1,439, with no stitch under 0.4 mm.
  - The tie-in and tie-off at each end of every run were 0.5 mm back and forth in the same
    two holes. A tie is now 1.0 mm out, 0.6 mm back and 1.0 mm on along the line, so no two
    needle drops share a hole. Runs too short for a tie are sewn without one.
- **Fills follow SVG's fill rule.** Where two parts of one shape overlap and are drawn the
  same way, the overlap is now filled instead of left as a hole, as SVG and fonts mean it
  (some fonts, such as Bahnschrift, draw many letters this way). A shape marked
  `fill-rule="evenodd"` in the SVG still gets holes in its overlaps. Holes such as the middle
  of an "o" stay empty. Projects remember each shape's rule.
- **Cut-out fills overlap a little**: a fill under a filled shape stacked on top now reaches
  0.4 mm under it, so fabric pulled in by the stitches can't show between the two.
- **Warnings for imported stitches** that have been resized beyond 80–120% (they keep their
  number, so they spread apart or pack together) or that include stitches over 12 mm.
- **Long satin borders plan much faster** (a 1 m curvy border: from over 2 seconds to a
  hundredth of one), so the window keeps up while you arrange them.

### Designing
- **Opening a picture no longer loses the current design** if you cancel the import dialog
  or the picture can't be traced.
- **Phone photos are traced the right way up** (their stored rotation is followed).
- **Keyboard shortcuts work with Caps Lock on.** Before, Ctrl+Z redid, Ctrl+S saved the
  project instead of the PES and Ctrl+G ungrouped, and the other shortcuts did nothing.
- The title bar marks unsaved changes with a dot.
- Opening a project selects its hoop size in the hoop list.
- Save PES (Ctrl+S) says why when it can't save, instead of doing nothing.
- Projects saved by a later version, with settings this one doesn't know, still open.

### Command line
- `--speed 0` and a pull compensation outside 0–1 mm are refused with a plain message
  (`--speed 0` used to crash).

### Building
- The Windows app is built from pinned library versions (`requirements.txt`), so every
  build of a release is the same. The automatic checks run on Python 3.10, 3.12 and 3.14.

## 0.2.2 (2026-11-12)

- Version tags now publish a GitHub Release with `stitchwright.exe` attached.
- The automatic checks use the Node.js 24 versions of the GitHub actions.

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
