# svg2pes

Turn simple SVG artwork into Brother **.PES** embroidery files, with a desktop window for
choosing thread colours and previewing the stitches.

Built for the **Brother PE540D** (100 × 100 mm / 4" × 4" hoop). Outlines are sewn as
run stitches, and closed shapes can be filled with fill (tatami) stitches. Satin stitch is
not built yet.

![svg2pes window](docs/screenshot.png)

## Features

- Reads SVG shapes (rect, circle, ellipse, polygon, polyline, path), including transforms
  and viewBox units, at their real size in millimetres.
- Gives each object a thread from Brother's 61-colour thread chart (the *Color Thread
  Conversion Chart* in the machine manual). The machine shows the right colour name and
  thread number on screen.
- Colours that come out as the same thread are sewn together, so the machine never stops
  twice for the same spool.
- Orders the outlines to keep jumps short. Each outline gets tie-in and tie-off stitches,
  and no stitch is shorter than 0.4 mm (very short stitches tend to break the thread).
- Lets you move, resize and rotate objects in the hoop, by dragging in the preview or by
  typing sizes, positions and angles. Undo and redo are included.
- Fills closed shapes with fill stitches (tatami) at the angle and density you choose, in the
  outline's thread or another one. Fills are sewn before outlines, so the outlines cover the
  fill's edges.
- Adds text in any installed font, sewn as letter outlines (or filled).
- Shows the real stitches: zoom in to see each stitch and needle point, or watch a sew-out
  simulation that stitches the design in machine order and stops at each colour change.
- Checks that everything fits inside the hoop and its margin, and checks each saved file by
  reading it back.
- Comes with a desktop window (also as a single Windows `.exe`) and a command line. Both use
  the same conversion code.

See [Future features](#future-features) for what is planned next.

## Windows app (.exe)

`svg2pes.exe` is a single file that runs without Python installed. Double-click it, or drag
an SVG onto it to open that file straight away. The first launch takes a few seconds while
it unpacks.

To build it yourself, run this in PowerShell from the project folder:

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

## Using the window

    python -m svg2pes gui                 # or double-click svg2pes_gui.pyw on Windows

1. **Open SVG…** (Ctrl+O). The design is centred in the hoop (and shrunk if it is too big).
   Each object appears with two swatches: its colour in the SVG and the Brother thread it
   will be sewn in.
2. **Choose threads.** Double-click an object (or select several and press **Choose
   thread…**) to pick one of the 61 threads. Tick *Also use it for…* to recolour every object
   that had the same SVG colour. **Sew / skip** (Space) leaves objects out.
3. **Arrange.** Select what you want to change:
   - drag a box around objects in the preview. Everything entirely inside is selected, and the
     objects that will be caught are highlighted while you drag. Hold Shift to add to the
     selection.
   - or click an object's outline, or click inside a shape (Shift or Ctrl adds to the
     selection)
   - **Select all** (Ctrl+A) for the whole design, Esc for nothing

   Then:
   - drag the selection to move it, and use the arrow keys to nudge 1 mm (Shift: 0.1 mm)
   - drag a corner handle to resize, or the round handle to rotate (Shift: 15° steps)
   - or type an exact size, position or angle in the **Arrange** tab, which also has
     **⟲ 90° / ⟳ 90°**, **Centre in hoop**, **Fit to hoop** and **Reset**

   With nothing selected, the Arrange tab acts on the whole design. Anything outside the
   sewing area is outlined in red, and Save stays off until it is moved back in.
4. **Add text…** (Ctrl+T). Type the text, then pick a font and the letter height (the height
   of the capitals; 8 mm or more sews best). The text appears in the middle of the hoop in
   900 Black. Drag it into place and change its thread like any other object. Double-click
   it to edit, or press **Delete text** to remove it.
5. **Fill shapes.** Select closed shapes (such as the wings of a butterfly) and press
   **Fill…**, or double-click the **Fill** column. Choose the thread (the outline's, or another),
   the angle of the stitch rows, the row spacing (0.4 mm is typical; smaller is denser) and the
   stitch length. Untick *Also sew the outline* for a fill without a border. Open shapes, like
   a line, can't be filled.
6. Check the **Stitching** tab (hoop, margin, stitch length) and the **Sewing order** tab (one
   row per thread change on the machine).
7. **Check the stitches** (see below), then **Save PES…** (Ctrl+S). It suggests a short file
   name without spaces for the USB stick.

Undo and Redo (Ctrl+Z / Ctrl+Y) cover every change in the window.

### Looking at the stitches

The preview is drawn from the stitches that will be saved, not from the SVG.

- **Zoom:** scroll the mouse wheel over the preview to zoom in on the point under the
  pointer, or use **−** / **+** (keys `-` / `+`). Right-drag or middle-drag to pan.
  **Fit** (key `0`) shows the whole hoop again. Zoomed in, stitches are drawn about as thick
  as real thread, with a dot at each needle point (turn the dots off with **Needle
  points**). Thin pink lines are jumps (**Show jumps**).
- **Sew-out:** press **▶ Sew** to watch the design stitched in the order the machine will sew
  it. A red marker shows the needle, and the parts not sewn yet are shown faintly.
  - Choose the speed from the list next to the slider. Drag the slider to jump to any point.
  - **⏮** goes back to the start, and **⏭** shows the finished design.
  - With *Stop at colour changes* ticked, playback pauses at each thread change, like the
    machine, and names the next thread. Press **▶ Sew** to carry on.
  - Zooming and panning work while it plays.

## Using the command line

    python -m svg2pes list design.svg            # objects, ids, colours and nearest threads
    python -m svg2pes palette                    # the 61 Brother threads
    python -m svg2pes convert design.svg -o design.pes --preview design.png \
        --set sun=brother:205 --set badge="Sky Blue" --map "#ffffff=Black" --skip wave

| Option | Meaning |
| --- | --- |
| `--set OBJECT=COLOUR` | colour of one object, by SVG id or by its number from `list` |
| `--map OLD=NEW` | recolour every object that uses one SVG colour |
| `--skip OBJECT` | leave an object out |
| `--width MM` | scale the design to this width |
| `--fit` | shrink the design to fit the hoop if it is too big |
| `--hoop WxH` | hoop size in mm (default `100x100`) |
| `--margin MM` | space kept clear inside the hoop (default 2) |
| `--stitch-len MM` | run-stitch length, 0.5–12 (default 2.5) |
| `--keep-order` | keep the SVG drawing order (usually means more colour changes) |
| `--pes-version 1\|6` | PES format version (default 1, tested on the PE540D) |
| `--no-snap` | keep the exact SVG colours instead of Brother threads |
| `--fill OBJECT[=COLOUR]` | fill a closed shape, in COLOUR or else its own thread |
| `--no-outline OBJECT` | don't sew the outline (for a fill without a border) |
| `--fill-angle DEG` | direction of the fill rows (default 45) |
| `--fill-spacing MM` | distance between fill rows; smaller is denser (default 0.4) |
| `--fill-stitch MM` | fill stitch length (default 3.5) |

COLOUR can be any of these:
- `#rrggbb`
- a colour name from the chart, such as `Red` or `Sky Blue`
- `brother:<thread number>`, the Embroidery number on the spool (`brother:900` is Black)
- `chart:<row>`, the row number (1–61) in the chart

With `--no-snap` the machine shows the nearest Brother colour name for each thread.

## Trying it on the machine

1. Copy `samples/sample.pes` to a FAT32 USB stick. Use a short file name without spaces.
2. Check that it loads, the size reads about 56 × 36 mm, and 5 colour stops appear.
   `samples/sample.pes` has been checked on a PE540D. `samples/sample_v2.pes` is the same design
   made with the current code, with the sun in 214 Deep Gold.
3. Sew a test on scrap fabric with stabiliser. If a file will not load, try PES version 6.

**What has been checked on the machine so far:** a PES v1 file with run-stitch outlines
(`samples/sample.pes`) loads and is read by the PE540D. Fills, added text and arranged
layouts are tested in code and in the preview, but have not been sewn yet. Sew a test
piece before using them on a real project.

## Limitations

- Fills are plain tatami: there is no underlay or pull compensation yet, so use a firm
  stabiliser. On stretchy fabric a fill can pull in slightly from its outline.
- Where filled shapes overlap, both fills are sewn (one on top of the other). The overlap is
  not cut out of the lower fill.
- A fill's row angle is measured in the hoop, so rotating a filled object doesn't turn its
  rows with it. Set the angle in **Fill…** after rotating.
- Satin stitch (for borders and small text) is not built yet.
- Text inside the SVG, images, gradients and opacity are ignored. Use **Add text…** for
  lettering, or convert text to paths in your drawing program.
- Added text uses outline fonts, sewn as letter outlines or filled. Small text (under about
  8 mm) gets crowded, and kerning is not applied.
- There is no project file yet. A PES file can't be opened again for editing, and closing the
  window loses the layout, threads, fills and text. Save the PES before you close.
- The PE540D has no automatic thread cutter, so it most likely ignores the trim codes written
  for long jumps. Snip those threads by hand after sewing.
- SVGs saved at 72 dpi (some Illustrator exports) come out about 25% smaller than intended.
  Use `--width`, or the size boxes in the Arrange tab.
- Grouping by colour changes the sewing order. All fills are sewn before all outlines, but
  overlapping fills in different threads may not follow the SVG's stacking order. Use
  *Keep drawing order* (Stitching tab, or `--keep-order`) when stacking matters. It
  usually adds colour changes.

## Future features

Ideas for later versions, roughly in order of importance. Tick them off as they are done.

### Better sewing quality
- [ ] **Fill underlay:** a light layer of stitches under each fill (an edge run plus sparse
      rows across) so the fill lies flat and doesn't sink into the fabric.
- [ ] **Pull compensation:** widen fills slightly so they don't pull in from their outlines.
- [ ] **Satin stitch:** smooth zig-zag columns for borders, outlines of a set width and
      small lettering.
- [ ] **Cut out overlaps:** cut the area under an upper shape out of the fill below, so
      fills don't pile up into stiff patches.
- [ ] **Travel stitches instead of jumps:** run hidden stitches under a fill to reach the
      next row or shape. The PE540D has no thread cutter, so every jump is a thread to snip.
- [ ] **Smarter sewing order:** let the user drag colour stops into order, and choose the
      order that needs the fewest colour changes and jumps.
- [ ] **Fill angle follows rotation:** keep a fill's row direction relative to its object.

### Designing and editing
- [ ] **Project files:** save and reopen a layout (objects, threads, fills, text) to keep
      working on it later.
- [ ] **Open PES files:** load an existing design to view it, simulate it or recolour it.
- [ ] **Copy, paste and duplicate**, plus **mirror/flip**, **align and distribute**, and
      **group/ungroup**.
- [ ] **Text from the SVG:** stitch `<text>` elements with the matching installed font
      instead of ignoring them.
- [ ] **Single-line (stroke) fonts and monograms** for small, clean lettering.
- [ ] **Text on a curve** (arched names and badges).
- [ ] **Image import:** PNG/JPEG reduced to a few thread colours and traced into shapes.
- [ ] **Hoop presets** (Brother 4" × 4" and others) and an **inches/mm** switch.
- [ ] **Spot the 72 dpi problem:** warn when an SVG's size looks like a 72 dpi export.

### Machine and output
- [ ] **Other formats:** DST, JEF, EXP and VP3 (pyembroidery can write them) for other
      machines.
- [ ] **Thread and time estimates:** thread used per colour and an estimated sewing time.
- [ ] **Printable worksheet:** a page with the design, its size, the colour sequence and
      the thread numbers to have ready at the machine.
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
| `svg2pes/svg_import.py` | reads the SVG into objects made of polylines, in mm |
| `svg2pes/palette.py` | the 61 Brother threads and colour parsing |
| `svg2pes/stitching.py` | outline simplification, needle drops, tie stitches, ordering |
| `svg2pes/convert.py` | sizing, colour choices, stitch planning, PES writing and checking |
| `svg2pes/geometry.py` | move, resize and rotate transforms |
| `svg2pes/fill.py` | fill (tatami) stitches for closed shapes |
| `svg2pes/text.py` | text to outlines with installed fonts |
| `svg2pes/preview.py` | draws the stitch preview (whole hoop or zoomed) and the sew-out |
| `svg2pes/gui.py` | the Tkinter window |
| `svg2pes/cli.py` | the command line |
| `svg2pes/assets/` | app icon |
| `svg2pes.spec`, `build_exe.ps1`, `version_info.txt` | Windows .exe build |

## Tests

    pip install pytest
    python -m pytest tests

## Built with

[pyembroidery](https://github.com/EmbroidePy/pyembroidery) (PES writing),
[svgelements](https://github.com/meerk40t/svgelements) (SVG parsing),
[fontTools](https://github.com/fonttools/fonttools) (fonts), Pillow and NumPy.
Brother and PE540D are trademarks of Brother Industries, Ltd. This project is not affiliated
with Brother.

## License

MIT, see [LICENSE](LICENSE).
