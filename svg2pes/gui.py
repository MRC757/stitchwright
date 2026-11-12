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
import re
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk
from pyembroidery import EmbPattern

from .convert import ConversionError, Plan, outside_hoop, plan_design, write_pes_file
from .geometry import IDENTITY, Affine, apply, bounds, chain, rotate, scale, translate
from .palette import Thread, brother_threads, nearest_brother, thread_for_rgb, to_hex
from .preview import BACKGROUND, render_image
from .stitching import Settings
from .svg_import import Design, DesignObject, Polyline, load_svg
from .text import TextSpec, default_font, installed_fonts, text_polylines

TITLE = "svg2pes – SVG to Brother PES"
OK_COLOUR, WARN_COLOUR, ERROR_COLOUR = "#2e7d32", "#9a6700", "#c62828"
SELECT_COLOUR = "#1a73e8"
SHIFT, CONTROL = 0x0001, 0x0004
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

        self.cv = tk.Canvas(f, width=int(480 * ui), height=int(150 * ui), bg="white",
                            highlightthickness=1, highlightbackground="#cccccc")
        self.cv.grid(row=5, column=0, columnspan=4, pady=(8, 2))
        self.info = ttk.Label(f, text="", foreground="#555555")
        self.info.grid(row=6, column=0, columnspan=4, sticky="w")

        b = ttk.Frame(f)
        b.grid(row=7, column=0, columnspan=4, sticky="e", pady=(8, 0))
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(b, text="OK", command=self._ok).pack(side="right", padx=6)

        self.t_text.bind("<KeyRelease>", lambda e: self._schedule())
        self.c_font.bind("<KeyRelease>", self._filter)
        self.c_font.bind("<<ComboboxSelected>>", lambda e: self._schedule())
        for v in (self.v_height, self.v_spacing, self.v_align):
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
        return TextSpec(self.t_text.get("1.0", "end-1c"), path, name, idx, height,
                        self.v_align.get(), spacing)

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


# ------------------------------------------------------------------ main window
class App:
    def __init__(self, root: tk.Tk, path: str | None = None):
        self.root = root
        self.ui = max(1.0, root.winfo_fpixels("1i") / 96)
        self.svg_path: str | None = None
        self.svg_map: dict[str, DesignObject] = {}
        self.text_objs: dict[str, DesignObject] = {}
        self.texts: dict[str, TextSpec] = {}
        self.order_ids: list[str] = []
        self.mats: dict[str, Affine] = {}      # outline -> hoop placement, per object
        self.home: dict[str, Affine] = {}      # placement when first opened / added
        self.colours: dict[str, tuple[int, int, int]] = {}   # obj_id -> chosen thread rgb
        self.skipped: set[str] = set()
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
        self.v_pes = tk.StringVar(value="1")
        self.v_jumps = tk.BooleanVar(value=True)
        self.v_status = tk.StringVar()
        self.v_w, self.v_h = tk.StringVar(), tk.StringVar()
        self.v_x, self.v_y = tk.StringVar(), tk.StringVar()
        self.v_rot = tk.StringVar(value="15")
        self.v_ratio = tk.BooleanVar(value=True)
        for v in (self.v_hoop_w, self.v_hoop_h, self.v_margin, self.v_stitch, self.v_keep):
            v.trace_add("write", lambda *a: self.schedule())
        self.v_jumps.trace_add("write", lambda *a: self.draw_preview())

        self._build()
        self.set_status("Open an SVG (Ctrl+O) or add text (Ctrl+T) to start.")
        if path:
            self.root.after(50, lambda: self.open(path))

    # ---------------------------------------------------------------- layout
    def _build(self):
        r, ui = self.root, self.ui
        r.title(TITLE)
        r.geometry(f"{int(1200 * ui)}x{int(780 * ui)}")
        r.minsize(int(940 * ui), int(620 * ui))
        style = ttk.Style(r)
        style.configure("Treeview", rowheight=int(22 * ui))

        bar = ttk.Frame(r, padding=(8, 8, 8, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="Open SVG…", command=self.ask_open).pack(side="left")
        ttk.Button(bar, text="Add text…", command=self.add_text).pack(side="left", padx=(6, 0))
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
        cols = ("id", "kind", "thread", "sew")
        self.tree = ttk.Treeview(f_obj, columns=cols, show="tree headings", height=7,
                                 selectmode="extended")
        self.tree.heading("#0", text="#", anchor="w")
        self.tree.column("#0", width=int(88 * ui), stretch=False)
        for c, text, w in (("id", "Object", 90), ("kind", "Shape", 120),
                           ("thread", "Thread", 140), ("sew", "Sew", 40)):
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
        ttk.Button(btns, text="Sew / skip", command=self.toggle_skip).pack(side="left", padx=6)
        ttk.Button(btns, text="Delete text", command=self.delete_selected).pack(side="left")
        ttk.Button(btns, text="Reset all", command=self.reset_all).pack(side="left", padx=6)
        ttk.Label(f_obj, foreground="#666666",
                  text="Double-click: choose thread (or edit text).  Space: sew/skip.") \
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
        ttk.Label(f_arr, **grey, justify="left", text=(
            "In the preview: drag a box around objects to select them (Ctrl+A or\n"
            "Select all for everything, Esc for none). Drag the selection to move it;\n"
            "corner handles resize, the round handle rotates (Shift: 15° steps).\n"
            "Arrow keys nudge 1 mm (Shift: 0.1 mm).")) \
            .grid(row=5, column=0, columnspan=4, sticky="w", pady=(4, 0))
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

        # Sewing order ------------------------------------------------------
        f_ord = ttk.Frame(tabs, padding=8)
        tabs.add(f_ord, text=" Sewing order ")
        self.order = ttk.Treeview(f_ord, columns=("thread", "objects"), show="tree headings",
                                  height=6, selectmode="none")
        self.order.heading("#0", text="Stop", anchor="w")
        self.order.column("#0", width=int(70 * ui), stretch=False)
        self.order.heading("thread", text="Thread", anchor="w")
        self.order.column("thread", width=int(150 * ui), stretch=False)
        self.order.heading("objects", text="Objects", anchor="w")
        self.order.column("objects", width=int(200 * ui))
        self.order.pack(fill="both", expand=True)

        # Preview -----------------------------------------------------------
        self.canvas = tk.Canvas(right, bg=to_hex(BACKGROUND), highlightthickness=1,
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
        under = ttk.Frame(right)
        under.pack(fill="x", pady=(6, 0))
        self.l_info = ttk.Label(under, text="")
        self.l_info.pack(side="left")
        ttk.Checkbutton(under, text="Show jumps", variable=self.v_jumps).pack(side="right")

        r.bind_all("<Control-o>", lambda e: self._main_key(e, self.ask_open))
        r.bind_all("<Control-s>", lambda e: self._main_key(e, self.save))
        r.bind_all("<Control-t>", lambda e: self._main_key(e, self.add_text))
        r.bind_all("<Control-z>", lambda e: self._main_key(e, self.undo, text_ok=False))
        r.bind_all("<Control-y>", lambda e: self._main_key(e, self.redo, text_ok=False))
        r.bind_all("<Control-Z>", lambda e: self._main_key(e, self.redo, text_ok=False))
        r.bind_all("<Control-a>", lambda e: self._main_key(e, self.select_all, text_ok=False))
        r.bind_all("<Escape>", lambda e: self._main_key(e, self.select_none, text_ok=False))

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
        return self.text_objs.get(obj_id) or self.svg_map[obj_id]

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
                dict(self.texts), dict(self.text_objs), list(self.order_ids))

    def _restore(self, st):
        (self.mats, self.home, self.colours, self.skipped, self.texts, self.text_objs,
         self.order_ids) = st
        self.fill_objects()
        self.refresh()

    def push_undo(self):
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
    def ask_open(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Open SVG artwork",
            filetypes=[("SVG artwork", "*.svg"), ("All files", "*.*")])
        if path:
            self.open(path)

    def open(self, path: str):
        try:
            design = load_svg(path)
        except (OSError, ValueError) as e:
            messagebox.showerror(TITLE, f"Could not open {path}:\n\n{e}", parent=self.root)
            return
        if not design.objects:
            messagebox.showerror(TITLE, "No stitchable shapes were found in this SVG.\n\n"
                                 + "\n".join(sorted(set(design.warnings))), parent=self.root)
            return
        try:
            self._read_hoop()
        except ValueError:
            pass            # keep the last good hoop size
        self.svg_path = path
        self.svg_map = {o.obj_id: o for o in design.objects}
        self.text_objs.clear()
        self.texts.clear()
        self.colours.clear()
        self.skipped.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._undo_buttons()
        self.order_ids = [o.obj_id for o in design.objects]
        self.warnings = ["Text in the SVG was ignored: use Add text… instead" if "Text element" in w
                         else w for w in design.warnings]

        # Start centred in the hoop, shrunk to fit if it is too big.
        x0, y0, x1, y1 = bounds([p for o in design.objects for pl in o.polylines for p in pl.points])
        aw, ah = self.hoop[0] - 2 * self.margin, self.hoop[1] - 2 * self.margin
        limits = [a / d for a, d in ((aw, x1 - x0), (ah, y1 - y0)) if d > 0]
        s = min([1.0] + limits)
        m = chain(translate(-(x0 + x1) / 2, -(y0 + y1) / 2), scale(s))
        self.mats = {i: m for i in self.order_ids}
        self.home = dict(self.mats)

        self.l_file.configure(text=os.path.basename(path))
        self.root.title(f"{os.path.basename(path)} – {TITLE}")
        self.fill_objects()
        self.refresh()
        if s < 1:
            self.set_status(f"The design was shrunk to {s:.0%} to fit the hoop. Use Arrange to "
                            "change its size.", WARN_COLOUR)

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
            if oid in self.texts:
                first = self.texts[oid].text.strip().split("\n")[0]
                kind = f"Text “{first[:16]}{'…' if len(first) > 16 else ''}”"
            else:
                kind = o.kind
            self.tree.insert("", "end", iid=oid, text=f" {o.index}", image=img,
                             values=(oid, kind, f"{t.number}  {t.name}" if t else to_hex(rgb),
                                     "skip" if skipped else "sew"),
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

    def delete_selected(self):
        sel = self._need_selection()
        if not sel:
            return
        texts = [i for i in sel if i in self.texts]
        if not texts:
            self.set_status("Only added text can be deleted. Use Sew / skip to leave SVG "
                            "objects out.", WARN_COLOUR)
            return
        self.push_undo()
        for i in texts:
            for d in (self.texts, self.text_objs, self.mats, self.home, self.colours):
                d.pop(i, None)
            self.skipped.discard(i)
            self.order_ids.remove(i)
        self.fill_objects()
        self.refresh()

    def reset_all(self):
        if not self.order_ids:
            return
        self.push_undo()
        self.colours.clear()
        self.skipped.clear()
        self.mats = dict(self.home)
        self.fill_objects()
        self.refresh()

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
                    self.tree.selection_set(hit)
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
                chosen = list(dict.fromkeys(d["base"] + caught))
                self.tree.selection_set(chosen) if chosen else \
                    self.tree.selection_remove(self.tree.selection())
                self.set_status(f"Selected {len(chosen)} object(s)." if chosen else
                                "Nothing was entirely inside the box.",
                                "#333333" if chosen else WARN_COLOUR)
            elif d["hit"] is not None:
                if d["additive"]:
                    self.tree.selection_toggle(d["hit"])
                else:
                    self.tree.selection_set(d["hit"])
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
                                     skipped=oid in self.skipped))
        design = Design(objs, list(self.warnings))
        try:
            self._read_hoop()
            stitch = float(self.v_stitch.get())
            plan = plan_design(design, hoop_mm=self.hoop, margin_mm=self.margin,
                               settings=Settings(stitch_len=stitch, keep_order=self.v_keep.get()),
                               placed=True)
        except ValueError:
            plan, err = None, "Hoop, margin and stitch length must be numbers."
        except ConversionError as e:
            plan, err = None, str(e)
        self.plan = plan
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
            text=f"{rep.width_mm:.1f} × {rep.height_mm:.1f} mm   ·   {rep.stitch_count} stitches"
                 f"   ·   {rep.color_blocks} colour(s)   ·   {rep.jump_count} jumps")
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
        self.order.delete(*self.order.get_children())
        self._order_images.clear()
        if self.plan is None:
            return
        size = int(16 * self.ui)
        for i, tu in enumerate(self.plan.report.thread_map, 1):
            img = swatch(self.root, [to_hex(tu.rgb)], size)
            self._order_images.append(img)
            name = f"{tu.thread.number}  {tu.thread.name}" if tu.thread else to_hex(tu.rgb)
            self.order.insert("", "end", text=f" {i}", image=img,
                              values=(name, ", ".join(tu.object_ids)))

    # ---------------------------------------------------------------- preview drawing
    def _schedule_draw(self):
        if self._draw_job is not None:
            self.root.after_cancel(self._draw_job)
        self._draw_job = self.root.after(80, self.draw_preview)

    def draw_preview(self):
        self._draw_job = None
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
        self.px = max(0.5, min((cw - 24) / hw, (ch - 24) / hh))
        self.origin = (cw / 2, ch / 2)
        pattern = self.plan.pattern if self.plan else EmbPattern()
        # draw at 2x and shrink, which smooths the lines
        big = render_image(pattern, self.hoop, self.px * 2, self.margin, self.v_jumps.get())
        img = big.resize((max(1, round(hw * self.px)), max(1, round(hh * self.px))), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(img, master=self.root)
        cv.create_image(cw / 2, ch / 2, image=self._photo)
        for oid in self.outside:                       # mark what sticks out in red
            for pts in self.placed.get(oid, []):
                cv.create_line(*[c for x, y in pts for c in self.to_canvas(x, y)],
                               fill=ERROR_COLOUR, width=1, dash=(3, 2))
        self.draw_overlay()

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
