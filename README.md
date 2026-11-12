# svg2pes

Turn artwork into Brother **.PES** embroidery files, with a desktop window for arranging the
design, choosing threads, filling shapes, lettering and previewing the stitches.

Built for the **Brother PE540D** (100 × 100 mm / 4" × 4" hoop). It reads SVG artwork,
pictures (PNG/JPEG) and existing embroidery files. It sews run-stitch outlines, satin columns
and filled areas (tatami fill with underlay).

![svg2pes window](docs/screenshot.png)

## Features

**Artwork in**
- SVG shapes (rect, circle, ellipse, polygon, polyline, path) with transforms and viewBox
  units, at their real size in millimetres. SVG `<text>` is stitched with the matching
  installed font.
- Pictures (PNG, JPEG, BMP, GIF), reduced to a few thread colours and traced into shapes.
- Existing embroidery files (PES, DST, JEF, EXP, VP3), to view, simulate, move and recolour.
- Illustrator's 72 dpi SVG exports are recognised and can be scaled to their intended size.

**Threads**
- Brother's 61-colour thread chart (the *Color Thread Conversion Chart* in the machine
  manual). The machine shows the right colour name and thread number on screen.
- Colours that come out as the same thread are sewn together, so the machine never stops
  twice for the same spool.

**Stitches**
- Run-stitch outlines with tie-in and tie-off stitches, and no stitch shorter than 0.4 mm.
- Satin columns for borders, of any width from 0.5 to 8 mm, with a centre-walk underlay.
- Fill (tatami) for closed shapes:
  - you choose the angle, density and stitch length
  - underlay and pull compensation help it lie flat
  - fill hidden under shapes stacked on top is cut out
  - separate pieces are joined by travel stitches instead of jumps
- Fills are sewn before outlines. You can reorder the colour stops yourself.
- A sewing-time estimate for the whole design and for each colour stop.

**Designing**
- Move, resize, rotate, flip, align and space objects, by dragging in the preview or by
  typing numbers.
- Copy, paste, duplicate, delete, group and ungroup, with undo and redo for everything.
- Text in any installed font, or in built-in single-line fonts that stay clean at small
  sizes. Text can be bent along an arc or laid out as a three-letter monogram.
- Project files (`.svg2pes`) save the whole layout so you can come back to it.
- Hoop presets and a millimetres/inches switch.

**Checking**
- A zoomable stitch preview showing each stitch and needle point, and a sew-out simulation
  that stitches the design in machine order and stops at each colour change.
- Checks that everything fits inside the hoop and its margin, and checks each saved PES file
  by reading it back.

There is a desktop window (also as a single Windows `.exe`) and a command line. Both use the
same conversion code. See [Future features](#future-features) for what's planned next.

## Windows app (.exe)

`svg2pes.exe` is a single file that runs without Python installed. Double-click it, or drag
a design onto it to open it straight away. The first launch takes a few seconds while it
unpacks.

To build it yourself, run this in PowerShell from the project folder (close the app first):

    .\build_exe.ps1

The script creates its own build environment (`.venv-build`), runs the tests, and writes
`dist\svg2pes.exe`. If PowerShell blocks scripts, run
`powershell -ExecutionPolicy Bypass -File .\build_exe.ps1`. Unsigned PyInstaller apps sometimes
trigger a Windows SmartScreen or antivirus warning. If so, choose *More info → Run anyway*.

## Install from source

Needs Python 3.10 or newer. Tkinter, which the window uses, comes with Python on Windows and
macOS.

    git clone https://github.com/MRC757/svg2pes.git
    cd svg2pes
    pip install -r requirements.txt
    python -m svg2pes gui                 # or double-click svg2pes_gui.pyw on Windows

## Using the window

### Getting artwork in
- **File → Open…** (Ctrl+O) starts a new design from an SVG, a project (`.svg2pes`), an
  embroidery file (PES, DST, JEF, EXP, VP3) or a picture. The design is centred in the hoop
  and shrunk if it is too big.
- **File → Import into this design…** (Ctrl+I) adds another file's objects to the current
  design. **File → Import picture…** traces a picture: choose the number of thread colours,
  the width, whether to leave out the background, and the detail level. A preview shows the
  result and names the threads. Simple, flat-coloured pictures (logos, clip art) work best.
- **Add text…** (Ctrl+T): type the text, choose a font and the letter height (the height of
  the capitals).
  - The **Single-line** fonts at the top of the list sew each letter as one clean line, which
    is best for small lettering.
  - *Arc* bends the text: 90 arches it up, -90 curves it down.
  - *Monogram* lays out three letters with the middle one larger.
  - Double-click text to edit it.

### Threads, fills and outlines
Each object is listed with two swatches: its colour in the artwork and the Brother thread it
will be sewn in.

- **Choose thread…** (or double-click an object) picks one of the 61 threads. Tick *Also use
  it for…* to recolour every object that had the same artwork colour.
- **Fill / outline…** (or double-click the **Fill** column) sets the stitches for the
  selected objects:
  - **Fill:** on or off; the thread (the outline's, or another); the row angle (it turns
    with the object); row spacing (0.4 mm is typical; smaller is denser); stitch length;
    pull compensation; and underlay.
  - **Outline:** on or off; run stitch or a satin column of a set width.
  - Only closed shapes can be filled.
- **Sew / skip** (Space) leaves objects out without deleting them.

### Arranging
Select objects by dragging a box around them in the preview (Shift adds to the selection), by
clicking an outline or the inside of a shape, or in the list. **Select all** is Ctrl+A, and
Esc selects nothing. Then:

- drag the selection to move it, a corner handle to resize, or the round handle to rotate
  (Shift: 15° steps). Arrow keys nudge 1 mm (Shift: 0.1 mm).
- in the **Arrange** tab, type an exact size, position or angle. The tab also has ⟲/⟳ 90°,
  Centre in hoop, Fit to hoop, Reset, Flip ↔/↕, Align (edges, centres, space evenly), Group
  and Ungroup. With nothing selected it acts on the whole design.
- **Edit** menu: Copy (Ctrl+C), Paste (Ctrl+V), Duplicate (Ctrl+D) and Delete (Del). Group
  is Ctrl+G and Ungroup Ctrl+Shift+G; clicking one member of a group selects the whole
  group.

Anything outside the sewing area is outlined in red, and Save stays off until it is moved back
in. **View → Inches** shows sizes in inches. Undo and Redo (Ctrl+Z / Ctrl+Y) cover every
change.

### Settings and sewing order
- **Stitching** tab:
  - hoop (pick a preset or type a size), margin and run-stitch length
  - *Keep drawing order*: sew in the artwork's order (usually means more colour changes)
  - *Cut fills out under filled shapes stacked on top*
  - machine speed, used for the time estimate
  - PES version
- **Sewing order** tab: one row per thread change, with its stitches and time. Select a stop
  and press **▲ Earlier** / **▼ Later** to change the order. Fills always stay before
  outlines. **Automatic order** goes back to the fewest colour changes. The estimated total
  time is shown here and under the preview.

### Looking at the stitches
The preview is drawn from the stitches that will be saved, not from the artwork.

- **Zoom:** scroll over the preview, or use **−** / **+** (keys `-` / `+`). Right-drag or
  middle-drag pans, and **Fit** (key `0`) shows the whole hoop. Zoomed in, stitches are drawn
  about as thick as real thread, with a dot at each needle point (**Needle points**). Thin
  pink lines are jumps (**Show jumps**).
- **Sew-out:** **▶ Sew** stitches the design in machine order, with a red needle marker and
  the unsewn part shown faintly.
  - Choose the speed, or drag the slider to any point.
  - **⏮** goes back to the start, and **⏭** shows the finished design.
  - *Stop at colour changes* pauses at each thread change and names the next thread.

### Saving
- **Save project** (Ctrl+Shift+S) saves everything to a `.svg2pes` file: outlines,
  positions, threads, fills, text, groups, sewing order and settings. Open it again with
  File → Open. The window asks before closing or opening over unsaved changes.
- **Save PES…** (Ctrl+S) writes the file for the machine and suggests a short name without
  spaces for the USB stick.

## Using the command line

    python -m svg2pes list design.svg            # objects, ids, colours and nearest threads
    python -m svg2pes palette                    # the 61 Brother threads
    python -m svg2pes convert design.svg -o design.pes --preview design.png \
        --set sun=brother:205 --fill wing1 --fill wing2=Yellow --satin border=2.5

| Option | Meaning |
| --- | --- |
| `--set OBJECT=COLOUR` | colour of one object, by SVG id or by its number from `list` |
| `--map OLD=NEW` | recolour every object that uses one SVG colour |
| `--skip OBJECT` | leave an object out |
| `--fill OBJECT[=COLOUR]` | fill a closed shape, in COLOUR or else its own thread |
| `--no-outline OBJECT` | don't sew the outline (for a fill without a border) |
| `--satin OBJECT[=WIDTH]` | sew the outline as a satin column, WIDTH mm (default 2) |
| `--fill-angle DEG` | direction of the fill rows (default 45) |
| `--fill-spacing MM` | distance between fill rows; smaller is denser (default 0.4) |
| `--fill-stitch MM` | fill stitch length (default 3.5) |
| `--pull-comp MM` | added to each end of a fill row (default 0.2) |
| `--no-underlay` | fills without underlay |
| `--no-cutout` | don't cut fills out under filled shapes stacked above |
| `--width MM` | scale the design to this width |
| `--fit` | shrink the design to fit the hoop if it is too big |
| `--hoop WxH` | hoop size in mm (default `100x100`) |
| `--margin MM` | space kept clear inside the hoop (default 2) |
| `--stitch-len MM` | run-stitch length, 0.5–12 (default 2.5) |
| `--keep-order` | keep the SVG drawing order (usually means more colour changes) |
| `--svg-dpi DPI` | the SVG's own units per inch, e.g. 72 for Illustrator exports |
| `--speed SPM` | machine speed for the time estimate (default 650 stitches/min) |
| `--pes-version 1\|6` | PES format version (default 1, tested on the PE540D) |
| `--no-snap` | keep the exact SVG colours instead of Brother threads |

COLOUR can be any of these:
- `#rrggbb`
- a colour name from the chart, such as `Red` or `Sky Blue`
- `brother:<thread number>`, the Embroidery number on the spool (`brother:900` is Black)
- `chart:<row>`, the row number (1–61) in the chart

The command line prints the stitch count, size, estimated sewing time and the thread for each
colour stop.

## Trying it on the machine

1. Copy `samples/sample.pes` to a FAT32 USB stick. Use a short file name without spaces.
2. Check that it loads, the size reads about 56 × 36 mm, and 5 colour stops appear.
   `samples/sample.pes` has been checked on a PE540D. `samples/sample_v2.pes` is the same design
   made with the current code, with the sun in 214 Deep Gold.
3. Sew a test on scrap fabric with stabiliser. If a file will not load, try PES version 6.

**What has been checked on the machine so far:** a PES v1 file with run-stitch outlines
(`samples/sample.pes`) loads and is read by the PE540D. Fills, satin, lettering, imported
pictures and arranged layouts are tested in code and in the preview, but have not been sewn
yet. Sew a test piece before using them on a real project.

## Limitations

- **Satin** works best at 1.5 mm or wider on gentle curves. On very tight curves (smaller than
  the column is wide) it fans out. For small script lettering, use a single-line font with run
  stitch.
- **Fills** are tatami only: there is no contour or radial fill. Very thin areas fill
  poorly; give them a satin outline instead.
- **Overlapping fills:** the area under an upper fill is cut out of the fill below it, but
  shapes that are only outlined don't cut anything out.
- **Text:**
  - Outline-font text gets crowded below about 8 mm.
  - Kerning is not applied.
  - The single-line fonts cover basic Latin letters only.
  - Editing text that came from an SVG drops any rotation it had in the SVG.
- **Pictures:** photos and soft gradients don't trace into good embroidery. Use simple
  flat-coloured artwork.
- **Imported embroidery files** are sewn exactly as they are. They can be moved, rotated,
  recoloured and skipped, but not refilled. Resizing them changes their stitch density.
- **SVG features ignored:** gradients, opacity, clipping masks and pictures embedded inside an
  SVG.
- **Thread cutting:** the PE540D has no automatic cutter, so it most likely ignores the trim
  codes written for long jumps. Snip those threads by hand after sewing.
- **Time estimate:** it assumes the machine averages 85% of the speed you set, plus a minute
  per thread change. Real times vary.

## Future features

Finished items are ticked.

### Better sewing quality
- [x] **Fill underlay:** sparse rows at right angles, kept in from the edge, so the fill lies
      flat.
- [x] **Pull compensation:** fill rows are lengthened slightly at the shape's edge.
- [x] **Satin stitch:** zig-zag columns of a set width for borders, with centre-walk underlay.
- [x] **Cut out overlaps:** fill under filled shapes stacked on top is left out.
- [x] **Travel stitches instead of jumps** between the pieces of a fill.
- [x] **Smarter sewing order:** move colour stops earlier or later, or use the automatic
      order with the fewest colour changes.
- [x] **Fill angle follows rotation.**
- [ ] **Satin lettering:** satin columns that follow the shape of outline-font letters.
- [ ] **Edge-run underlay** and **contour fills** that follow a shape's outline.
- [ ] **Travel stitches between objects** of the same colour, not only within a fill.

### Designing and editing
- [x] **Project files** (`.svg2pes`).
- [x] **Open PES files** (and DST, JEF, EXP, VP3) to view, simulate or recolour them.
- [x] **Copy, paste and duplicate**, **mirror/flip**, **align and distribute**,
      **group/ungroup**.
- [x] **Text from the SVG**, stitched with the matching installed font.
- [x] **Single-line fonts and monograms.**
- [x] **Text on a curve.**
- [x] **Image import:** PNG/JPEG reduced to thread colours and traced into shapes.
- [x] **Hoop presets** and an **inches/mm** switch.
- [x] **Spot the 72 dpi problem** in Illustrator exports.
- [ ] **Edit points:** drag individual points of a shape.
- [ ] **Text along any path**, not only a circular arc.
- [ ] **Picture tracing with smoothing controls** and the option to merge similar colours.

### Machine and output
- [x] **Sewing-time estimates** for the whole design and each colour stop.
- [ ] **Other formats:** DST, JEF, EXP and VP3 output (pyembroidery can write them) for other
      machines.
- [ ] **Thread estimates:** thread used per colour.
- [ ] **Printable worksheet:** a page with the design, its size, the colour sequence and the
      thread numbers to have ready at the machine.
- [ ] **Other thread brands:** pick by Madeira, Sulky or Robison-Anton number, using the
      matching columns of the Brother conversion chart.
- [ ] **Basting box:** an optional outline around the design that holds the fabric and
      stabiliser together while sewing.

### Project and packaging
- [ ] **Automatic tests and builds:** GitHub Actions to run the tests and build the `.exe` on
      every push.
- [ ] **Tests for the window** (selection, arranging, undo) to go with the core tests.
- [ ] **Installer and code signing** for the Windows app, so SmartScreen doesn't warn.
- [ ] **macOS and Linux builds.**

## Project layout

| Path | What it does |
| --- | --- |
| `svg2pes/svg_import.py` | reads SVG into objects made of polylines, in mm (shapes and text) |
| `svg2pes/image_import.py` | traces pictures into thread-colour shapes |
| `svg2pes/pes_import.py` | reads existing embroidery files as objects of exact stitches |
| `svg2pes/palette.py` | the 61 Brother threads and colour parsing |
| `svg2pes/fill.py` | fill (tatami, underlay, pull compensation, cut-outs, travel) and satin |
| `svg2pes/stitching.py` | run stitches, tie stitches, colour blocks and sewing order |
| `svg2pes/convert.py` | sizing, colour choices, stitch planning, time estimate, PES writing |
| `svg2pes/text.py` | text with installed and single-line fonts, arcs, monograms |
| `svg2pes/geometry.py` | move, resize and rotate transforms |
| `svg2pes/project.py` | `.svg2pes` project files |
| `svg2pes/preview.py` | the stitch preview (whole hoop or zoomed) and the sew-out |
| `svg2pes/gui.py` | the Tkinter window |
| `svg2pes/cli.py` | the command line |
| `svg2pes/assets/` | app icon |
| `svg2pes.spec`, `build_exe.ps1`, `version_info.txt` | Windows .exe build |

## Tests

    pip install pytest
    python -m pytest tests

## Built with

[pyembroidery](https://github.com/EmbroidePy/pyembroidery) (embroidery files),
[svgelements](https://github.com/meerk40t/svgelements) (SVG parsing),
[fontTools](https://github.com/fonttools/fonttools) (fonts),
[Hershey-Fonts](https://github.com/apshu/HersheyFonts) (single-line fonts), Pillow and NumPy.
Brother and PE540D are trademarks of Brother Industries, Ltd. This project is not affiliated
with Brother.

## License

MIT, see [LICENSE](LICENSE).
