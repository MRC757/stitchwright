"""Desktop window for svg2pes: open an SVG, arrange objects, add text, choose
Brother threads, preview the stitches and save PES.

Run with:  python -m svg2pes gui [design.svg]   (or double-click svg2pes_gui.pyw)

Objects are placed in hoop coordinates: millimetres, hoop centre at (0, 0),
y pointing down. Each object keeps its original outline plus a transform
(self.mats) that places it in the hoop, so moving, resizing and rotating never
degrade the artwork and can be undone.
"""
from __future__ import annotations

import math
import os
from dataclasses import replace
import re
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from .convert import (ConversionError, Plan, format_minutes, outside_hoop, plan_design,
                      write_pes_file)
from .geometry import IDENTITY, Affine, apply, bounds, chain, rotate, scale, translate
from .palette import Thread, brother_threads, nearest_brother, thread_for_rgb, to_hex
from .preview import CHANGE, OUTSIDE, Style, Track, View, draw_track, render_view
from .stitching import Settings
from .svg_import import Design, DesignObject, Polyline, load_svg
from .fill import FillSpec, SatinSpec, can_fill
from .pes_import import READ_EXTENSIONS, load_stitches
from .project import EXTENSION as PROJECT_EXTENSION
from .project import Project, load_project, save_project
from .text import TextSpec, default_font, installed_fonts, text_polylines

TITLE = "svg2pes – SVG to Brother PES"
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".gif")
OK_COLOUR, WARN_COLOUR, ERROR_COLOUR = "#2e7d32", "#9a6700", "#c62828"
SELECT_COLOUR = "#1a73e8"
SHIFT, CONTROL = 0x0001, 0x0004
SIM_SPEEDS = {"Slow": 15, "Normal": 60, "Fast": 250, "Very fast": 1200}   # stitches per second
SIM_FRAME_MS = 33
NEEDLE_COLOUR = "#d32f2f"
MAX_ZOOM = 60.0
TEXT_WIDGETS = ("Entry", "TEntry", "Text", "TCombobox", "TSpinbox", "Spinbox")


def swatch(master, colours: list[str], size: int, gap: int = 6) -> tk.PhotoImage:
    """A small image with one bordered square per colour, side by side."""
    w = size * len(colours) + gap * (len(colours) - 1)
    img = tk.PhotoImage(master=master, width=w, height=size)
    x = 0
    for c in colours:
        img.put("#777777", to=(x, 0, x + size, size))
        img.put(c, to=(x + 1, 1, x + size - 1, size - 1))
        x += size + gap
    return img


def _centre_on(win: tk.Toplevel, parent):
    win.update_idletasks()
    px = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_width()) // 2
    py = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_height()) // 3
    win.geometry(f"+{max(px, 0)}+{max(py, 0)}")


def _seg_dist(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def _inside(p, pts) -> bool:
    x, y = p
    inside = False
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _area(pts) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:]))) / 2


# --------------------------------------------------------------------- dialogs
class ThreadPicker(tk.Toplevel):
    """Modal grid of the 61 chart threads. After wait(), .result is a Thread,
    the string "reset" (back to the nearest match), or None (cancelled)."""

    COLS = 4

    def __init__(self, parent, ui: float, title: str, current: Thread | None,
                 source_hex: str | None, others_same_colour: int):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.result: Thread | str | None = None
        self.apply_all = tk.BooleanVar(value=False)

        threads = brother_threads()
        rows = math.ceil(len(threads) / self.COLS)
        cw, ch = int(190 * ui), int(26 * ui)
        cv = tk.Canvas(self, width=self.COLS * cw, height=rows * ch, bg="white",
                       highlightthickness=0, cursor="hand2")
        cv.pack(padx=10, pady=(10, 4))
        self.info = ttk.Label(self, text="Click a thread to use it.")
        self.info.pack(anchor="w", padx=10)

        self._bg: dict[int, int] = {}
        for i, t in enumerate(threads):
            col, row = divmod(i, rows)          # column by column, like the chart
            x, y = col * cw, row * ch
            tag = f"c{i}"
            selected = current is not None and t.chart == current.chart
            self._bg[i] = cv.create_rectangle(
                x + 1, y + 1, x + cw - 2, y + ch - 2, fill="white",
                outline=SELECT_COLOUR if selected else "", width=2, tags=(tag,))
            cv.create_rectangle(x + 5, y + 4, x + 5 + int(30 * ui), y + ch - 4,
                                fill=to_hex(t.rgb), outline="#777777", tags=(tag,))
            cv.create_text(x + int(42 * ui), y + ch / 2, anchor="w", tags=(tag,),
                           text=f"{t.number}  {t.name}",
                           font=("Segoe UI", 9, "bold") if selected else ("Segoe UI", 9))
            cv.tag_bind(tag, "<Enter>", lambda e, i=i, t=t: self._hover(cv, i, t))
            cv.tag_bind(tag, "<Leave>", lambda e, i=i: cv.itemconfigure(self._bg[i], fill="white"))
            cv.tag_bind(tag, "<Button-1>", lambda e, t=t: self._done(t))

        bottom = ttk.Frame(self, padding=(10, 6, 10, 10))
        bottom.pack(fill="x")
        if others_same_colour:
            ttk.Checkbutton(
                bottom, variable=self.apply_all,
                text=f"Also use it for the {others_same_colour} other object(s) "
                     f"coloured {source_hex} in the SVG").pack(anchor="w", pady=(0, 6))
        ttk.Button(bottom, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text="Back to nearest match",
                   command=lambda: self._done("reset")).pack(side="right", padx=6)
        self.bind("<Escape>", lambda e: self.destroy())
        _centre_on(self, parent)

    def _hover(self, cv, i, t: Thread):
        cv.itemconfigure(self._bg[i], fill="#e8f0fe")
        self.info.configure(text=f"Chart row {t.chart}  ·  Embroidery {t.number}  ·  "
                                 f"Country {t.country}  ·  {t.name}")

    def _done(self, result):
        self.result = result
        self.destroy()

    def wait(self):
        self.grab_set()
        self.focus_set()
        self.wait_window()
        return self.result


class TextDialog(tk.Toplevel):
    """Modal dialog to type text and choose its font and size, with a live
    outline preview. After wait(), .result is a TextSpec or None."""

    def __init__(self, parent, ui: float, spec: TextSpec | None, editing: bool):
        super().__init__(parent)
        self.title("Edit text" if editing else "Add text")
        self.transient(parent)
        self.resizable(False, False)
        self.ui = ui
        self.result: TextSpec | None = None
        self.fonts = dict(installed_fonts())
        self.names = list(self.fonts)
        self._job = None

        if spec is None:
            d = default_font()
            spec = TextSpec("", d[1] if d else "", d[0] if d else "", d[2] if d else 0)
        if spec.font_name and spec.font_name not in self.fonts and spec.font_path:
            self.fonts[spec.font_name] = (spec.font_path, spec.font_index)
            self.names.append(spec.font_name)

        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Text").grid(row=0, column=0, sticky="nw", pady=2)
        self.t_text = tk.Text(f, width=44, height=3, wrap="none", undo=True,
                              font=("Segoe UI", 11))
        self.t_text.grid(row=0, column=1, columnspan=3, sticky="we", pady=2)
        self.t_text.insert("1.0", spec.text)

        self.v_font = tk.StringVar(value=spec.font_name)
        ttk.Label(f, text="Font").grid(row=1, column=0, sticky="w", pady=2)
        self.c_font = ttk.Combobox(f, textvariable=self.v_font, values=self.names, width=34)
        self.c_font.grid(row=1, column=1, columnspan=2, sticky="we", pady=2)
        ttk.Button(f, text="Font file…", command=self._browse).grid(row=1, column=3, sticky="w", padx=(6, 0))

        self.v_height = tk.StringVar(value=f"{spec.height_mm:g}")
        self.v_spacing = tk.StringVar(value=f"{spec.spacing_mm:g}")
        self.v_align = tk.StringVar(value=spec.align)
        self.v_arc = tk.StringVar(value=f"{spec.arc:g}")
        self.v_mono = tk.BooleanVar(value=spec.monogram)
        ttk.Label(f, text="Letter height (mm)").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(f, textvariable=self.v_height, width=7).grid(row=2, column=1, sticky="w")
        ttk.Label(f, text="height of the capital letters; 8 mm or more sews best",
                  foreground="#666666").grid(row=2, column=2, columnspan=2, sticky="w")
        ttk.Label(f, text="Letter spacing (mm)").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Entry(f, textvariable=self.v_spacing, width=7).grid(row=3, column=1, sticky="w")
        ttk.Label(f, text="Alignment").grid(row=4, column=0, sticky="w", pady=2)
        af = ttk.Frame(f)
        af.grid(row=4, column=1, columnspan=3, sticky="w")
        for val, label in (("left", "Left"), ("center", "Centre"), ("right", "Right")):
            ttk.Radiobutton(af, text=label, value=val, variable=self.v_align).pack(side="left", padx=(0, 8))
        ttk.Label(f, text="Arc (°)").grid(row=5, column=0, sticky="w", pady=2)
        arcf = ttk.Frame(f)
        arcf.grid(row=5, column=1, columnspan=3, sticky="w")
        ttk.Entry(arcf, textvariable=self.v_arc, width=7).pack(side="left")
        ttk.Label(arcf, text="  0 = straight; 90 arches up, -90 curves down",
                  foreground="#666666").pack(side="left")
        ttk.Checkbutton(f, text="Monogram: three letters, the middle one larger",
                        variable=self.v_mono).grid(row=6, column=0, columnspan=4, sticky="w", pady=2)
        ttk.Label(f, foreground="#666666", text="Tip: the Single-line fonts (top of the list) sew "
                  "each letter as one clean line: best for small lettering.").grid(
            row=7, column=0, columnspan=4, sticky="w")

        self.cv = tk.Canvas(f, width=int(480 * ui), height=int(150 * ui), bg="white",
                            highlightthickness=1, highlightbackground="#cccccc")
        self.cv.grid(row=8, column=0, columnspan=4, pady=(8, 2))
        self.info = ttk.Label(f, text="", foreground="#555555")
        self.info.grid(row=9, column=0, columnspan=4, sticky="w")

        b = ttk.Frame(f)
        b.grid(row=10, column=0, columnspan=4, sticky="e", pady=(8, 0))
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(b, text="OK", command=self._ok).pack(side="right", padx=6)

        self.t_text.bind("<KeyRelease>", lambda e: self._schedule())
        self.c_font.bind("<KeyRelease>", self._filter)
        self.c_font.bind("<<ComboboxSelected>>", lambda e: self._schedule())
        for v in (self.v_height, self.v_spacing, self.v_align, self.v_arc, self.v_mono):
            v.trace_add("write", lambda *a: self._schedule())
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-Return>", lambda e: self._ok())
        _centre_on(self, parent)
        self._preview()

    def _filter(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        typed = self.v_font.get().lower()
        self.c_font.configure(values=[n for n in self.names if typed in n.lower()] or self.names)
        self._schedule()

    def _browse(self):
        path = filedialog.askopenfilename(
            parent=self, title="Choose a font file",
            filetypes=[("Fonts", "*.ttf *.otf *.ttc *.otc"), ("All files", "*.*")])
        if path:
            name = os.path.splitext(os.path.basename(path))[0]
            self.fonts[name] = (path, 0)
            if name not in self.names:
                self.names.append(name)
            self.v_font.set(name)
            self._schedule()

    def _spec(self) -> TextSpec:
        name = self.v_font.get().strip()
        if name not in self.fonts:
            raise ValueError("Choose a font from the list")
        try:
            height = float(self.v_height.get())
            spacing = float(self.v_spacing.get() or 0)
        except ValueError:
            raise ValueError("Letter height and spacing must be numbers") from None
        if height <= 0:
            raise ValueError("Letter height must be more than 0")
        path, idx = self.fonts[name]
        try:
            arc = float(self.v_arc.get() or 0)
        except ValueError:
            raise ValueError("Arc must be a number of degrees") from None
        if not -300 <= arc <= 300:
            raise ValueError("Arc must be between -300 and 300 degrees")
        return TextSpec(self.t_text.get("1.0", "end-1c"), path, name, idx, height,
                        self.v_align.get(), spacing, arc=arc, monogram=self.v_mono.get())

    def _schedule(self):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(150, self._preview)

    def _preview(self):
        self._job = None
        cv = self.cv
        cv.delete("all")
        try:
            spec = self._spec()
            polys, missing = text_polylines(spec) if spec.text.strip() else ([], "")
        except ValueError as e:
            self.info.configure(text=str(e), foreground=ERROR_COLOUR)
            return
        if not polys:
            self.info.configure(text="Type the text to add.", foreground="#555555")
            return
        pts = [p for pl in polys for p in pl.points]
        x0, y0, x1, y1 = bounds(pts)
        cw, ch = int(cv["width"]), int(cv["height"])
        k = min((cw - 20) / max(x1 - x0, 1e-6), (ch - 20) / max(y1 - y0, 1e-6))
        for pl in polys:
            cv.create_line(*[c for x, y in pl.points
                             for c in (cw / 2 + x * k, ch / 2 + y * k)], fill="#222222")
        msg = f"{x1 - x0:.1f} × {y1 - y0:.1f} mm"
        colour = "#555555"
        if missing:
            msg += f"   ·   not in this font: {missing}"
            colour = WARN_COLOUR
        self.info.configure(text=msg, foreground=colour)

    def _ok(self):
        try:
            spec = self._spec()
        except ValueError as e:
            self.info.configure(text=str(e), foreground=ERROR_COLOUR)
            return
        if not spec.text.strip():
            self.info.configure(text="Type the text to add.", foreground=ERROR_COLOUR)
            return
        self.result = spec
        self.destroy()

    def wait(self):
        self.grab_set()
        self.t_text.focus_set()
        self.wait_window()
        return self.result


class FillDialog(tk.Toplevel):
    """Modal dialog for an object's fill and outline stitches. After wait(),
    .result is None (cancelled) or a dict with keys fill (FillSpec or None),
    outline (bool) and satin (SatinSpec or None)."""

    def __init__(self, parent, ui: float, title: str, spec: FillSpec | None, outline: bool,
                 outline_rgb, satin: SatinSpec | None = None, can_fill_shape: bool = True):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.ui = ui
        self.result = None
        self.can_fill_shape = can_fill_shape
        base = spec or FillSpec()
        self.other_rgb = base.color or outline_rgb
        # ticked if it already has a fill, or if it is a closed shape not yet given a satin border
        self.v_on = tk.BooleanVar(value=spec is not None or (can_fill_shape and satin is None))
        self.v_same = tk.BooleanVar(value=base.color is None)
        self.v_angle = tk.StringVar(value=f"{base.angle:g}")
        self.v_spacing = tk.StringVar(value=f"{base.spacing:g}")
        self.v_len = tk.StringVar(value=f"{base.stitch_len:g}")
        self.v_underlay = tk.BooleanVar(value=base.underlay)
        self.v_pull = tk.StringVar(value=f"{base.pull_comp:g}")
        self.v_outline = tk.BooleanVar(value=outline)
        self.v_style = tk.StringVar(value="satin" if satin else "run")
        self.v_width = tk.StringVar(value=f"{(satin or SatinSpec()).width:g}")
        self._img = None
        grey = dict(foreground="#666666")

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)

        # Fill ------------------------------------------------------------
        ff = ttk.LabelFrame(f, text=" Fill ", padding=8)
        ff.pack(fill="x")
        chk = ttk.Checkbutton(ff, text="Fill the inside with stitches", variable=self.v_on,
                              command=self._enable)
        chk.grid(row=0, column=0, columnspan=3, sticky="w")
        if not can_fill_shape:
            self.v_on.set(False)
            chk.configure(state="disabled")
            ttk.Label(ff, text="(open shapes can't be filled)", **grey).grid(row=0, column=2, sticky="e")
        self.widgets = []
        ttk.Label(ff, text="Thread").grid(row=1, column=0, sticky="nw", pady=(8, 2))
        tf = ttk.Frame(ff)
        tf.grid(row=1, column=1, columnspan=2, sticky="w", pady=(8, 2))
        r1 = ttk.Radiobutton(tf, text="Same as the outline", variable=self.v_same, value=True)
        r1.pack(anchor="w")
        of = ttk.Frame(tf)
        of.pack(anchor="w", pady=(2, 0))
        r2 = ttk.Radiobutton(of, text="Another thread:", variable=self.v_same, value=False)
        r2.pack(side="left")
        self.l_thread = ttk.Label(of, compound="left", padding=(6, 0))
        self.l_thread.pack(side="left")
        b = ttk.Button(of, text="Choose…", command=self._pick)
        b.pack(side="left", padx=6)
        self.widgets += [r1, r2, b]
        rows = (("Angle (°)", self.v_angle, "direction of the rows; turns with the object"),
                ("Row spacing (mm)", self.v_spacing, "0.4 is typical; smaller is denser"),
                ("Stitch length (mm)", self.v_len, "3–4 mm"),
                ("Pull compensation (mm)", self.v_pull, "0.2 typical; more for stretchy fabric"))
        for i, (label, var, hint) in enumerate(rows, start=2):
            ttk.Label(ff, text=label).grid(row=i, column=0, sticky="w", pady=2)
            e = ttk.Entry(ff, textvariable=var, width=7)
            e.grid(row=i, column=1, sticky="w", pady=2)
            ttk.Label(ff, text=hint, **grey).grid(row=i, column=2, sticky="w", padx=6)
            self.widgets.append(e)
        u = ttk.Checkbutton(ff, text="Underlay (light stitches first, so the fill lies flat)",
                            variable=self.v_underlay)
        u.grid(row=6, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.widgets.append(u)

        # Outline ---------------------------------------------------------
        fo = ttk.LabelFrame(f, text=" Outline ", padding=8)
        fo.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(fo, text="Sew the outline (on top of any fill)",
                        variable=self.v_outline, command=self._enable).grid(
            row=0, column=0, columnspan=3, sticky="w")
        self.o_widgets = []
        sf = ttk.Frame(fo)
        sf.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        for val, text in (("run", "Run stitch (a thin line)"), ("satin", "Satin column, width")):
            rb = ttk.Radiobutton(sf, text=text, value=val, variable=self.v_style)
            rb.pack(side="left", padx=(0, 6))
            self.o_widgets.append(rb)
        e = ttk.Entry(sf, textvariable=self.v_width, width=5)
        e.pack(side="left")
        ttk.Label(sf, text=" mm  (1–5 mm suits borders)", **grey).pack(side="left")
        self.o_widgets.append(e)

        self.info = ttk.Label(f, text="", foreground=ERROR_COLOUR)
        self.info.pack(anchor="w", pady=(6, 0))
        bf = ttk.Frame(f)
        bf.pack(anchor="e", pady=(8, 0))
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bf, text="OK", command=self._ok).pack(side="right", padx=6)
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self._ok())
        self._show_thread()
        self._enable()
        _centre_on(self, parent)

    def _show_thread(self):
        t = thread_for_rgb(self.other_rgb)
        self._img = swatch(self, [to_hex(self.other_rgb)], int(16 * self.ui))
        self.l_thread.configure(image=self._img,
                                text=f" {t.number} {t.name}" if t else f" {to_hex(self.other_rgb)}")

    def _pick(self):
        p = ThreadPicker(self, self.ui, "Fill thread", thread_for_rgb(self.other_rgb), None, 0)
        res = p.wait()
        self.grab_set()                      # the picker took the grab; take it back
        if isinstance(res, Thread):
            self.other_rgb = res.rgb
            self.v_same.set(False)
            self._show_thread()

    def _enable(self):
        state = "normal" if self.v_on.get() else "disabled"
        for w in self.widgets:
            w.configure(state=state)
        ostate = "normal" if self.v_outline.get() else "disabled"
        for w in self.o_widgets:
            w.configure(state=ostate)

    def _num(self, var, label, lo, hi):
        try:
            v = float(var.get())
        except ValueError:
            raise ValueError(f"{label} must be a number.") from None
        if not lo <= v <= hi:
            raise ValueError(f"{label} must be between {lo:g} and {hi:g}.")
        return v

    def _ok(self):
        try:
            fill = None
            if self.v_on.get():
                fill = FillSpec(None if self.v_same.get() else self.other_rgb,
                                self._num(self.v_angle, "Angle", -360, 360),
                                self._num(self.v_spacing, "Row spacing", 0.2, 3),
                                self._num(self.v_len, "Stitch length", 1, 12),
                                self.v_underlay.get(),
                                self._num(self.v_pull, "Pull compensation", 0, 1))
            satin = None
            if self.v_outline.get() and self.v_style.get() == "satin":
                satin = SatinSpec(width=self._num(self.v_width, "Satin width", 0.5, 8))
        except ValueError as e:
            self.info.configure(text=str(e))
            return
        self.result = {"fill": fill, "outline": self.v_outline.get(), "satin": satin}
        self.destroy()

    def wait(self):
        self.grab_set()
        self.focus_set()
        self.wait_window()
        return self.result


# ------------------------------------------------------------------ main window
class App:
    def __init__(self, root: tk.Tk, path: str | None = None):
        self.root = root
        self.ui = max(1.0, root.winfo_fpixels("1i") / 96)
        self.svg_path: str | None = None
        self.svg_map: dict[str, DesignObject] = {}
        self.text_objs: dict[str, DesignObject] = {}
        self.extra_objs: dict[str, DesignObject] = {}  # copies, imported stitches and images
        self.groups: dict[str, str] = {}             # obj_id -> group id
        self.clipboard: list[dict] = []
        self.project_path: str | None = None
        self.dirty = False                           # changes since the project was saved
        self.texts: dict[str, TextSpec] = {}
        self.order_ids: list[str] = []
        self.mats: dict[str, Affine] = {}      # outline -> hoop placement, per object
        self.home: dict[str, Affine] = {}      # placement when first opened / added
        self.colours: dict[str, tuple[int, int, int]] = {}   # obj_id -> chosen thread rgb
        self.skipped: set[str] = set()
        self.fills: dict[str, FillSpec] = {}         # obj_id -> fill settings
        self.no_outline: set[str] = set()            # objects whose outline is not sewn
        self.satins: dict[str, SatinSpec] = {}       # obj_id -> satin outline settings
        self.color_order: list = []                  # user's sewing order: [(phase, rgb), ...]
        self.warnings: list[str] = []
        self.placed: dict[str, list[list[tuple[float, float]]]] = {}
        self.plan: Plan | None = None
        self.outside: list[str] = []
        self.hoop = (100.0, 100.0)
        self.margin = 2.0
        self.last_text: TextSpec | None = None
        self.undo_stack: list = []
        self.redo_stack: list = []
        self.px, self.origin = 1.0, (0.0, 0.0)
        self.handles: dict[str, tuple[float, float]] = {}
        self.drag = None
        self._shown: dict[str, str] = {}
        self._images: list[tk.PhotoImage] = []
        self._order_images: list[tk.PhotoImage] = []
        self._photo = None
        self._job = None
        self._draw_job = None

        self.v_hoop_w = tk.StringVar(value="100")
        self.v_hoop_h = tk.StringVar(value="100")
        self.v_margin = tk.StringVar(value="2")
        self.v_stitch = tk.StringVar(value="2.5")
        self.v_keep = tk.BooleanVar(value=False)
        self.v_knockout = tk.BooleanVar(value=True)
        self.v_spm = tk.StringVar(value="650")
        self.v_pes = tk.StringVar(value="1")
        self.v_jumps = tk.BooleanVar(value=True)
        self.v_dots = tk.BooleanVar(value=True)
        # zoom / pan: zoom 1 = whole hoop fits; view_c = hoop mm shown at the canvas centre
        self.zoom = 1.0
        self.view_c = (0.0, 0.0)
        self._pan = None
        self._hq_job = None
        # sew-out simulation: sim_pos None = show everything, else entries sewn so far
        self.track = Track()
        self.sim_pos: int | None = None
        self.sim_playing = False
        self._sim_job = None
        self._sim_acc = 0.0
        self._sim_img = None          # 1x image being drawn on during playback
        self._sim_drawn = 0
        self.v_sim = tk.DoubleVar(value=0)
        self._slide_lock = False
        self.v_speed = tk.StringVar(value="Normal")
        self.v_stop_change = tk.BooleanVar(value=True)
        self.v_status = tk.StringVar()
        self.v_w, self.v_h = tk.StringVar(), tk.StringVar()
        self.v_x, self.v_y = tk.StringVar(), tk.StringVar()
        self.v_rot = tk.StringVar(value="15")
        self.v_ratio = tk.BooleanVar(value=True)
        for v in (self.v_hoop_w, self.v_hoop_h, self.v_margin, self.v_stitch, self.v_keep,
                  self.v_knockout, self.v_spm):
            v.trace_add("write", lambda *a: self.schedule())
        self.v_jumps.trace_add("write", lambda *a: self.draw_preview())
        self.v_dots.trace_add("write", lambda *a: self.draw_preview())

        self._build()
        self.set_status("Open an SVG (Ctrl+O) or add text (Ctrl+T) to start.")
        if path:
            self.root.after(50, lambda: self.open(path))

    # ---------------------------------------------------------------- layout
    def _build(self):
        r, ui = self.root, self.ui
        r.title(TITLE)
        # Fit the screen (leaving room for the taskbar); maximise if the full size won't fit.
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        want_w, want_h = int(1200 * ui), int(820 * ui)
        w, h = min(want_w, sw - 40), min(want_h, sh - 100)
        r.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 30)}")
        r.minsize(min(int(900 * ui), sw - 40), min(int(560 * ui), sh - 100))
        if sys.platform == "win32" and (w < want_w or h < want_h):
            r.state("zoomed")
        style = ttk.Style(r)
        style.configure("Treeview", rowheight=int(22 * ui))

        bar = ttk.Frame(r, padding=(8, 8, 8, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="Open…", command=self.ask_open).pack(side="left")
        ttk.Button(bar, text="Add text…", command=self.add_text).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Save project", command=self.save_project).pack(side="left", padx=(6, 0))
        self.b_save = ttk.Button(bar, text="Save PES…", command=self.save, state="disabled")
        self.b_save.pack(side="left", padx=6)
        self.b_undo = ttk.Button(bar, text="Undo", command=self.undo, state="disabled", width=6)
        self.b_undo.pack(side="left", padx=(12, 0))
        self.b_redo = ttk.Button(bar, text="Redo", command=self.redo, state="disabled", width=6)
        self.b_redo.pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="Select all", command=self.select_all).pack(side="left", padx=(12, 0))
        self.l_file = ttk.Label(bar, text="No file open", foreground="#555555")
        self.l_file.pack(side="left", padx=12)

        status = ttk.Label(r, textvariable=self.v_status, padding=(10, 4), anchor="w",
                           relief="sunken")
        status.pack(side="bottom", fill="x")
        self.l_status = status

        panes = ttk.PanedWindow(r, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=8)
        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=0)
        panes.add(right, weight=1)

        # Objects -----------------------------------------------------------
        f_obj = ttk.LabelFrame(left, text=" Objects  (SVG colour → thread) ", padding=6)
        f_obj.pack(fill="both", expand=True)
        cols = ("id", "kind", "thread", "fill", "sew")
        self.tree = ttk.Treeview(f_obj, columns=cols, show="tree headings", height=7,
                                 selectmode="extended")
        self.tree.heading("#0", text="#", anchor="w")
        self.tree.column("#0", width=int(88 * ui), stretch=False)
        for c, text, w in (("id", "Object", 64), ("kind", "Shape", 84),
                           ("thread", "Outline", 118), ("fill", "Fill", 118), ("sew", "Sew", 36)):
            self.tree.heading(c, text=text, anchor="w")
            self.tree.column(c, width=int(w * ui), stretch=c == "kind")
        self.tree.tag_configure("skipped", foreground="#9e9e9e")
        sb = ttk.Scrollbar(f_obj, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        f_obj.rowconfigure(0, weight=1)
        f_obj.columnconfigure(0, weight=1)
        btns = ttk.Frame(f_obj)
        btns.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(btns, text="Choose thread…", command=self.change_thread).pack(side="left")
        ttk.Button(btns, text="Fill / outline…", command=self.edit_fill).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Sew / skip", command=self.toggle_skip).pack(side="left", padx=6)
        ttk.Button(btns, text="Delete", command=self.delete_selected).pack(side="left")
        ttk.Button(btns, text="Reset all", command=self.reset_all).pack(side="left", padx=6)
        ttk.Label(f_obj, foreground="#666666",
                  text="Double-click: outline thread (or edit text); on the Fill column: fill.  "
                       "Space: sew/skip.") \
            .grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Return>", lambda e: self._edit_or_thread())
        self.tree.bind("<space>", lambda e: self.toggle_skip())
        self.tree.bind("<Delete>", lambda e: self.delete_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.on_select())

        tabs = ttk.Notebook(left)
        tabs.pack(fill="x", pady=(8, 0))
        num = dict(width=7)
        grey = dict(foreground="#666666")

        # Arrange -----------------------------------------------------------
        f_arr = ttk.Frame(tabs, padding=8)
        tabs.add(f_arr, text=" Arrange ")
        self.l_target = ttk.Label(f_arr, text="Everything", font=("Segoe UI", 9, "bold"))
        self.l_target.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        ttk.Label(f_arr, text="Size (mm)").grid(row=1, column=0, sticky="w")
        sf = ttk.Frame(f_arr)
        sf.grid(row=1, column=1, columnspan=3, sticky="w", pady=2)
        self.e_w = ttk.Entry(sf, textvariable=self.v_w, **num)
        self.e_w.pack(side="left")
        ttk.Label(sf, text=" × ").pack(side="left")
        self.e_h = ttk.Entry(sf, textvariable=self.v_h, **num)
        self.e_h.pack(side="left")
        ttk.Checkbutton(sf, text="Keep proportions", variable=self.v_ratio).pack(side="left", padx=8)
        ttk.Label(f_arr, text="Position (mm)").grid(row=2, column=0, sticky="w")
        pf = ttk.Frame(f_arr)
        pf.grid(row=2, column=1, columnspan=3, sticky="w", pady=2)
        ttk.Label(pf, text="X").pack(side="left")
        self.e_x = ttk.Entry(pf, textvariable=self.v_x, **num)
        self.e_x.pack(side="left", padx=(3, 8))
        ttk.Label(pf, text="Y").pack(side="left")
        self.e_y = ttk.Entry(pf, textvariable=self.v_y, **num)
        self.e_y.pack(side="left", padx=3)
        ttk.Label(pf, text="centre, from hoop centre", **grey).pack(side="left", padx=6)
        ttk.Label(f_arr, text="Rotate (°)").grid(row=3, column=0, sticky="w")
        rf = ttk.Frame(f_arr)
        rf.grid(row=3, column=1, columnspan=3, sticky="w", pady=2)
        e_rot = ttk.Entry(rf, textvariable=self.v_rot, width=5)
        e_rot.pack(side="left")
        ttk.Button(rf, text="Rotate", command=self.rotate_by_entry, width=7).pack(side="left", padx=4)
        ttk.Button(rf, text="⟲ 90°", command=lambda: self.rotate_by(-90), width=6).pack(side="left", padx=(8, 2))
        ttk.Button(rf, text="⟳ 90°", command=lambda: self.rotate_by(90), width=6).pack(side="left")
        af = ttk.Frame(f_arr)
        af.grid(row=4, column=0, columnspan=4, sticky="w", pady=(6, 2))
        ttk.Button(af, text="Centre in hoop", command=self.centre_target).pack(side="left")
        ttk.Button(af, text="Fit to hoop", command=self.fit_target).pack(side="left", padx=6)
        ttk.Button(af, text="Reset", command=self.reset_target).pack(side="left")
        gf = ttk.Frame(f_arr)
        gf.grid(row=5, column=0, columnspan=4, sticky="w", pady=(2, 2))
        ttk.Button(gf, text="Flip ↔", width=7, command=lambda: self.flip(True)).pack(side="left")
        ttk.Button(gf, text="Flip ↕", width=7, command=lambda: self.flip(False)).pack(side="left", padx=(4, 8))
        mb = ttk.Menubutton(gf, text="Align")
        mb.pack(side="left")
        mb["menu"] = self._align_menu(mb)
        ttk.Button(gf, text="Group", command=self.group_selection).pack(side="left", padx=(8, 4))
        ttk.Button(gf, text="Ungroup", command=self.ungroup_selection).pack(side="left")
        ttk.Label(f_arr, **grey, justify="left", text=(
            "In the preview: drag a box around objects to select them (Ctrl+A or\n"
            "Select all for everything, Esc for none). Drag the selection to move it;\n"
            "corner handles resize, the round handle rotates (Shift: 15° steps).\n"
            "Arrow keys nudge 1 mm (Shift: 0.1 mm).")) \
            .grid(row=6, column=0, columnspan=4, sticky="w", pady=(4, 0))
        for e, fn in ((self.e_w, self.apply_size), (self.e_h, self.apply_size),
                      (self.e_x, self.apply_position), (self.e_y, self.apply_position)):
            e.bind("<Return>", lambda ev, fn=fn: fn())
            e.bind("<FocusOut>", lambda ev, fn=fn: fn())
        e_rot.bind("<Return>", lambda ev: self.rotate_by_entry())

        # Stitching ---------------------------------------------------------
        f_set = ttk.Frame(tabs, padding=8)
        tabs.add(f_set, text=" Stitching ")
        ttk.Label(f_set, text="Hoop (mm)").grid(row=0, column=0, sticky="w")
        hf = ttk.Frame(f_set)
        hf.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Entry(hf, textvariable=self.v_hoop_w, **num).pack(side="left")
        ttk.Label(hf, text=" × ").pack(side="left")
        ttk.Entry(hf, textvariable=self.v_hoop_h, **num).pack(side="left")
        ttk.Label(f_set, text="PE540D: 100 × 100", **grey).grid(row=0, column=2, sticky="w", padx=6)
        ttk.Label(f_set, text="Margin (mm)").grid(row=1, column=0, sticky="w")
        ttk.Entry(f_set, textvariable=self.v_margin, **num).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(f_set, text="kept clear inside the hoop", **grey).grid(row=1, column=2, sticky="w", padx=6)
        ttk.Label(f_set, text="Stitch length (mm)").grid(row=2, column=0, sticky="w")
        ttk.Spinbox(f_set, textvariable=self.v_stitch, from_=1.0, to=7.0, increment=0.1,
                    **num).grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(f_set, text="2–3 mm suits most outlines", **grey).grid(row=2, column=2, sticky="w", padx=6)
        ttk.Checkbutton(f_set, text="Keep drawing order (more colour changes)",
                        variable=self.v_keep).grid(row=3, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Label(f_set, text="PES version").grid(row=4, column=0, sticky="w")
        ttk.Combobox(f_set, textvariable=self.v_pes, values=("1", "6"), state="readonly",
                     width=5).grid(row=4, column=1, sticky="w", pady=2)
        ttk.Label(f_set, text="1 is tested on the PE540D", **grey).grid(row=4, column=2, sticky="w", padx=6)
        ttk.Checkbutton(f_set, text="Cut fills out under filled shapes stacked on top",
                        variable=self.v_knockout).grid(row=5, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Label(f_set, text="Machine speed").grid(row=6, column=0, sticky="w")
        ttk.Entry(f_set, textvariable=self.v_spm, **num).grid(row=6, column=1, sticky="w", pady=2)
        ttk.Label(f_set, text="stitches per minute, for the time estimate (PE540D: up to 650)",
                  **grey).grid(row=6, column=2, sticky="w", padx=6)

        # Sewing order ------------------------------------------------------
        f_ord = ttk.Frame(tabs, padding=8)
        tabs.add(f_ord, text=" Sewing order ")
        self.order = ttk.Treeview(f_ord, columns=("thread", "stitches", "time", "objects"),
                                  show="tree headings", height=5, selectmode="browse")
        self.order.heading("#0", text="Stop", anchor="w")
        self.order.column("#0", width=int(64 * ui), stretch=False)
        for c, text, w in (("thread", "Thread", 130), ("stitches", "Stitches", 64),
                           ("time", "Time", 70), ("objects", "Objects", 160)):
            self.order.heading(c, text=text, anchor="w")
            self.order.column(c, width=int(w * ui), stretch=c == "objects")
        self.order.pack(fill="both", expand=True)
        ob = ttk.Frame(f_ord)
        ob.pack(fill="x", pady=(6, 0))
        ttk.Button(ob, text="▲ Earlier", command=lambda: self.move_stop(-1)).pack(side="left")
        ttk.Button(ob, text="▼ Later", command=lambda: self.move_stop(1)).pack(side="left", padx=6)
        ttk.Button(ob, text="Automatic order", command=self.auto_order).pack(side="left")
        self.l_time = ttk.Label(ob, text="", foreground="#555555")
        self.l_time.pack(side="right")

        # Preview -----------------------------------------------------------
        self.canvas = tk.Canvas(right, bg=to_hex(OUTSIDE), highlightthickness=1,
                                highlightbackground="#cccccc", takefocus=1)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._schedule_draw())
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_motion)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Double-1>", lambda e: self._edit_or_thread())
        self.canvas.bind("<Motion>", self.on_hover)
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            self.canvas.bind(f"<{key}>", lambda e, dx=dx, dy=dy: self.nudge(dx, dy, e))
            self.canvas.bind(f"<Shift-{key}>", lambda e, dx=dx, dy=dy: self.nudge(dx, dy, e))
        self.canvas.bind("<Delete>", lambda e: self.delete_selected())
        self.canvas.bind("<space>", lambda e: self.toggle_skip())
        # zoom and pan
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self.zoom_at(1.25, e.x, e.y))   # Linux
        self.canvas.bind("<Button-5>", lambda e: self.zoom_at(0.8, e.x, e.y))
        for b in (2, 3):
            self.canvas.bind(f"<ButtonPress-{b}>", self.pan_start)
            self.canvas.bind(f"<B{b}-Motion>", self.pan_move)
            self.canvas.bind(f"<ButtonRelease-{b}>", self.pan_end)
        for key in ("plus", "equal", "KP_Add"):
            self.canvas.bind(f"<KeyPress-{key}>", lambda e: self.zoom_step(1.5))
        for key in ("minus", "KP_Subtract"):
            self.canvas.bind(f"<KeyPress-{key}>", lambda e: self.zoom_step(1 / 1.5))
        self.canvas.bind("<KeyPress-0>", lambda e: self.zoom_fit())
        self.canvas.bind("<Home>", lambda e: self.zoom_fit())

        under = ttk.Frame(right)
        under.pack(fill="x", pady=(6, 0))
        # controls are packed before the info text, so if space is short the text is cut, not them
        ttk.Checkbutton(under, text="Needle points", variable=self.v_dots).pack(side="right")
        ttk.Checkbutton(under, text="Show jumps", variable=self.v_jumps).pack(side="right", padx=8)
        ttk.Button(under, text="Fit", width=4, command=self.zoom_fit).pack(side="right", padx=(0, 8))
        ttk.Button(under, text="+", width=3, command=lambda: self.zoom_step(1.5)).pack(side="right")
        self.l_zoom = ttk.Label(under, text="100%", width=6, anchor="center")
        self.l_zoom.pack(side="right")
        ttk.Button(under, text="−", width=3, command=lambda: self.zoom_step(1 / 1.5)).pack(side="right")
        self.l_info = ttk.Label(under, text="")
        self.l_info.pack(side="left", fill="x", expand=True)

        sim = ttk.Frame(right)
        sim.pack(fill="x", pady=(4, 0))
        ttk.Button(sim, text="⏮", width=3, command=self.sim_start).pack(side="left")
        self.b_play = ttk.Button(sim, text="▶ Sew", width=8, command=self.sim_toggle)
        self.b_play.pack(side="left", padx=3)
        ttk.Button(sim, text="⏭", width=3, command=self.sim_end).pack(side="left")
        self.s_sim = ttk.Scale(sim, from_=0, to=1, variable=self.v_sim, command=self._on_slide)
        self.s_sim.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Combobox(sim, textvariable=self.v_speed, values=list(SIM_SPEEDS), state="readonly",
                     width=9).pack(side="left")
        ttk.Checkbutton(sim, text="Stop at colour changes",
                        variable=self.v_stop_change).pack(side="left", padx=(8, 0))
        self.l_sim = ttk.Label(right, text="Sew-out: press ▶ Sew to watch the design being "
                                           "stitched in order.", foreground="#555555")
        self.l_sim.pack(fill="x", pady=(2, 0))

        r.bind_all("<Control-o>", lambda e: self._main_key(e, self.ask_open))
        r.bind_all("<Control-n>", lambda e: self._main_key(e, self.new_design))
        r.bind_all("<Control-i>", lambda e: self._main_key(e, self.ask_import))
        r.bind_all("<Control-S>", lambda e: self._main_key(e, self.save_project))
        r.protocol("WM_DELETE_WINDOW", self.exit_app)
        r.bind_all("<Control-s>", lambda e: self._main_key(e, self.save))
        r.bind_all("<Control-t>", lambda e: self._main_key(e, self.add_text))
        r.bind_all("<Control-z>", lambda e: self._main_key(e, self.undo, text_ok=False))
        r.bind_all("<Control-y>", lambda e: self._main_key(e, self.redo, text_ok=False))
        r.bind_all("<Control-Z>", lambda e: self._main_key(e, self.redo, text_ok=False))
        r.bind_all("<Control-a>", lambda e: self._main_key(e, self.select_all, text_ok=False))
        r.bind_all("<Control-c>", lambda e: self._main_key(e, self.copy_selection, text_ok=False))
        r.bind_all("<Control-v>", lambda e: self._main_key(e, self.paste, text_ok=False))
        r.bind_all("<Control-d>", lambda e: self._main_key(e, self.duplicate, text_ok=False))
        r.bind_all("<Control-g>", lambda e: self._main_key(e, self.group_selection, text_ok=False))
        r.bind_all("<Control-G>", lambda e: self._main_key(e, self.ungroup_selection, text_ok=False))
        self._build_menu()
        r.bind_all("<Escape>", lambda e: self._main_key(e, self.select_none, text_ok=False))

    def _align_menu(self, master) -> tk.Menu:
        m = tk.Menu(master, tearoff=False)
        for label, how in (("Left edges", "left"), ("Centres (across)", "hcentre"),
                           ("Right edges", "right"), (None, None), ("Top edges", "top"),
                           ("Middles (up and down)", "vcentre"), ("Bottom edges", "bottom")):
            if label is None:
                m.add_separator()
            else:
                m.add_command(label=label, command=lambda h=how: self.align(h))
        m.add_separator()
        m.add_command(label="Space evenly across", command=lambda: self.distribute(True))
        m.add_command(label="Space evenly up and down", command=lambda: self.distribute(False))
        return m

    def _build_menu(self):
        """The menu bar. Commands still to come are added by later sections."""
        bar = tk.Menu(self.root)
        self.m_file = tk.Menu(bar, tearoff=False)
        self.m_file.add_command(label="New", accelerator="Ctrl+N", command=self.new_design)
        self.m_file.add_command(label="Open…", accelerator="Ctrl+O", command=self.ask_open)
        self.m_file.add_command(label="Import into this design…", accelerator="Ctrl+I",
                                command=self.ask_import)
        self.m_file.add_command(label="Add text…", accelerator="Ctrl+T", command=self.add_text)
        self.m_file.add_separator()
        self.m_file.add_command(label="Save project", accelerator="Ctrl+Shift+S",
                                command=self.save_project)
        self.m_file.add_command(label="Save project as…", command=lambda: self.save_project(True))
        self.m_file.add_command(label="Save PES for the machine…", accelerator="Ctrl+S",
                                command=self.save)
        self.m_file.add_separator()
        self.m_file.add_command(label="Exit", command=self.exit_app)
        bar.add_cascade(label="File", menu=self.m_file)
        m = tk.Menu(bar, tearoff=False)
        m.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo)
        m.add_command(label="Redo", accelerator="Ctrl+Y", command=self.redo)
        m.add_separator()
        m.add_command(label="Copy", accelerator="Ctrl+C", command=self.copy_selection)
        m.add_command(label="Paste", accelerator="Ctrl+V", command=self.paste)
        m.add_command(label="Duplicate", accelerator="Ctrl+D", command=self.duplicate)
        m.add_command(label="Delete", accelerator="Del", command=self.delete_selected)
        m.add_separator()
        m.add_command(label="Select all", accelerator="Ctrl+A", command=self.select_all)
        m.add_command(label="Select none", accelerator="Esc", command=self.select_none)
        bar.add_cascade(label="Edit", menu=m)
        m = tk.Menu(bar, tearoff=False)
        m.add_command(label="Group", accelerator="Ctrl+G", command=self.group_selection)
        m.add_command(label="Ungroup", accelerator="Ctrl+Shift+G", command=self.ungroup_selection)
        m.add_separator()
        m.add_command(label="Flip left-right", command=lambda: self.flip(True))
        m.add_command(label="Flip up-down", command=lambda: self.flip(False))
        m.add_command(label="Rotate 90° left", command=lambda: self.rotate_by(-90))
        m.add_command(label="Rotate 90° right", command=lambda: self.rotate_by(90))
        m.add_separator()
        m.add_cascade(label="Align", menu=self._align_menu(m))
        m.add_separator()
        m.add_command(label="Centre in hoop", command=self.centre_target)
        m.add_command(label="Fit to hoop", command=self.fit_target)
        m.add_separator()
        m.add_command(label="Fill / outline…", command=self.edit_fill)
        m.add_command(label="Choose thread…", command=self.change_thread)
        bar.add_cascade(label="Arrange", menu=m)
        self.m_view = tk.Menu(bar, tearoff=False)
        self.m_view.add_command(label="Zoom in", accelerator="+", command=lambda: self.zoom_step(1.5))
        self.m_view.add_command(label="Zoom out", accelerator="-", command=lambda: self.zoom_step(1 / 1.5))
        self.m_view.add_command(label="Fit hoop", accelerator="0", command=self.zoom_fit)
        self.m_view.add_separator()
        self.m_view.add_command(label="Sew-out: play / pause", command=self.sim_toggle)
        bar.add_cascade(label="View", menu=self.m_view)
        self.root.configure(menu=bar)
        self.menubar = bar

    def _main_key(self, event, fn, text_ok: bool = True):
        """Run a shortcut only in the main window, and (unless text_ok) not while
        typing in a text box, where Ctrl+Z / Ctrl+A mean something else."""
        w = event.widget
        if not isinstance(w, tk.Misc) or w.winfo_toplevel() is not self.root:
            return None
        if not text_ok and w.winfo_class() in TEXT_WIDGETS:
            return None
        fn()
        return "break"

    # ---------------------------------------------------------------- helpers
    def set_status(self, text: str, colour: str = "#333333"):
        self.v_status.set(text)
        self.l_status.configure(foreground=colour)

    def obj(self, obj_id: str) -> DesignObject:
        return (self.text_objs.get(obj_id) or self.extra_objs.get(obj_id)
                or self.svg_map[obj_id])

    def chosen_rgb(self, o: DesignObject):
        return self.colours.get(o.obj_id) or nearest_brother(o.source_color).rgb

    def selection(self) -> list[str]:
        return [i for i in self.tree.selection() if i in self.mats]

    def target(self) -> list[str]:
        """What the Arrange controls act on: the selection, or everything."""
        return self.selection() or list(self.order_ids)

    def target_bounds(self, ids=None):
        pts = [p for i in (ids if ids is not None else self.target())
               for pl in self.placed.get(i, []) for p in pl]
        return bounds(pts) if pts else None

    def to_canvas(self, x, y):
        return self.origin[0] + x * self.px, self.origin[1] + y * self.px

    def from_canvas(self, cx, cy):
        return (cx - self.origin[0]) / self.px, (cy - self.origin[1]) / self.px

    def _on_double(self, event):
        if self.tree.identify_row(event.y):
            if self.tree.identify_column(event.x) == "#4":      # the Fill column
                self.edit_fill()
            else:
                self._edit_or_thread()

    def _edit_or_thread(self):
        sel = self.selection()
        if len(sel) == 1 and sel[0] in self.texts:
            self.edit_text(sel[0])
        elif sel:
            self.change_thread()

    # ---------------------------------------------------------------- undo
    def _state(self):
        return (dict(self.mats), dict(self.home), dict(self.colours), set(self.skipped),
                dict(self.texts), dict(self.text_objs), list(self.order_ids),
                dict(self.fills), set(self.no_outline), dict(self.satins), list(self.color_order),
                dict(self.extra_objs), dict(self.groups))

    def _restore(self, st):
        (self.mats, self.home, self.colours, self.skipped, self.texts, self.text_objs,
         self.order_ids, self.fills, self.no_outline, self.satins, self.color_order,
         self.extra_objs, self.groups) = st
        self.fill_objects()
        self.refresh()

    def push_undo(self):
        self.dirty = True
        self.undo_stack.append(self._state())
        del self.undo_stack[:-100]
        self.redo_stack.clear()
        self._undo_buttons()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self._state())
            self._restore(self.undo_stack.pop())
            self._undo_buttons()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self._state())
            self._restore(self.redo_stack.pop())
            self._undo_buttons()

    def _undo_buttons(self):
        self.b_undo.configure(state="normal" if self.undo_stack else "disabled")
        self.b_redo.configure(state="normal" if self.redo_stack else "disabled")

    # ---------------------------------------------------------------- files
    OPEN_TYPES = [("Designs", "*.svg *.svg2pes *.pes *.dst *.jef *.exp *.vp3 *.png *.jpg *.jpeg *.bmp *.gif"),
                  ("SVG artwork", "*.svg"), ("svg2pes projects", "*.svg2pes"),
                  ("Embroidery files", "*.pes *.dst *.jef *.exp *.vp3"),
                  ("Pictures", "*.png *.jpg *.jpeg *.bmp *.gif"), ("All files", "*.*")]

    def ask_open(self):
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(parent=self.root, title="Open a design",
                                          filetypes=self.OPEN_TYPES)
        if path:
            self.open(path)

    def _load_any(self, path: str):
        """Load an SVG or an embroidery file as a Design (raises OSError / ValueError)."""
        ext = os.path.splitext(path)[1].lower()
        if ext in READ_EXTENSIONS:
            return load_stitches(path)
        return load_svg(path)

    def open(self, path: str):
        """Open a file as a new design: SVG, project, embroidery file or picture."""
        ext = os.path.splitext(path)[1].lower()
        if ext == PROJECT_EXTENSION:
            self.open_project(path)
            return
        if ext in IMAGE_EXTENSIONS:
            self.new_design(confirm=False)
            self.import_image(path)
            return
        try:
            design = self._load_any(path)
        except (OSError, ValueError) as e:
            messagebox.showerror(TITLE, f"Could not open {path}:\n\n{e}", parent=self.root)
            return
        if not design.objects:
            messagebox.showerror(TITLE, "No stitchable shapes were found in this file.\n\n"
                                 + "\n".join(sorted(set(design.warnings))), parent=self.root)
            return
        self._start_design(design, path)

    def _clear_design(self):
        for d in (self.svg_map, self.text_objs, self.extra_objs, self.groups, self.texts,
                  self.colours, self.fills, self.satins, self.mats, self.home):
            d.clear()
        for st in (self.no_outline, self.skipped):
            st.clear()
        self.color_order.clear()
        self.order_ids = []
        self.warnings = []
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._undo_buttons()
        self.project_path = None
        self.dirty = False

    def _fit_matrix(self, objects):
        """Placement that centres objects in the hoop, shrunk to fit if too big."""
        x0, y0, x1, y1 = bounds([p for o in objects for pl in o.polylines for p in pl.points])
        aw, ah = self.hoop[0] - 2 * self.margin, self.hoop[1] - 2 * self.margin
        limits = [a / d for a, d in ((aw, x1 - x0), (ah, y1 - y0)) if d > 0]
        s = min([1.0] + limits)
        return chain(translate(-(x0 + x1) / 2, -(y0 + y1) / 2), scale(s)), s

    def _start_design(self, design, path: str):
        try:
            self._read_hoop()
        except ValueError:
            pass            # keep the last good hoop size
        self._clear_design()
        self.svg_path = path
        for o in design.objects:
            (self.text_objs if o.obj_id in design.texts else self.svg_map)[o.obj_id] = o
        self.texts.update(design.texts)
        self.order_ids = [o.obj_id for o in design.objects]
        self.warnings = list(design.warnings)
        if any(o.stitches for o in design.objects):
            m, s = IDENTITY, 1.0          # embroidery files are already placed on the hoop
        else:
            m, s = self._fit_matrix(design.objects)
        self.mats = {i: m for i in self.order_ids}
        self.home = dict(self.mats)
        self._set_title(os.path.basename(path))
        self.fill_objects()
        self.refresh()
        if s < 1:
            self.set_status(f"The design was shrunk to {s:.0%} to fit the hoop. Use Arrange to "
                            "change its size.", WARN_COLOUR)

    def _set_title(self, name: str):
        self.l_file.configure(text=name)
        self.root.title(f"{name} – {TITLE}")

    def new_design(self, confirm: bool = True):
        if confirm and not self._confirm_discard():
            return
        self._clear_design()
        self.svg_path = None
        self._set_title("New design")
        self.fill_objects()
        self.refresh()
        self.set_status("New design: open or import artwork, or add text.")

    def ask_import(self):
        path = filedialog.askopenfilename(parent=self.root, title="Import into this design",
                                          filetypes=[t for t in self.OPEN_TYPES
                                                     if "svg2pes" not in t[0]])
        if path:
            self.import_file(path)

    def import_file(self, path: str):
        """Add the objects of an SVG or embroidery file to the current design."""
        ext = os.path.splitext(path)[1].lower()
        if ext in IMAGE_EXTENSIONS:
            self.import_image(path)
            return
        try:
            design = self._load_any(path)
        except (OSError, ValueError) as e:
            messagebox.showerror(TITLE, f"Could not import {path}:\n\n{e}", parent=self.root)
            return
        if not design.objects:
            self.set_status("Nothing stitchable was found in that file.", WARN_COLOUR)
            return
        self.add_objects(design.objects, keep_place=any(o.stitches for o in design.objects),
                         texts=design.texts)
        if self.svg_path is None:
            self.svg_path = path

    def add_objects(self, objects, keep_place: bool = False, fills=None, texts=None):
        """Add new objects to the design (renamed if their ids are taken), centred in the
        hoop (or left where they are), and select them."""
        self.push_undo()
        index = max([self.obj(i).index for i in self.order_ids], default=0)
        m = IDENTITY if keep_place else self._fit_matrix(objects)[0]
        new_ids = []
        for o in objects:
            nid, n = o.obj_id, 2
            while nid in self.mats or nid in self.svg_map or nid in self.text_objs:
                nid = f"{o.obj_id}_{n}"
                n += 1
            index += 1
            if texts and o.obj_id in texts:
                self.text_objs[nid] = replace(o, index=index, obj_id=nid)
                self.texts[nid] = texts[o.obj_id]
            else:
                self.extra_objs[nid] = replace(o, index=index, obj_id=nid)
            self.order_ids.append(nid)
            self.mats[nid] = self.home[nid] = m
            if fills and o.obj_id in fills:
                self.fills[nid] = fills[o.obj_id]
            new_ids.append(nid)
        self.fill_objects()
        self.tree.selection_set(new_ids)
        self.refresh()
        self.set_status(f"Added {len(new_ids)} object(s). They are selected: drag them into place.")
        return new_ids

    # ---------------------------------------------------------------- projects
    def _project(self) -> Project:
        objs = [self.obj(i) for i in self.order_ids]
        settings = {"hoop": [self.v_hoop_w.get(), self.v_hoop_h.get()],
                    "margin": self.v_margin.get(), "stitch_len": self.v_stitch.get(),
                    "keep_order": self.v_keep.get(), "knockout": self.v_knockout.get(),
                    "machine_spm": self.v_spm.get(), "pes_version": self.v_pes.get()}
        return Project(objs, dict(self.mats), dict(self.home), dict(self.colours),
                       set(self.skipped), dict(self.fills), set(self.no_outline),
                       dict(self.satins), dict(self.texts), dict(self.groups),
                       list(self.color_order), settings, self.svg_path, list(self.warnings))

    def save_project(self, save_as: bool = False) -> bool:
        if not self.order_ids:
            self.set_status("Nothing to save yet.", WARN_COLOUR)
            return False
        path = self.project_path
        if save_as or not path:
            base = os.path.splitext(os.path.basename(self.svg_path or "design"))[0]
            folder = os.path.dirname(self.svg_path) if self.svg_path else os.path.expanduser("~")
            path = filedialog.asksaveasfilename(
                parent=self.root, title="Save project", defaultextension=PROJECT_EXTENSION,
                initialfile=base + PROJECT_EXTENSION, initialdir=folder,
                filetypes=[("svg2pes project", "*" + PROJECT_EXTENSION)])
            if not path:
                return False
        try:
            save_project(path, self._project())
        except OSError as e:
            messagebox.showerror(TITLE, f"Could not save {path}:\n\n{e}", parent=self.root)
            return False
        self.project_path = path
        self.dirty = False
        self._set_title(os.path.basename(path))
        self.set_status(f"Saved project {path}. Open it again with File → Open.", OK_COLOUR)
        return True

    def open_project(self, path: str):
        try:
            proj = load_project(path)
        except (OSError, ValueError, TypeError, KeyError) as e:
            messagebox.showerror(TITLE, f"Could not open {path}:\n\n{e}", parent=self.root)
            return
        self._clear_design()
        st = proj.settings
        if "hoop" in st:
            self.v_hoop_w.set(str(st["hoop"][0]))
            self.v_hoop_h.set(str(st["hoop"][1]))
        for key, var in (("margin", self.v_margin), ("stitch_len", self.v_stitch),
                         ("machine_spm", self.v_spm), ("pes_version", self.v_pes)):
            if key in st:
                var.set(str(st[key]))
        for key, var in (("keep_order", self.v_keep), ("knockout", self.v_knockout)):
            if key in st:
                var.set(bool(st[key]))
        try:
            self._read_hoop()
        except ValueError:
            pass
        for o in proj.objects:
            (self.text_objs if o.obj_id in proj.texts else self.svg_map)[o.obj_id] = o
        self.order_ids = [o.obj_id for o in proj.objects]
        self.mats = {i: proj.mats.get(i, IDENTITY) for i in self.order_ids}
        self.home = {i: proj.home.get(i, self.mats[i]) for i in self.order_ids}
        self.colours, self.fills, self.satins = proj.colours, proj.fills, proj.satins
        self.texts, self.groups = proj.texts, proj.groups
        self.skipped, self.no_outline = proj.skipped, proj.no_outline
        self.color_order, self.warnings = proj.color_order, proj.warnings
        self.svg_path = proj.source or path
        self.project_path = path
        self._set_title(os.path.basename(path))
        self.fill_objects()
        self.refresh()
        self.dirty = False

    def _confirm_discard(self) -> bool:
        """True if it's fine to throw away the current design (asks to save changes)."""
        if not self.dirty or not self.order_ids:
            return True
        ans = messagebox.askyesnocancel(TITLE, "Save the changes to this design as a project "
                                        "first?", parent=self.root)
        if ans is None:
            return False
        return self.save_project() if ans else True

    def import_image(self, path: str):
        self.set_status("Picture import is not available yet.", WARN_COLOUR)

    def exit_app(self):
        if self._confirm_discard():
            self.root.destroy()

    def save(self):
        if self._job is not None:            # settings changed moments ago: re-plan first
            self.root.after_cancel(self._job)
            self.refresh()
        if self.plan is None or self.outside:
            if self.outside:
                messagebox.showwarning(TITLE, "Part of the design is outside the sewing area. "
                                       "Move or shrink it first (Arrange → Fit to hoop).",
                                       parent=self.root)
            return
        if self.svg_path:
            stem, folder = os.path.splitext(os.path.basename(self.svg_path))[0], os.path.dirname(self.svg_path)
        else:
            stem, folder = "text", os.path.expanduser("~")
        short = re.sub(r"[^A-Za-z0-9]", "", stem)[:8] or "design"
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save PES for the machine", defaultextension=".pes",
            initialfile=short + ".pes", initialdir=folder, filetypes=[("Brother PES", "*.pes")])
        if not path:
            return
        try:
            write_pes_file(self.plan, path, int(self.v_pes.get()))
        except (ConversionError, OSError) as e:
            messagebox.showerror(TITLE, f"Could not save {path}:\n\n{e}", parent=self.root)
            return
        name = os.path.splitext(os.path.basename(path))[0]
        tip = ("  Tip: a short name without spaces is safest on the machine's USB stick."
               if " " in name or len(name) > 8 else "")
        self.set_status(f"Saved {path}.{tip}", OK_COLOUR)

    # ---------------------------------------------------------------- objects list
    def fill_objects(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self._images.clear()
        size = int(16 * self.ui)
        for oid in self.order_ids:
            o = self.obj(oid)
            rgb = self.chosen_rgb(o)
            t = thread_for_rgb(rgb)
            img = swatch(self.root, [to_hex(o.source_color), to_hex(rgb)], size)
            self._images.append(img)
            skipped = oid in self.skipped
            outline = "(no outline)" if oid in self.no_outline else \
                (f"{t.number}  {t.name}" if t else to_hex(rgb))
            if oid in self.satins and oid not in self.no_outline:
                outline += f"  (satin {self.satins[oid].width:g})"
            fill = "—"
            if oid in self.fills:
                frgb = self.fills[oid].color or rgb
                ft = thread_for_rgb(frgb)
                fill = f"{ft.number}  {ft.name}" if ft else to_hex(frgb)
            if oid in self.texts:
                first = self.texts[oid].text.strip().split("\n")[0]
                kind = f"Text “{first[:16]}{'…' if len(first) > 16 else ''}”"
            else:
                kind = o.kind
            if oid in self.groups:
                kind += f"  [{self.groups[oid]}]"
            self.tree.insert("", "end", iid=oid, text=f" {o.index}", image=img,
                             values=(oid, kind, outline, fill, "skip" if skipped else "sew"),
                             tags=("skipped",) if skipped else ())
        keep = [i for i in sel if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)

    def _need_selection(self) -> list[str]:
        sel = self.selection()
        if not sel:
            self.set_status("Select one or more objects first (in the list or the preview).",
                            WARN_COLOUR)
        return sel

    def select_all(self):
        if self.order_ids:
            self.tree.selection_set(self.order_ids)
            self.set_status(f"Selected all {len(self.order_ids)} objects.")

    def select_none(self):
        if self.drag and self.drag["mode"] == "box":     # Esc during a box drag cancels it
            self.drag = None
            self.canvas.delete("box")
        self.tree.selection_remove(self.tree.selection())

    def change_thread(self):
        sel = self._need_selection()
        if not sel:
            return
        objs = [self.obj(i) for i in sel]
        first = objs[0]
        others = []
        if len(objs) == 1:
            others = [self.obj(i) for i in self.order_ids
                      if self.obj(i).source_color == first.source_color and i != first.obj_id]
        title = (f"Thread for {first.obj_id}" if len(objs) == 1
                 else f"Thread for {len(objs)} objects")
        picker = ThreadPicker(self.root, self.ui, title, thread_for_rgb(self.chosen_rgb(first)),
                              to_hex(first.source_color), len(others))
        result = picker.wait()
        if result is None:
            return
        self.push_undo()
        for o in objs + (others if picker.apply_all.get() else []):
            if result == "reset":
                self.colours.pop(o.obj_id, None)
            else:
                self.colours[o.obj_id] = result.rgb
        self.fill_objects()
        self.refresh()

    def toggle_skip(self):
        sel = self._need_selection()
        if not sel:
            return
        self.push_undo()
        skip = any(i not in self.skipped for i in sel)
        for i in sel:
            (self.skipped.add if skip else self.skipped.discard)(i)
        self.fill_objects()
        self.refresh()

    def _forget(self, i: str):
        for d in (self.texts, self.text_objs, self.extra_objs, self.mats, self.home,
                  self.colours, self.fills, self.satins, self.groups):
            d.pop(i, None)
        self.skipped.discard(i)
        self.no_outline.discard(i)
        if i in self.order_ids:
            self.order_ids.remove(i)

    def delete_selected(self):
        sel = self._need_selection()
        if not sel:
            return
        self.push_undo()
        for i in sel:
            self._forget(i)
        self.fill_objects()
        self.refresh()
        self.set_status(f"Deleted {len(sel)} object(s). Undo (Ctrl+Z) brings them back.")

    # ---------------------------------------------------------------- copy / paste
    def _snapshot(self, i: str) -> dict:
        return {"obj": self.obj(i), "mat": self.mats[i], "colour": self.colours.get(i),
                "skipped": i in self.skipped, "fill": self.fills.get(i),
                "no_outline": i in self.no_outline, "satin": self.satins.get(i),
                "text": self.texts.get(i), "group": self.groups.get(i)}

    def _new_id(self, base: str) -> str:
        base = base.split("_copy")[0]
        n = 1
        while f"{base}_copy{n}" in self.mats or f"{base}_copy{n}" in self.svg_map:
            n += 1
        return f"{base}_copy{n}"

    def copy_selection(self):
        sel = self._need_selection()
        if sel:
            self.clipboard = [self._snapshot(i) for i in sel]
            self.set_status(f"Copied {len(sel)} object(s). Paste with Ctrl+V.")

    def paste(self, offset: float = 5.0):
        if not self.clipboard:
            self.set_status("Nothing to paste: copy objects first (Ctrl+C).", WARN_COLOUR)
            return
        self.push_undo()
        index = max([self.obj(i).index for i in self.order_ids], default=0)
        new_groups: dict[str, str] = {}
        new_ids = []
        for snap in self.clipboard:
            o = snap["obj"]
            nid = self._new_id(o.obj_id)
            index += 1
            copy = replace(o, index=index, obj_id=nid)
            if snap["text"] is not None:
                self.text_objs[nid] = copy
                self.texts[nid] = snap["text"]
            else:
                self.extra_objs[nid] = copy
            self.order_ids.append(nid)
            self.mats[nid] = chain(snap["mat"], translate(offset, offset))
            self.home[nid] = self.mats[nid]
            for key, store in (("colour", self.colours), ("fill", self.fills),
                               ("satin", self.satins)):
                if snap[key] is not None:
                    store[nid] = snap[key]
            if snap["skipped"]:
                self.skipped.add(nid)
            if snap["no_outline"]:
                self.no_outline.add(nid)
            if snap["group"]:
                if snap["group"] not in new_groups:          # copies form a group of their own
                    new_groups[snap["group"]] = self._new_group_id()
                self.groups[nid] = new_groups[snap["group"]]
            new_ids.append(nid)
        self.fill_objects()
        self.tree.selection_set(new_ids)
        self.refresh()
        self.set_status(f"Pasted {len(new_ids)} object(s), 5 mm down and right. Drag them into place.")

    def duplicate(self):
        sel = self._need_selection()
        if sel:
            saved = self.clipboard
            self.clipboard = [self._snapshot(i) for i in sel]
            self.paste()
            self.clipboard = saved

    # ---------------------------------------------------------------- groups
    def _new_group_id(self) -> str:
        n = 1
        while f"group {n}" in self.groups.values():
            n += 1
        return f"group {n}"

    def expand_groups(self, ids) -> list[str]:
        """ids plus every other member of their groups."""
        gids = {self.groups[i] for i in ids if i in self.groups}
        extra = [i for i in self.order_ids if self.groups.get(i) in gids and i not in ids]
        return list(ids) + extra

    def group_selection(self):
        sel = self._need_selection()
        if len(sel) < 2:
            if sel:
                self.set_status("Select two or more objects to group them.", WARN_COLOUR)
            return
        self.push_undo()
        gid = self._new_group_id()
        for i in sel:
            self.groups[i] = gid
        self.fill_objects()
        self.set_status(f"Grouped {len(sel)} objects as {gid}: clicking one selects them all.")

    def ungroup_selection(self):
        sel = [i for i in self._need_selection() if i in self.groups]
        if not sel:
            return
        self.push_undo()
        for i in sel:
            del self.groups[i]
        self.fill_objects()
        self.set_status(f"Ungrouped {len(sel)} object(s).")

    # ---------------------------------------------------------------- flip / align
    def flip(self, horizontal: bool):
        b = self.target_bounds()
        if b:
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            self.apply_transform(scale(-1, 1, cx, cy) if horizontal else scale(1, -1, cx, cy))

    def _units(self, ids) -> list[list[str]]:
        """Objects to move together: each group is one unit, other objects on their own."""
        units, seen = [], {}
        for i in ids:
            g = self.groups.get(i)
            if g is None:
                units.append([i])
            elif g in seen:
                seen[g].append(i)
            else:
                seen[g] = [i]
                units.append(seen[g])
        return units

    def align(self, how: str):
        sel = self._need_selection()
        if not sel:
            return
        units = self._units(sel)
        if len(units) == 1:          # one object (or group): align it to the sewing area
            hw, hh = self.hoop[0] / 2 - self.margin, self.hoop[1] / 2 - self.margin
            ref = (-hw, -hh, hw, hh)
        else:
            ref = self.target_bounds(sel)
        self.push_undo()
        for u in units:
            b = self.target_bounds(u)
            dx = {"left": ref[0] - b[0], "right": ref[2] - b[2],
                  "hcentre": (ref[0] + ref[2]) / 2 - (b[0] + b[2]) / 2}.get(how, 0.0)
            dy = {"top": ref[1] - b[1], "bottom": ref[3] - b[3],
                  "vcentre": (ref[1] + ref[3]) / 2 - (b[1] + b[3]) / 2}.get(how, 0.0)
            for i in u:
                self.mats[i] = chain(self.mats[i], translate(dx, dy))
        self.refresh()

    def distribute(self, horizontal: bool):
        sel = self._need_selection()
        units = self._units(sel)
        if len(units) < 3:
            if sel:
                self.set_status("Select three or more objects (or groups) to space them evenly.",
                                WARN_COLOUR)
            return
        k = 0 if horizontal else 1
        boxes = sorted(((self.target_bounds(u), u) for u in units), key=lambda bu: bu[0][k])
        first, last = boxes[0][0], boxes[-1][0]
        total = sum(b[k + 2] - b[k] for b, _ in boxes)
        gap = ((last[k + 2] - first[k]) - total) / (len(boxes) - 1)
        self.push_undo()
        pos = first[k]
        for b, u in boxes:
            shift = pos - b[k]
            for i in u:
                self.mats[i] = chain(self.mats[i], translate(shift, 0) if horizontal else translate(0, shift))
            pos += (b[k + 2] - b[k]) + gap
        self.refresh()

    def reset_all(self):
        if not self.order_ids:
            return
        self.push_undo()
        self.colours.clear()
        self.skipped.clear()
        self.fills.clear()
        self.no_outline.clear()
        self.satins.clear()
        self.color_order.clear()
        self.mats = dict(self.home)
        self.fill_objects()
        self.refresh()

    # ---------------------------------------------------------------- fill
    def _placed_fill(self, oid: str):
        """The object's fill with its row angle turned by the object's own rotation,
        so the rows keep their direction relative to the shape."""
        spec = self.fills.get(oid)
        if spec is None:
            return None
        a, b, c, d, _, _ = self.mats[oid]
        turn = math.degrees(math.atan2(b, a))
        angle = spec.angle + turn if a * d - b * c >= 0 else turn - spec.angle   # mirrored
        return replace(spec, angle=angle)

    def edit_fill(self):
        sel = self._need_selection()
        if not sel:
            return
        sel = [i for i in sel if not self.obj(i).stitches]      # imported stitches: as they are
        if not sel:
            self.set_status("Imported stitches are sewn as they are; only their thread can change.",
                            WARN_COLOUR)
            return
        ok = [i for i in sel if can_fill(self.obj(i).polylines)]
        first = (ok or sel)[0]
        title = f"Fill and outline: {first}" if len(sel) == 1 else \
            f"Fill and outline: {len(sel)} objects"
        res = FillDialog(self.root, self.ui, title, self.fills.get(first),
                         first not in self.no_outline, self.chosen_rgb(self.obj(first)),
                         self.satins.get(first), can_fill_shape=bool(ok)).wait()
        if res is None:
            return
        self.push_undo()
        for i in sel:
            if i in ok:
                if res["fill"] is None:
                    self.fills.pop(i, None)
                else:
                    self.fills[i] = res["fill"]
            (self.no_outline.discard if res["outline"] else self.no_outline.add)(i)
            if res["satin"] is None:
                self.satins.pop(i, None)
            else:
                self.satins[i] = res["satin"]
        self.fill_objects()
        self.refresh()
        left_out = [i for i in sel if i not in ok]
        if left_out and res["fill"] is not None:
            self.set_status(f"Not closed shapes, so not filled: {', '.join(left_out)}", WARN_COLOUR)

    # ---------------------------------------------------------------- text
    def add_text(self):
        spec = TextDialog(self.root, self.ui, self.last_text and
                          TextSpec("", self.last_text.font_path, self.last_text.font_name,
                                   self.last_text.font_index, self.last_text.height_mm,
                                   self.last_text.align, self.last_text.spacing_mm),
                          editing=False).wait()
        if spec is None:
            return
        polys, missing = text_polylines(spec)
        if not polys:
            self.set_status("That text has no letters this font can draw.", ERROR_COLOUR)
            return
        self.push_undo()
        n = 1
        while f"text{n}" in self.mats or f"text{n}" in self.svg_map:
            n += 1
        oid = f"text{n}"
        index = max([self.obj(i).index for i in self.order_ids], default=0) + 1
        self.text_objs[oid] = DesignObject(index, oid, "Text", (0, 0, 0), polys, color=(0, 0, 0))
        self.texts[oid] = spec
        self.order_ids.append(oid)
        self.mats[oid] = self.home[oid] = IDENTITY       # centred in the hoop
        self.last_text = spec
        self.fill_objects()
        self.tree.selection_set([oid])
        self.tree.see(oid)
        self.refresh()
        if missing:
            self.set_status(f"Not in this font, left out: {missing}", WARN_COLOUR)
        elif not self.outside:
            self.set_status("Text added in the middle of the hoop: drag it into place. "
                            "Double-click it to edit.", OK_COLOUR)

    def edit_text(self, oid: str):
        old = self.texts[oid]
        spec = TextDialog(self.root, self.ui, old, editing=True).wait()
        if spec is None or spec == old:
            return
        polys, missing = text_polylines(spec)
        if not polys:
            self.set_status("That text has no letters this font can draw.", ERROR_COLOUR)
            return
        self.push_undo()
        o = self.text_objs[oid]
        old = [q for pl in o.polylines for q in pl.points]
        if old:
            ob = bounds(old)
            cx, cy = (ob[0] + ob[2]) / 2, (ob[1] + ob[3]) / 2
            polys = [Polyline([(x + cx, y + cy) for x, y in pl.points], pl.closed) for pl in polys]
        self.text_objs[oid] = DesignObject(o.index, oid, "Text", o.source_color, polys,
                                           color=o.source_color)
        self.texts[oid] = spec
        self.last_text = spec
        self.fill_objects()
        self.refresh()
        if missing:
            self.set_status(f"Not in this font, left out: {missing}", WARN_COLOUR)

    # ---------------------------------------------------------------- arranging
    def apply_transform(self, m: Affine, ids=None):
        ids = list(ids if ids is not None else self.target())
        if not ids:
            return
        self.push_undo()
        for i in ids:
            self.mats[i] = chain(self.mats[i], m)
        self.refresh()

    def _fmt(self, v: float) -> str:
        return f"{v:.1f}"

    def update_arrange_fields(self):
        sel = self.selection()
        if not self.order_ids:
            self.l_target.configure(text="Nothing to arrange yet")
        elif sel:
            self.l_target.configure(text=f"Selected: {', '.join(sel[:4])}"
                                         f"{' …' if len(sel) > 4 else ''}")
        else:
            self.l_target.configure(text="Everything (nothing selected)")
        b = self.target_bounds()
        vals = {"w": "", "h": "", "x": "", "y": ""}
        if b:
            x0, y0, x1, y1 = b
            vals = {"w": self._fmt(x1 - x0), "h": self._fmt(y1 - y0),
                    "x": self._fmt((x0 + x1) / 2), "y": self._fmt((y0 + y1) / 2)}
        for k, var in (("w", self.v_w), ("h", self.v_h), ("x", self.v_x), ("y", self.v_y)):
            var.set(vals[k])
        self._shown = vals

    def _num(self, var: tk.StringVar, label: str):
        try:
            return float(var.get())
        except ValueError:
            self.set_status(f"{label} must be a number.", ERROR_COLOUR)
            return None

    def apply_size(self):
        b = self.target_bounds()
        if not b or (self.v_w.get() == self._shown.get("w") and self.v_h.get() == self._shown.get("h")):
            return
        w, h = self._num(self.v_w, "Width"), self._num(self.v_h, "Height")
        if w is None or h is None:
            return
        if w <= 0 or h <= 0:
            self.set_status("Size must be more than 0.", ERROR_COLOUR)
            self.update_arrange_fields()
            return
        x0, y0, x1, y1 = b
        cw, ch = x1 - x0, y1 - y0
        sx = w / cw if cw > 1e-6 else 1.0
        sy = h / ch if ch > 1e-6 else 1.0
        if self.v_ratio.get():
            if self.v_w.get() != self._shown.get("w") or ch <= 1e-6:
                sy = sx
            else:
                sx = sy
        self.apply_transform(scale(sx, sy, (x0 + x1) / 2, (y0 + y1) / 2))

    def apply_position(self):
        b = self.target_bounds()
        if not b or (self.v_x.get() == self._shown.get("x") and self.v_y.get() == self._shown.get("y")):
            return
        x, y = self._num(self.v_x, "X"), self._num(self.v_y, "Y")
        if x is None or y is None:
            return
        x0, y0, x1, y1 = b
        self.apply_transform(translate(x - (x0 + x1) / 2, y - (y0 + y1) / 2))

    def rotate_by(self, degrees: float):
        b = self.target_bounds()
        if b:
            x0, y0, x1, y1 = b
            self.apply_transform(rotate(degrees, (x0 + x1) / 2, (y0 + y1) / 2))

    def rotate_by_entry(self):
        deg = self._num(self.v_rot, "Angle")
        if deg is not None:
            self.rotate_by(deg)

    def centre_target(self):
        b = self.target_bounds()
        if b:
            x0, y0, x1, y1 = b
            self.apply_transform(translate(-(x0 + x1) / 2, -(y0 + y1) / 2))

    def fit_target(self):
        b = self.target_bounds()
        if not b:
            return
        x0, y0, x1, y1 = b
        aw, ah = self.hoop[0] - 2 * self.margin, self.hoop[1] - 2 * self.margin
        limits = [a / d for a, d in ((aw, x1 - x0), (ah, y1 - y0)) if d > 1e-6]
        if not limits:
            return
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.apply_transform(chain(translate(-cx, -cy), scale(min(limits))))

    def reset_target(self):
        ids = self.target()
        if not ids:
            return
        self.push_undo()
        for i in ids:
            self.mats[i] = self.home[i]
        self.refresh()

    def nudge(self, dx, dy, event):
        sel = self.selection()
        if not sel:
            return "break"
        step = 0.1 if event.state & SHIFT else 1.0
        self.apply_transform(translate(dx * step, dy * step), sel)
        return "break"

    # ---------------------------------------------------------------- preview mouse
    def hit_test(self, cx, cy) -> tuple[str | None, bool]:
        """The object under a canvas point, and whether the point is on its outline
        (True) or only inside a closed shape (False)."""
        p = self.from_canvas(cx, cy)
        tol = 6 * self.ui / self.px
        best = None
        for oid in reversed(self.order_ids):
            if oid in self.skipped:
                continue
            for pts in self.placed.get(oid, []):
                d = min((_seg_dist(p, a, b) for a, b in zip(pts, pts[1:])), default=1e9)
                if d <= tol:
                    score = d
                elif len(pts) > 3 and math.dist(pts[0], pts[-1]) < 1e-6 and _inside(p, pts):
                    score = tol + _area(pts)          # prefer the smallest shape clicked inside
                else:
                    continue
                if best is None or score < best[0]:
                    best = (score, oid)
        if best is None:
            return None, False
        return best[1], bool(best[0] <= tol)

    def objects_in_box(self, c0, c1) -> list[str]:
        """Objects lying entirely inside the rectangle between two canvas points
        (skipped objects included, so they stay in place with the rest)."""
        (x0, y0), (x1, y1) = self.from_canvas(*c0), self.from_canvas(*c1)
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        out = []
        for oid in self.order_ids:
            b = self.target_bounds([oid])
            if b and x0 <= b[0] and y0 <= b[1] and b[2] <= x1 and b[3] <= y1:
                out.append(oid)
        return out

    def _near(self, p, q) -> bool:
        return math.dist(p, q) <= 7 * self.ui

    def on_hover(self, e):
        if self.drag:
            return
        cursor = ""
        if self.handles:
            if self._near((e.x, e.y), self.handles["rot"]):
                cursor = "exchange"
            elif any(self._near((e.x, e.y), self.handles[k]) for k in ("nw", "ne", "se", "sw")):
                cursor = "sizing"
            elif self._in_box(e.x, e.y):
                cursor = "fleur"
        if not cursor and self.order_ids and self.hit_test(e.x, e.y)[1]:
            cursor = "hand2"
        self.canvas.configure(cursor=cursor)

    def _in_box(self, cx, cy) -> bool:
        b = self.target_bounds(self.selection())
        if not b:
            return False
        x0, y0 = self.to_canvas(b[0], b[1])
        x1, y1 = self.to_canvas(b[2], b[3])
        return x0 - 4 <= cx <= x1 + 4 and y0 - 4 <= cy <= y1 + 4

    def on_press(self, e):
        self.canvas.focus_set()
        if not self.order_ids:
            return
        p = (e.x, e.y)
        additive = bool(e.state & (SHIFT | CONTROL))
        mode = None
        if self.handles and not additive:
            if self._near(p, self.handles["rot"]):
                mode = "rotate"
            elif any(self._near(p, self.handles[k]) for k in ("nw", "ne", "se", "sw")):
                mode = "scale"
            elif self._in_box(*p):
                mode = "move"
        self.drag = None
        if mode is None:
            hit, on_line = self.hit_test(*p)
            if hit is not None and on_line:
                if additive:
                    self.tree.selection_toggle(hit)
                    return
                if hit not in self.tree.selection():
                    self.tree.selection_set(self.expand_groups([hit]))
                mode = "move"
            else:
                # Empty space, or inside a shape: dragging draws a selection box,
                # a plain click selects that shape (or clears the selection).
                self.drag = {"mode": "box", "start": p, "moved": False, "hit": hit,
                             "additive": additive,
                             "base": list(self.selection()) if additive else []}
                return
        if mode:
            b = self.target_bounds(self.selection())
            if b:
                self.drag = {"mode": mode, "start": p, "moved": False, "m": None,
                             "centre": ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)}

    def on_motion(self, e):
        d = self.drag
        if not d:
            return
        sx, sy = d["start"]
        if not d["moved"] and abs(e.x - sx) + abs(e.y - sy) < 3:
            return
        d["moved"] = True
        if d["mode"] == "box":
            self.draw_box(d["start"], (e.x, e.y), d["base"])
            return
        cx, cy = d["centre"]
        ccx, ccy = self.to_canvas(cx, cy)
        if d["mode"] == "move":
            mx, my = (e.x - sx) / self.px, (e.y - sy) / self.px
            m = translate(mx, my)
            msg = f"Move {mx:+.1f}, {my:+.1f} mm"
        elif d["mode"] == "scale":
            r0 = math.dist((sx, sy), (ccx, ccy))
            k = max(0.05, math.dist((e.x, e.y), (ccx, ccy)) / r0) if r0 > 2 else 1.0
            m = scale(k, k, cx, cy)
            msg = f"Size {k:.0%}"
        else:
            deg = math.degrees(math.atan2(e.y - ccy, e.x - ccx) - math.atan2(sy - ccy, sx - ccx))
            deg = (deg + 180) % 360 - 180
            if e.state & SHIFT:
                deg = round(deg / 15) * 15
            m = rotate(deg, cx, cy)
            msg = f"Rotate {deg:+.0f}°"
        d["m"] = m
        self.draw_overlay(m)
        self.set_status(msg)

    def on_release(self, e):
        d, self.drag = self.drag, None
        if d and d["mode"] == "box":
            self.canvas.delete("box")
            if d["moved"]:
                caught = self.objects_in_box(d["start"], (e.x, e.y))
                chosen = self.expand_groups(list(dict.fromkeys(d["base"] + caught)))
                self.tree.selection_set(chosen) if chosen else \
                    self.tree.selection_remove(self.tree.selection())
                self.set_status(f"Selected {len(chosen)} object(s)." if chosen else
                                "Nothing was entirely inside the box.",
                                "#333333" if chosen else WARN_COLOUR)
            elif d["hit"] is not None:
                if d["additive"]:
                    self.tree.selection_toggle(d["hit"])
                else:
                    self.tree.selection_set(self.expand_groups([d["hit"]]))
            elif not d["additive"]:
                self.tree.selection_remove(self.tree.selection())
            return
        if d and d["moved"] and d["m"] is not None:
            self.apply_transform(d["m"], self.selection())
        else:
            self.draw_overlay()

    def draw_box(self, c0, c1, base: list[str]):
        """Rubber-band rectangle, with the objects it would select highlighted."""
        cv = self.canvas
        cv.delete("box")
        caught = self.objects_in_box(c0, c1)
        for oid in dict.fromkeys(base + caught):
            for pts in self.placed.get(oid, []):
                cv.create_line(*[c for x, y in pts for c in self.to_canvas(x, y)],
                               fill=SELECT_COLOUR, width=max(2, round(2 * self.ui)), tags="box")
        cv.create_rectangle(*c0, *c1, outline=SELECT_COLOUR, dash=(4, 3), tags="box")
        self.set_status(f"Box: {len(caught)} object(s) entirely inside"
                        + (f", {len(base)} already selected" if base else ""))

    def on_select(self):
        self.draw_overlay()
        self.update_arrange_fields()

    # ---------------------------------------------------------------- planning
    def schedule(self, delay: int = 300):
        if self._job is not None:
            self.root.after_cancel(self._job)
        self._job = self.root.after(delay, self.refresh)

    def _read_hoop(self) -> None:
        try:
            hoop = (float(self.v_hoop_w.get()), float(self.v_hoop_h.get()))
            margin = float(self.v_margin.get())
        except ValueError:
            raise ValueError("Hoop size and margin must be numbers") from None
        if hoop[0] > 0 and hoop[1] > 0:
            self.hoop, self.margin = hoop, margin

    def refresh(self):
        self._job = None
        self.placed = {}
        if not self.order_ids:
            self.plan = None
            self.track = Track()
            self.sim_reset()
            self.b_save.configure(state="disabled")
            self.fill_order()
            self.l_info.configure(text="")
            self.update_arrange_fields()
            self.draw_preview()
            return
        objs = []
        for oid in self.order_ids:
            o = self.obj(oid)
            polys = [Polyline(apply(self.mats[oid], pl.points), pl.closed) for pl in o.polylines]
            self.placed[oid] = [pl.points for pl in polys]
            objs.append(DesignObject(o.index, oid, o.kind, o.source_color, polys,
                                     color=self.colours.get(oid, o.source_color),
                                     skipped=oid in self.skipped,
                                     fill=self._placed_fill(oid),
                                     outline=oid not in self.no_outline,
                                     satin=self.satins.get(oid),
                                     stitches=o.stitches))
        design = Design(objs, list(self.warnings))
        try:
            self._read_hoop()
            stitch = float(self.v_stitch.get())
            spm = max(100, int(float(self.v_spm.get())))
            plan = plan_design(design, hoop_mm=self.hoop, margin_mm=self.margin,
                               settings=Settings(stitch_len=stitch, keep_order=self.v_keep.get(),
                                                 knockout=self.v_knockout.get(), machine_spm=spm,
                                                 color_order=tuple(self.color_order)),
                               placed=True)
        except ValueError:
            plan, err = None, "Hoop, margin, stitch length and machine speed must be numbers."
        except ConversionError as e:
            plan, err = None, str(e)
        self.plan = plan
        self.track = Track(plan.pattern) if plan else Track()
        self.sim_reset()
        self.outside = outside_hoop(design, self.hoop, self.margin) if plan else []
        self.fill_order()
        self.update_arrange_fields()
        self.draw_preview()
        if plan is None:
            self.b_save.configure(state="disabled")
            self.l_info.configure(text="")
            self.set_status(err, ERROR_COLOUR)
            return
        rep = plan.report
        self.l_info.configure(
            text=f"{rep.width_mm:.1f} × {rep.height_mm:.1f} mm  ·  {rep.stitch_count} stitches"
                 f"  ·  {rep.color_blocks} colours  ·  about {format_minutes(rep.total_minutes)}")
        if self.outside:
            self.b_save.configure(state="disabled")
            self.set_status(f"Outside the sewing area: {', '.join(self.outside)}. Drag it in, "
                            "or use Arrange → Fit to hoop.", ERROR_COLOUR)
            return
        self.b_save.configure(state="normal")
        warnings = sorted(set(rep.warnings))
        if warnings:
            self.set_status("Note: " + "   ·   ".join(warnings), WARN_COLOUR)
        else:
            self.set_status("Ready. Save the PES when the preview looks right (Ctrl+S).", OK_COLOUR)

    def fill_order(self):
        keep = self.order.selection()
        self.order.delete(*self.order.get_children())
        self._order_images.clear()
        if self.plan is None:
            self.l_time.configure(text="")
            return
        rep = self.plan.report
        size = int(16 * self.ui)
        spm = rep.stitch_count / rep.sew_minutes if rep.sew_minutes else 650
        for i, tu in enumerate(rep.thread_map, 1):
            img = swatch(self.root, [to_hex(tu.rgb)], size)
            self._order_images.append(img)
            name = f"{tu.thread.number}  {tu.thread.name}" if tu.thread else to_hex(tu.rgb)
            self.order.insert("", "end", iid=str(i - 1), text=f" {i}", image=img,
                              values=(name, tu.stitches, format_minutes(tu.stitches / spm),
                                      ", ".join(tu.object_ids)))
        if keep and self.order.exists(keep[0]):
            self.order.selection_set(keep)
        self.l_time.configure(
            text=f"About {format_minutes(rep.total_minutes)}: {format_minutes(rep.sew_minutes)} "
                 f"sewing + {format_minutes(rep.change_minutes)} for {rep.color_blocks - 1} "
                 f"thread change(s)")

    def _stop_keys(self, tu) -> list:
        """(phase, rgb) keys of the parts sewn in one colour stop (fills, then outlines)."""
        phases = sorted({0 if lab.endswith(" fill") else 1 for lab in tu.object_ids})
        return [(ph, tuple(tu.rgb)) for ph in phases]

    def move_stop(self, step: int):
        """Move the selected colour stop earlier or later in the sewing order."""
        sel = self.order.selection()
        if not sel or self.plan is None:
            self.set_status("Select a colour stop in the Sewing order list first.", WARN_COLOUR)
            return
        if self.v_keep.get():
            self.set_status("Untick 'Keep drawing order' (Stitching tab) to reorder colour stops.",
                            WARN_COLOUR)
            return
        tmap = self.plan.report.thread_map
        i = int(sel[0])
        j = i + step
        if not 0 <= j < len(tmap):
            return
        keys = [self._stop_keys(tu) for tu in tmap]
        before = [k for ks in keys for k in ks]
        keys[i], keys[j] = keys[j], keys[i]
        # fills always come before outlines: keep the new order within each group, so a
        # stop holding a fill and an outline moves its fill among the fills
        order = sorted((k for ks in keys for k in ks), key=lambda k: k[0])
        if order == before:
            self.set_status("Fills are always sewn before outlines, so this stop can't move past "
                            "that one.", WARN_COLOUR)
            return
        self.push_undo()
        self.color_order = order
        self.refresh()
        # keep the moved stop selected (find it by its first key)
        moved = keys[j][0]
        for n, tu in enumerate(self.plan.report.thread_map if self.plan else []):
            if moved in self._stop_keys(tu):
                self.order.selection_set(str(n))
                break

    def auto_order(self):
        if self.color_order:
            self.push_undo()
            self.color_order = []
            self.refresh()
        self.set_status("Automatic sewing order: fewest colour changes, fills before outlines.")

    # ---------------------------------------------------------------- preview drawing
    def _schedule_draw(self):
        if self._draw_job is not None:
            self.root.after_cancel(self._draw_job)
        self._draw_job = self.root.after(80, self.draw_preview)

    def _style(self) -> Style:
        return Style(show_jumps=self.v_jumps.get(), needle_points=self.v_dots.get())

    def _view(self, ss: int) -> View:
        x0, y0 = self.from_canvas(0, 0)
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        return View(x0, y0, self.px * ss, cw * ss, ch * ss, ss)

    def draw_preview(self, fast: bool = False):
        """Redraw the preview. fast=True draws without smoothing (used while
        zooming, panning and playing) and schedules a smooth redraw afterwards."""
        self._draw_job = None
        if self._hq_job is not None:
            self.root.after_cancel(self._hq_job)
            self._hq_job = None
        cv = self.canvas
        cv.delete("all")
        self.handles = {}
        cw, ch = cv.winfo_width(), cv.winfo_height()
        if cw < 20 or ch < 20:
            return
        if not self.order_ids:
            cv.create_text(cw / 2, ch / 2, text="Open an SVG or add text to start.",
                           fill="#777777", font=("Segoe UI", 11))
            return
        hw, hh = self.hoop
        self.px = max(0.2, min((cw - 24) / hw, (ch - 24) / hh)) * self.zoom
        vx, vy = self.view_c
        self.origin = (cw / 2 - vx * self.px, ch / 2 - vy * self.px)
        self.l_zoom.configure(text=f"{self.zoom:.0%}")
        ss = 1 if (fast or self.sim_playing) else 2      # 2x then shrink smooths the lines
        img = render_view(self.track, self._view(ss), self.hoop, self.margin, self._style(),
                          self.sim_pos)
        if ss > 1:
            img = img.resize((cw, ch), Image.LANCZOS)
        else:
            self._sim_img, self._sim_drawn = img, len(self.track) if self.sim_pos is None else self.sim_pos
        self._photo = ImageTk.PhotoImage(img, master=self.root)
        cv.create_image(0, 0, anchor="nw", image=self._photo, tags="image")
        for oid in self.outside:                       # mark what sticks out in red
            for pts in self.placed.get(oid, []):
                cv.create_line(*[c for x, y in pts for c in self.to_canvas(x, y)],
                               fill=ERROR_COLOUR, width=1, dash=(3, 2))
        self.draw_needle()
        self.draw_overlay()
        if fast and not self.sim_playing:
            self._hq_job = self.root.after(220, self.draw_preview)

    # ---------------------------------------------------------------- zoom and pan
    def zoom_at(self, factor: float, cx: float, cy: float):
        """Zoom by factor, keeping the point under canvas (cx, cy) still."""
        new = max(0.5, min(MAX_ZOOM, self.zoom * factor))
        if abs(new - self.zoom) < 1e-9 or not self.order_ids:
            return
        mx, my = self.from_canvas(cx, cy)
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.zoom = new
        px = max(0.2, min((cw - 24) / self.hoop[0], (ch - 24) / self.hoop[1])) * new
        self.view_c = (mx - (cx - cw / 2) / px, my - (cy - ch / 2) / px)
        self.draw_preview(fast=True)

    def zoom_step(self, factor: float):
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.zoom_at(factor, cw / 2, ch / 2)
        return "break"

    def zoom_fit(self):
        self.zoom, self.view_c = 1.0, (0.0, 0.0)
        self.draw_preview()
        return "break"

    def on_wheel(self, e):
        self.zoom_at(1.25 if e.delta > 0 else 0.8, e.x, e.y)
        return "break"

    def pan_start(self, e):
        self._pan = (e.x, e.y, self.view_c)
        self.canvas.configure(cursor="fleur")

    def pan_move(self, e):
        if self._pan:
            sx, sy, (vx, vy) = self._pan
            self.view_c = (vx - (e.x - sx) / self.px, vy - (e.y - sy) / self.px)
            self.draw_preview(fast=True)

    def pan_end(self, e):
        self._pan = None
        self.canvas.configure(cursor="")

    # ---------------------------------------------------------------- sew-out simulation
    def _thread_label(self, ci: int) -> str:
        tmap = self.plan.report.thread_map if self.plan else []
        if 0 <= ci < len(tmap):
            tu = tmap[ci]
            return f"{tu.thread.number} {tu.thread.name}" if tu.thread else to_hex(tu.rgb)
        return ""

    def sim_reset(self):
        """Back to showing the whole design (called whenever the design changes)."""
        self.sim_pause()
        self.sim_pos = None
        n = len(self.track)
        self.s_sim.configure(to=max(1, n))
        self._set_slider(n)
        self._update_sim_label()

    def _update_sim_label(self):
        t = self.track
        n = len(t)
        if not n:
            self.l_sim.configure(text="Sew-out: nothing to sew yet.")
            return
        blocks = (t.colour[-1] + 1) if n else 0
        if self.sim_pos is None:
            self.l_sim.configure(text=f"Sew-out: {t.total_stitches} stitches in {blocks} colour(s). "
                                      "Press ▶ Sew to watch it stitched in order.   "
                                      "Scroll to zoom, right-drag to pan.")
            return
        i = max(0, self.sim_pos - 1)
        sewn = t.stitch_no[i] if self.sim_pos else 0
        ci = t.colour[i] if self.sim_pos else 0
        self.l_sim.configure(text=f"Stitch {sewn} of {t.total_stitches}   ·   colour {ci + 1} of "
                                  f"{blocks}: {self._thread_label(ci)}")

    def draw_needle(self):
        cv = self.canvas
        cv.delete("needle")
        if self.sim_pos is None or not len(self.track):
            return
        i = max(0, min(self.sim_pos, len(self.track)) - 1)
        x, y = self.to_canvas(self.track.x[i], self.track.y[i])
        r = 7 * self.ui
        cv.create_oval(x - r, y - r, x + r, y + r, outline=NEEDLE_COLOUR, width=2, tags="needle")
        cv.create_line(x - 2 * r, y, x - r / 2, y, fill=NEEDLE_COLOUR, width=2, tags="needle")
        cv.create_line(x + r / 2, y, x + 2 * r, y, fill=NEEDLE_COLOUR, width=2, tags="needle")
        cv.create_line(x, y - 2 * r, x, y - r / 2, fill=NEEDLE_COLOUR, width=2, tags="needle")
        cv.create_line(x, y + r / 2, x, y + 2 * r, fill=NEEDLE_COLOUR, width=2, tags="needle")

    def _set_sim_pos(self, pos: int, smooth: bool):
        n = len(self.track)
        self.sim_pos = max(0, min(pos, n))
        self._set_slider(self.sim_pos)
        self._update_sim_label()
        if smooth or self._sim_img is None or self.sim_pos < self._sim_drawn:
            self.draw_preview(fast=not smooth)
            return
        # playing forward: draw only the new stitches onto the current image
        draw_track(self._sim_img, self.track, self._view(1), self._sim_drawn, self.sim_pos,
                   self._style())
        self._sim_drawn = self.sim_pos
        try:
            self._photo.paste(self._sim_img)
        except (ValueError, tk.TclError):         # canvas was resized: redraw instead
            self.draw_preview(fast=True)
            return
        self.draw_needle()

    def _set_slider(self, value):
        """Move the slider from code without it counting as the user dragging it."""
        self._slide_lock = True
        try:
            self.v_sim.set(value)
        finally:
            self._slide_lock = False

    def _on_slide(self, value):
        if self._slide_lock or not len(self.track):
            return
        pos = int(float(value))
        if pos != self.sim_pos:
            self._set_sim_pos(pos, smooth=not self.sim_playing)

    def sim_toggle(self):
        if self.sim_playing:
            self.sim_pause()
            self.draw_preview()
        else:
            self.sim_play()

    def sim_play(self):
        if not len(self.track):
            return
        if self.sim_pos is None or self.sim_pos >= len(self.track):
            self.sim_pos = 0
        self.sim_playing = True
        self._sim_acc = 0.0
        self.b_play.configure(text="⏸ Pause")
        self.draw_preview(fast=True)
        self._sim_job = self.root.after(SIM_FRAME_MS, self._sim_tick)

    def sim_pause(self):
        self.sim_playing = False
        if self._sim_job is not None:
            self.root.after_cancel(self._sim_job)
            self._sim_job = None
        if hasattr(self, "b_play"):
            self.b_play.configure(text="▶ Sew")

    def sim_start(self):
        self.sim_pause()
        if len(self.track):
            self._set_sim_pos(0, smooth=True)

    def sim_end(self):
        self.sim_pause()
        self.sim_pos = None
        self._set_slider(len(self.track))
        self._update_sim_label()
        self.draw_preview()

    def _sim_tick(self):
        self._sim_job = None
        if not self.sim_playing:
            return
        t = self.track
        n = len(t)
        self._sim_acc += SIM_SPEEDS.get(self.v_speed.get(), 60) * SIM_FRAME_MS / 1000
        budget = int(self._sim_acc)            # stitches to sew this frame
        if budget == 0:                        # slow speeds: not every frame sews one
            self._sim_job = self.root.after(SIM_FRAME_MS, self._sim_tick)
            return
        self._sim_acc -= budget
        pos = self.sim_pos or 0
        goal = (t.stitch_no[pos - 1] if pos else 0) + budget
        stopped_at = None
        while pos < n:
            if t.kind[pos] == CHANGE and pos > 0 and self.v_stop_change.get():
                stopped_at = pos
                pos += 1
                break
            pos += 1
            if t.stitch_no[pos - 1] >= goal:
                break
        self._set_sim_pos(pos, smooth=False)
        if stopped_at is not None:
            self.sim_pause()
            self.draw_preview()
            self.set_status(f"Colour change: put on thread {self._thread_label(t.colour[stopped_at])}, "
                            "then press ▶ Sew to carry on.", WARN_COLOUR)
            return
        if pos >= n:
            self.sim_pause()
            self.draw_preview()
            changes = t.colour[-1] if n else 0
            self.set_status(f"Sew-out finished: {t.total_stitches} stitches, {changes} colour "
                            f"change(s).", OK_COLOUR)
            return
        self._sim_job = self.root.after(SIM_FRAME_MS, self._sim_tick)

    def draw_overlay(self, m: Affine | None = None):
        """Selection box and handles; with m, the selection as it would look after m."""
        cv = self.canvas
        cv.delete("overlay")
        self.handles = {}
        sel = self.selection()
        b = self.target_bounds(sel) if sel else None
        if not b or self.px <= 0:
            return
        if m is not None:
            for oid in sel:
                for pts in self.placed.get(oid, []):
                    cv.create_line(*[c for x, y in apply(m, pts) for c in self.to_canvas(x, y)],
                                   fill=SELECT_COLOUR, width=max(1, round(self.ui)), tags="overlay")
        pad = 1.0 / self.px * 4
        x0, y0, x1, y1 = b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        top = ((x0 + x1) / 2, y0)
        if m is not None:
            corners = apply(m, corners)
            top = apply(m, [top])[0]
        cc = [self.to_canvas(x, y) for x, y in corners]
        cv.create_polygon(*[c for p in cc for c in p], outline=SELECT_COLOUR, fill="",
                          dash=(4, 3), tags="overlay")
        if m is not None:
            return
        hs = 4 * self.ui
        for key, (hx, hy) in zip(("nw", "ne", "se", "sw"), cc):
            cv.create_rectangle(hx - hs, hy - hs, hx + hs, hy + hs, fill="white",
                                outline=SELECT_COLOUR, tags="overlay")
            self.handles[key] = (hx, hy)
        tx, ty = self.to_canvas(*top)
        rx, ry = tx, ty - 22 * self.ui
        cv.create_line(tx, ty, rx, ry, fill=SELECT_COLOUR, tags="overlay")
        cv.create_oval(rx - hs - 1, ry - hs - 1, rx + hs + 1, ry + hs + 1, fill="white",
                       outline=SELECT_COLOUR, width=2, tags="overlay")
        self.handles["rot"] = (rx, ry)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)        # crisp text on high-DPI screens
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("svg2pes")  # own taskbar icon
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    try:
        if sys.platform == "win32":
            root.iconbitmap(default=os.path.join(assets, "icon.ico"))
        else:
            root.iconphoto(True, tk.PhotoImage(file=os.path.join(assets, "icon.png")))
    except tk.TclError:
        pass  # no icon is not worth failing over
    App(root, argv[0] if argv else None)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
