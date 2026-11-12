# Implementation plan: code-review follow-ups

> **Completed in 0.2.0.** Written before the rename to Stitchwright: `svg2pes/` here is
> now `stitchwright/`, and the commands and files are named to match.

Written for an AI coding agent (Claude Opus) working in this repository in a fresh session.
It assumes no memory of how the code was written. Read the whole plan before starting, then
work through the phases in order. Each task says where the code is, what to change, how to
verify it, and when it is done.

## 1. Orientation

**What this is.** `svg2pes` converts artwork (SVG, pictures, existing embroidery files) into
Brother `.pes` embroidery files for a Brother PE540D, with a Tkinter window (`svg2pes/gui.py`)
and a command line (`svg2pes/cli.py`). Read `README.md` first: it describes every feature and
has a *Future features* checklist that you must keep in step with your work.

**Pipeline** (all in `svg2pes/`):

| Stage | Module | Key names |
| --- | --- | --- |
| Import | `svg_import.py`, `image_import.py`, `pes_import.py`, `text.py` | `load_svg`, `trace_image`, `load_stitches`, `text_polylines` |
| Model | `svg_import.py` | `Design`, `DesignObject` (polylines in mm, `fill`, `satin`, `outline`, `stitches` flags), `Polyline` |
| Stitch planning | `stitching.py`, `fill.py` | `plan_blocks`, `Settings`, `Run`, `ColorBlock`; `fill_paths`, `satin_path`, `FillSpec`, `SatinSpec` |
| Conversion | `convert.py` | `plan_design` (no I/O), `write_pes_file`, `convert` (CLI path), `Report`, `ThreadUse`, `estimate_minutes`, `outside_hoop` |
| Threads | `palette.py` | the 61 Brother chart threads; RGBs must stay exactly equal to pyembroidery's PEC table entries |
| Preview | `preview.py` | `Track`, `View`, `render_view`, `render_image` |
| Transforms | `geometry.py` | 6-tuple affine: `chain`, `translate`, `scale`, `rotate`, `apply`, `bounds` |
| Projects | `project.py` | `.svg2pes` JSON; round trip must be exact |
| Window | `gui.py` | dialogs + one `App` class (~2,000 lines) |

**Coordinate conventions.** Millimetres everywhere in the model; hoop centre is (0, 0), y
points down. `pattern` objects (pyembroidery) are in 0.1 mm units. The window keeps each
object's original outline plus a placement matrix in `App.mats[obj_id]`.

**Running things.**

```powershell
# tests (the build venv already has pytest; the global Python may not)
.\.venv-build\Scripts\python.exe -m pytest tests -q
# if .venv-build does not exist yet:
python -m venv .venv-build; .\.venv-build\Scripts\python.exe -m pip install -r requirements.txt pytest pyinstaller
# the window
python -m svg2pes gui [file]
# the Windows exe (close any running svg2pes.exe first, or the build fails on "Access is denied")
.\build_exe.ps1
```

**Repository rules.**
- Commit after each task with a message that says what changed and why. Follow whatever
  commit attribution guidance your own session gives you.
- Never modify `samples/sample.pes`: it is the only file confirmed to load on the real
  machine. `samples/sample.svg` must keep producing the same stitches (the tests check this
  indirectly via counts).
- `Butterfly.svg`, `Heart.svg`, `*.png` in the project root and `Thread Colors.pdf` are the
  user's files, deliberately untracked. Do not add them to git. `Butterfly.svg` is a good
  manual test file (12 objects, closed wings that can be filled).
- Line-ending warnings from git ("LF will be replaced by CRLF") are normal; ignore them.
- Keep the README's *Future features* list accurate: tick items you finish, add items you
  discover.
- The user's machine has no automatic thread cutter, so jumps matter: never add jumps where
  travel stitches would do.

**Pitfalls learned the hard way.**
- Editing files with shell scripts that contain backslashes (`sed`, heredocs with `\s`,
  Windows paths) corrupts them silently. Use the Edit tool for source changes.
- Tk tests need a display; this is a Windows desktop session, so `tk.Tk()` works. Use
  `root.withdraw()` in tests and `root.update()` to flush events. Drive the canvas with
  `canvas.event_generate("<ButtonPress-1>", x=.., y=..)`; motion events need `state=0x100`
  (button 1 held).
- Screenshots of the window with `ImageGrab` capture anything on top of it (taskbar
  previews once leaked the user's name into `docs/screenshot.png`). If you retake it, use
  `PrintWindow` (see the `grab_window` approach: `user32.PrintWindow(hwnd, hdc, 2)`) and look
  at the image before committing it.
- `project.py` must store coordinates as exact floats; rounding broke the round-trip test
  (stitch counts differed by one).
- `App.refresh()` re-plans the whole design on every change; it is fast (0.03 s for the
  butterfly with fills), so do not add caching unless a profile shows a need.

## 2. Phase A: reliability (do first)

### A1. Crash handler and log file
*Problem:* `gui.py` only catches `ValueError` and `ConversionError`. Any other exception in a
Tk callback is printed to a console the `.exe` does not have, so the app appears to do
nothing.

Steps:
1. Add `svg2pes/log.py` with a `setup_logging()` that configures the `logging` module to
   write to `%LOCALAPPDATA%\svg2pes\svg2pes.log` on Windows (fall back to `~/.svg2pes/` or
   the temp directory elsewhere), rotating at ~1 MB, plus stderr when a console exists.
2. In `gui.main()`, call `setup_logging()`, then set
   `root.report_callback_exception = app.on_exception`.
3. `App.on_exception(exc_type, exc, tb)` logs the traceback, then shows one dialog: a short
   message, the log file path, and "the design is unchanged; use Undo if something looks
   wrong". Guard against dialog storms (ignore repeats within a second).
4. Log at INFO: file opened/imported/saved (path and object count), project saved, PES
   written (stitches, colours). Keep it quiet otherwise.
5. Add a **Help → Open log folder** menu item (open with `os.startfile` on Windows,
   `xdg-open`/`open` elsewhere).

Done when: raising an exception deliberately from a button shows the dialog and the log file
contains the traceback; `python -m svg2pes convert` still prints errors to stderr as before.
Test: a unit test for `setup_logging` (writes to a temp dir passed in) and for the
"no dialog storm" logic (pure function, no Tk).

### A2. Hoop check from the planned stitches
*Problem:* `convert.outside_hoop()` uses outline bounds; satin width and pull compensation
extend stitches beyond the outline, so a border at the margin can sew outside the area.

Steps:
1. In `plan_design`, after `build_pattern`, compute the pattern bounds in mm and store them on
   `Report` (`bounds_mm: tuple`).
2. Change `outside_hoop(design, hoop_mm, margin_mm)` to also accept a plan:
   `outside_hoop(plan_or_design, ...)`. Keep the per-object version (it names the objects),
   and add a design-level check against `report.bounds_mm`. In the window, if the per-object
   check passes but the stitch bounds exceed the sewing area, report "the stitches of X reach
   outside the sewing area" (find X by testing each object's own runs; the per-block
   `thread_map[i].object_ids` is enough to name candidates).
3. The CLI path (`_size_and_centre`) should also refuse to write a PES whose stitch bounds
   exceed the area unless `--fit` is given; `--fit` scales by the stitch bounds, which means
   planning twice (plan, measure, scale, plan again). That is acceptable at these speeds.

Done when: a 2 mm satin rectangle whose outline sits 1 mm inside the margin is flagged, and
the same shape with a run-stitch outline is not. Add both as tests.

### A3. Mark settings changes as modified
*Problem:* changing hoop, margin, stitch length, machine speed, knockout, keep-order or
the colour order does not set `App.dirty`, so closing will not offer to save.

Steps: in `App.__init__`, the `trace_add` callbacks on those variables call
`self.schedule()`; make them also set `self.dirty = True` (but only after the first design
is loaded, so opening a file is not itself "modified"). `move_stop`/`auto_order` go through
`push_undo` already.

Done when: open a file, change the margin, press Ctrl+N → the save prompt appears.

## 3. Phase B: structure

### B1. Extract a `Document` model from `App`
This is the largest task. Goal: all design state and every operation on it live in a plain
Python class with no Tk, and `App` becomes a view/controller. Afterwards the window can be
tested without a display and `gui.py` shrinks by roughly half.

Steps:
1. Create `svg2pes/document.py` with `class Document`. Move these attributes from `App`:
   `svg_map` (rename to `base_objs`), `text_objs`, `extra_objs`, `texts`, `order_ids`,
   `mats`, `home`, `colours`, `skipped`, `fills`, `no_outline`, `satins`, `groups`,
   `color_order`, `warnings`, `svg_path`, `project_path`, `dirty`, and the undo/redo stacks.
2. Move these methods, unchanged in behaviour, and strip them of Tk calls (they return values
   or raise `ValueError` instead of calling `set_status`/`messagebox`):
   `obj`, `chosen_rgb`, `_state`/`_restore`/`push_undo`/`undo`/`redo`, `_fit_matrix`,
   `_start_design` (as `load_design(design, path)`), `_clear_design`, `add_objects`,
   `_forget`/`delete`, `_snapshot`/`copy`/`paste`/`duplicate`, group/ungroup/`expand_groups`,
   `_units`, `apply_transform`, `flip`, `align`, `distribute`, `rotate`, `centre`, `fit`,
   `reset`, `set_fill_outline` (the body of `edit_fill` after the dialog), `set_thread`,
   `toggle_skip`, `add_text`/`edit_text` (given a `TextSpec`), `import_image` (given
   `TraceSettings`), `_placed_fill`, `build_design()` (the loop at the top of `refresh` that
   makes the placed `Design`), `to_project()`/`from_project()`, `move_stop`/`auto_order`
   (given the current `thread_map`).
3. `Document.plan(settings, hoop, margin)` returns `(plan, placed_bounds_by_id, outside)`,
   i.e. what `refresh` needs. Keep `plan_design` in `convert.py` as is.
4. Selection stays in the window (it is view state), but pass the selected ids into the
   document methods.
5. `App` keeps: widget construction, event handlers, dialogs, zoom/pan, sew-out playback,
   drawing. Each handler becomes "gather input → call document → `self.sync()`" where
   `sync()` refills the list, refreshes the plan and redraws.
6. Move the four dialog classes to `svg2pes/dialogs.py`; move canvas hit-testing helpers
   (`_seg_dist`, `_inside`, `_area`) to `geometry.py`.
7. Do it incrementally: move one group of methods, run the existing GUI smoke steps by hand
   (open `Butterfly.svg`, fill the wings, group two objects, drag, undo, save project, reopen),
   commit, repeat. Do not change behaviour while moving code.

Done when: `gui.py` contains no design-state mutation outside `Document` calls; `tests/`
gains `test_document.py` covering: load, select-and-move, resize keeping proportions,
rotate, group selection expansion, copy/paste ids, delete + undo + redo, fill/satin settings,
project round trip through `Document`, and the fill angle following rotation. Run the full
suite; all previous tests still pass.

### B2. Shared geometry helpers
Duplicates to consolidate into `geometry.py`:
- line simplification: `stitching._simplify` (numpy RDP) and `image_import._rdp`
  (iterative RDP) → one `simplify(points, tol)`; keep the iterative one (no recursion limit),
  make sure `_simplify_ring` still splits closed rings before calling it.
- corner cutting: `text._smooth` (open) and `image_import._chaikin_closed` → `chaikin(points,
  rounds, closed)`.
- minimum spacing: `stitching._drop_short` and `fill._drop_close` → `drop_close(points,
  min_len)` (both keep the end points; check the two behave identically on the ends before
  merging, and keep the stricter one).
- `fill._split` (subdivide a segment) is also useful to `text._densify`; share it.

Done when: tests pass unchanged (they cover each caller) and `grep` finds one definition of
each.

### B3. A `Choices` dataclass for per-object options
`convert()`/`plan_design()` take 15 keyword arguments and `apply_color_choices()` 8.
Introduce in `convert.py`:

```python
@dataclass
class Choices:
    overrides: dict[str, str] = field(default_factory=dict)   # object -> colour text
    color_map: dict[str, str] = field(default_factory=dict)
    skip: list[str] = field(default_factory=list)
    fills: dict[str, str | None] = field(default_factory=dict)
    no_outline: list[str] = field(default_factory=list)
    satins: dict[str, float] = field(default_factory=dict)
    fill_spec: FillSpec = FillSpec()
```

`plan_design(design, choices=None, *, width_mm, fit, hoop_mm, margin_mm, settings, snap,
placed)` and `convert(svg_path, out_path, choices=None, *, ...)`. Keep the old keyword
arguments working for one release by accepting them and folding them into a `Choices`
(emit no warning; the tests use them). The CLI builds a `Choices` directly.

### B4. Small clean-ups
- Delete `stitching._order_runs` (unused).
- Replace `getattr(obj, "fill", None)` etc. in `plan_blocks` with attribute access.
- Rename `App.svg_map` → `base_objs` (done in B1 if you do B1 first).
- Remove the four unused imports pyflakes reports in `tests/test_basic.py`; add pyflakes
  (or ruff) to the test step so they do not come back.
- Replace hard-coded `("Segoe UI", 9)` fonts with the Tk default font via
  `tkinter.font.nametofont("TkDefaultFont")`, copying it and changing size/weight as needed.
- Split `tests/test_basic.py` (670 lines) into `test_import.py`, `test_stitching.py`,
  `test_convert.py`, `test_text.py`, `test_project.py`, keeping the shared helpers in
  `conftest.py`.

## 4. Phase C: sewing quality

### C1. Shorter stitches on curves
In `stitching.resample()`, scale the local stitch length by curvature: for each segment of
the simplified outline, estimate the turn angle per mm over the next ~2 mm; use
`stitch_len` on straight runs, falling to `max(min_stitch * 2, 1.0)` mm where the outline
turns 30° or more per stitch. Keep corners (`corner_deg`) as hard anchors as now.
Done when: a 12 mm circle gets ≥ 30 stitches (currently 15) while a 50 mm straight line still
gets 20; add both as tests. Check the sample's stitch count changes are reflected in any test
that asserts exact counts (prefer ranges).

### C2. Travel stitches only where they will be covered
In `fill.fill_paths`, `travel_join` runs along the shape edge on the assumption the outline
covers it. Add a parameter `hidden: bool` (true when the object sews its outline on top).
When false, run the travel path 0.3 mm inside the ring (offset each ring point along the
inward normal; for simplicity, scale the ring toward its centroid by the amount that moves
the nearest edge 0.3 mm) so the stitches sit inside the fill rather than on its edge.
`plan_blocks` passes `obj.outline`. Test: with `outline=False`, no travel point lies within
0.25 mm of the ring.

### C3. Distinct colours for DST blocks
`pes_import.load_stitches`: when the pattern has no thread list (DST, EXP), assign each block
a colour from a fixed sequence of visually distinct Brother chart threads (e.g. Black, Red,
Blue, Deep Gold, Leaf Green, Violet, Pumpkin, Pewter, repeating) and add a warning "DST files
carry no colours; placeholders were assigned". Test with a DST written by
`pyembroidery.write_dst` from a 3-colour pattern: three objects, three different colours.

## 5. Phase D: project hygiene

### D1. `pyproject.toml`
Add a `pyproject.toml` (setuptools or hatchling) with the package metadata, dependencies
from `requirements.txt`, `[project.scripts] svg2pes = "svg2pes.cli:main"`, and
`[project.gui-scripts] svg2pes-gui = "svg2pes.gui:main"`. Include `svg2pes/assets/*` as
package data. Remove the `sys.path.insert` from the tests and `svg2pes_gui.pyw` once
`pip install -e .` works. Update README install steps. Verify `pip install .` in a fresh
venv and that `svg2pes palette` runs.

### D2. Version and release
Bump `svg2pes/__init__.py` and `version_info.txt` to 0.2.0; read the version in
`pyproject.toml` from `__init__.py` (dynamic version) so there is one source. Tag `v0.2.0`.
Add a `CHANGELOG.md` summarising the features since 0.1.0 (the git log has the details).
Do not push or create the GitHub release yourself; tell the user the commands
(`gh repo create …`, `gh release create v0.2.0 dist/svg2pes.exe`).

### D3. GitHub Actions
`.github/workflows/ci.yml`: on push and pull request, on `windows-latest` (Tk and fonts are
needed for the tests), set up Python 3.12, `pip install -e . pytest pyflakes`, run pyflakes
and pytest. A second job on tags `v*` runs `build_exe.ps1` and uploads `dist/svg2pes.exe` as
an artifact (attaching it to a release can come later). The text tests call `_font()`,
which skips if no fonts are installed; Windows runners have Arial.

### D4. Portable project files
`project.py` stores absolute font paths in `TextSpec`. On save, also store the font *name*
(already in `font_name`). On load, if `font_path` does not exist, look the name up in
`text.installed_fonts()`; if it is a `hershey:` path it is always valid; otherwise keep the
missing path and add a warning to `Project.warnings` ("font X is not installed here; the text
keeps its stitched outlines but editing it will use the default font"). `App.edit_text` should
fall back to `default_font()` when the path is missing instead of raising.

## 6. Order and checkpoints

| Order | Task | Rough size | Checkpoint |
| --- | --- | --- | --- |
| 1 | A1 crash handler | small | dialog + log file seen |
| 2 | A2 hoop check | small | two new tests |
| 3 | A3 dirty flag | tiny | manual check |
| 4 | B4 clean-ups (except rename) | small | pyflakes clean |
| 5 | B2 geometry helpers | small | suite green |
| 6 | B3 Choices | small | suite green, CLI unchanged |
| 7 | B1 Document split | large | `test_document.py`, manual smoke |
| 8 | C1, C2, C3 | small each | new tests |
| 9 | D1, D2, D4 | small each | `pip install .` works |
| 10 | D3 CI | small | workflow file (runs after the user pushes) |
| 11 | Rebuild the exe, retake the screenshot if the UI changed, update README | small | `build_exe.ps1` green |

After everything: run the full suite, build the `.exe`, open `Butterfly.svg` in it, fill the
wings, add text, save a project, reopen it, save a PES, and confirm the log file has the
INFO lines. Then write a short summary for the user listing what changed, what was tested,
and that nothing new has been sewn on the machine.
