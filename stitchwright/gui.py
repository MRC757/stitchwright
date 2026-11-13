"""The Stitchwright window: open an SVG, arrange objects, add text, choose
Brother threads, preview the stitches and save PES.

Run with:  python -m stitchwright gui [design.svg]   (or double-click stitchwright.pyw)

Objects are placed in hoop coordinates: millimetres, hoop centre at (0, 0),
y pointing down. Each object keeps its original outline plus a transform
(self.doc.mats) that places it in the hoop, so moving, resizing and rotating never
degrade the artwork and can be undone.
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import sys
import time
import tkinter as tk
from dataclasses import replace
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import __version__
from .convert import ConversionError, Plan, format_minutes, write_pes_file
from .dialogs import (ERROR_COLOUR, OK_COLOUR, SELECT_COLOUR, WARN_COLOUR, FillDialog,
                      ImageDialog, TextDialog, ThreadPicker, swatch, ui_font)
from .document import DocError, Document, PlanResult
from .geometry import (Affine, apply, bounds, point_in_polygon, polygon_area, rotate, scale,
                       segment_distance, translate)
from .image_import import trace_image
from .log import RepeatGuard, default_log_dir, log, setup_logging
from .palette import thread_for_rgb, to_hex
from .pes_import import READ_EXTENSIONS, load_stitches
from .preview import CHANGE, OUTSIDE, Style, Track, View, draw_track, render_view
from .project import EXTENSION as PROJECT_EXTENSION
from .project import PROJECT_EXTENSIONS
from .project import load_project, save_project
from .stitching import Settings
from .svg_import import DesignObject, load_svg

TITLE = "Stitchwright"
PROJECTS_LABEL = "Stitchwright projects"
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".gif")
HOOP_PRESETS = {                       # name -> (width, height) in mm
    "4 × 4 in (100 × 100 mm): PE540D": (100, 100),
    "5 × 7 in (130 × 180 mm)": (130, 180),
    "6 × 10 in (160 × 260 mm)": (160, 260),
    "8 × 8 in (200 × 200 mm)": (200, 200),
    "8 × 12 in (200 × 300 mm)": (200, 300),
}
SHIFT, CONTROL = 0x0001, 0x0004
SIM_SPEEDS = {"Slow": 15, "Normal": 60, "Fast": 250, "Very fast": 1200}   # stitches per second
SIM_FRAME_MS = 33
NEEDLE_COLOUR = "#d32f2f"
MAX_ZOOM = 60.0
TEXT_WIDGETS = ("Entry", "TEntry", "Text", "TCombobox", "TSpinbox", "Spinbox")


# ------------------------------------------------------------------ main window
class App:
    def __init__(self, root: tk.Tk, path: str | None = None):
        self.root = root
        self.ui = max(1.0, root.winfo_fpixels("1i") / 96)
        self.doc = Document()                  # the design and its undo history
        self.log_path: str | None = None
        self._error_guard = RepeatGuard()
        self.placed: dict[str, list[list[tuple[float, float]]]] = {}
        self.plan: Plan | None = None
        self.plan_error = ""                  # why there is no plan, for Save to explain
        self._title_name: str | None = None   # file name shown in the title bar
        self.outside: list[str] = []
        self.outside_stitches_only: list[str] = []
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
        self.v_units = tk.StringVar(value="mm")
        self.v_preset = tk.StringVar(value=next(iter(HOOP_PRESETS)))
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
            v.trace_add("write", lambda *a: self._setting_changed())
        self.v_pes.trace_add("write", lambda *a: self._setting_changed(replan=False))
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
        # Fit the usable screen (without the taskbar), counting the title bar and menu bar;
        # maximise if the full size won't fit.
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        top, avail_h = 0, sh - 100
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
                rect = wintypes.RECT()
                ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)  # work area
                top, avail_h = rect.top, rect.bottom - rect.top
            except (AttributeError, OSError):
                pass
        frame = int(70 * ui)                          # title bar + menu bar
        want_w, want_h = int(1200 * ui), int(820 * ui)
        w, h = min(want_w, sw - 40), min(want_h, avail_h - frame)
        r.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{top + max(0, (avail_h - frame - h) // 2)}")
        r.minsize(min(int(900 * ui), sw - 40), min(int(560 * ui), avail_h - frame))
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
        self.l_target = ttk.Label(f_arr, text="Everything", font=ui_font(f_arr, bold=True))
        self.l_target.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        self.l_size = ttk.Label(f_arr, text="Size (mm)")
        self.l_size.grid(row=1, column=0, sticky="w")
        sf = ttk.Frame(f_arr)
        sf.grid(row=1, column=1, columnspan=3, sticky="w", pady=2)
        self.e_w = ttk.Entry(sf, textvariable=self.v_w, **num)
        self.e_w.pack(side="left")
        ttk.Label(sf, text=" × ").pack(side="left")
        self.e_h = ttk.Entry(sf, textvariable=self.v_h, **num)
        self.e_h.pack(side="left")
        ttk.Checkbutton(sf, text="Keep proportions", variable=self.v_ratio).pack(side="left", padx=8)
        self.l_pos = ttk.Label(f_arr, text="Position (mm)")
        self.l_pos.grid(row=2, column=0, sticky="w")
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
        cb = ttk.Combobox(f_set, textvariable=self.v_preset, state="readonly", width=26,
                          values=list(HOOP_PRESETS) + ["Custom"])
        cb.grid(row=0, column=2, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._pick_hoop())
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

        r.protocol("WM_DELETE_WINDOW", self.exit_app)
        # Ctrl+letter: (command, command with Shift held, works while typing in a text box).
        # Both cases of each letter are bound and Shift is read from the event, because
        # with Caps Lock on Tk reports the capital letter for an unshifted key.
        self._shortcuts = {
            "o": (self.ask_open, None, True),
            "n": (self.new_design, None, True),
            "i": (self.ask_import, None, True),
            "s": (self.save, self.save_project, True),
            "t": (self.add_text, None, True),
            "z": (self.undo, self.redo, False),
            "y": (self.redo, None, False),
            "a": (self.select_all, None, False),
            "c": (self.copy_selection, None, False),
            "v": (self.paste, None, False),
            "d": (self.duplicate, None, False),
            "g": (self.group_selection, self.ungroup_selection, False),
        }
        for letter in self._shortcuts:
            for key in (letter, letter.upper()):
                r.bind_all(f"<Control-{key}>", lambda e, k=letter: self._shortcut(e, k))
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
        self.m_file.add_command(label="Import picture…", command=self.ask_import_image)
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
        self.m_view.add_radiobutton(label="Millimetres", variable=self.v_units, value="mm",
                                    command=self.set_units)
        self.m_view.add_radiobutton(label="Inches", variable=self.v_units, value="in",
                                    command=self.set_units)
        self.m_view.add_separator()
        self.m_view.add_command(label="Sew-out: play / pause", command=self.sim_toggle)
        bar.add_cascade(label="View", menu=self.m_view)
        m = tk.Menu(bar, tearoff=False)
        m.add_command(label="Open log folder", command=self.open_log_folder)
        bar.add_cascade(label="Help", menu=m)
        self.root.configure(menu=bar)
        self.menubar = bar

    # ---------------------------------------------------------------- errors and the log
    def on_exception(self, exc_type, exc, tb):
        """Tk calls this for any error in a button or key handler. Log it and tell the
        user, instead of failing silently (the .exe has no console)."""
        log.error("Unexpected error", exc_info=(exc_type, exc, tb))
        if not self._error_guard.should_show(time.monotonic()):
            return
        where = self.log_path or "the log file"
        messagebox.showerror(
            TITLE, f"Something went wrong: {exc_type.__name__}: {exc}\n\n"
                   f"The design may have been partly changed; if something looks wrong, "
                   f"Undo (Ctrl+Z) puts it back.\n\n"
                   f"Details were saved to:\n{where}", parent=self.root)

    def open_log_folder(self):
        folder = os.path.dirname(self.log_path) if self.log_path else default_log_dir()
        os.makedirs(folder, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])
        except OSError as e:
            messagebox.showinfo(TITLE, f"The log folder is:\n{folder}\n\n({e})", parent=self.root)

    def _pick_hoop(self):
        size = HOOP_PRESETS.get(self.v_preset.get())
        if size:
            self.v_hoop_w.set(f"{size[0]:g}")
            self.v_hoop_h.set(f"{size[1]:g}")
            self.zoom_fit()
            if size != (100, 100):
                self.set_status("The PE540D sews only in its 4 × 4 in (100 × 100 mm) hoop; larger "
                                "hoops are for other machines.", WARN_COLOUR)

    def _shortcut(self, event, letter: str):
        """Ctrl+letter, with or without Shift (Caps Lock makes no difference)."""
        plain, shifted, text_ok = self._shortcuts[letter]
        fn = shifted if shifted is not None and event.state & SHIFT else plain
        return self._main_key(event, fn, text_ok)

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
        return self.doc.obj(obj_id)

    def chosen_rgb(self, o: DesignObject):
        return self.doc.chosen_rgb(o)

    def selection(self) -> list[str]:
        return [i for i in self.tree.selection() if i in self.doc.mats]

    def target(self) -> list[str]:
        """What the Arrange controls act on: the selection, or everything."""
        return self.selection() or list(self.doc.order_ids)

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
        if len(sel) == 1 and sel[0] in self.doc.texts:
            self.edit_text(sel[0])
        elif sel:
            self.change_thread()

    # ---------------------------------------------------------------- undo

    def undo(self):
        if self.doc.undo():
            self.sync()

    def redo(self):
        if self.doc.redo():
            self.sync()

    def _undo_buttons(self):
        self.b_undo.configure(state="normal" if self.doc.undo_stack else "disabled")
        self.b_redo.configure(state="normal" if self.doc.redo_stack else "disabled")

    def sync(self, select=None):
        """After the document changes: refill the list, set the selection (if given),
        re-plan, redraw and update Undo / Redo."""
        self.fill_objects()
        if select is not None:
            if select:
                self.tree.selection_set(select)
            else:
                self.tree.selection_remove(self.tree.selection())
        self.refresh()
        self._undo_buttons()
        self._update_title()

    def _doc_error(self, e: DocError):
        self.set_status(str(e), WARN_COLOUR)

    # ---------------------------------------------------------------- files
    OPEN_TYPES = [("Designs", "*.svg *.stitchwright *.svg2pes *.pes *.dst *.jef *.exp *.vp3 "
                               "*.png *.jpg *.jpeg *.bmp *.gif"),
                  ("SVG artwork", "*.svg"), (PROJECTS_LABEL, "*.stitchwright *.svg2pes"),
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
        if ext in PROJECT_EXTENSIONS:              # .stitchwright, or .svg2pes from before
            self.open_project(path)
            return
        if ext in IMAGE_EXTENSIONS:
            self.import_image(path, replace_design=True)
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
        self._fix_dpi(design)
        self._start_design(design, path)

    def _fix_dpi(self, design) -> None:
        """Offer to scale an Illustrator (72 dpi) export up to its intended size."""
        if not getattr(design, "dpi_hint", None):
            return
        design.warnings = [w for w in design.warnings if "72 dpi" not in w]
        if messagebox.askyesno(TITLE, "This SVG looks like an Adobe Illustrator export. "
                               "Illustrator measures at 72 dpi, so the design would come out "
                               "25% smaller than drawn.\n\nScale it up to 133% (its intended "
                               "size)?", parent=self.root):
            Document.scale_objects(design, 96.0 / design.dpi_hint)

    def _start_design(self, design, path: str):
        try:
            self._read_hoop()
        except ValueError:
            pass            # keep the last good hoop size
        s = self.doc.load_design(design, path)
        self._set_title(os.path.basename(path))
        log.info("Opened %s (%d objects)", path, len(self.doc.order_ids))
        self.sync()
        if s < 1:
            self.set_status(f"The design was shrunk to {s:.0%} to fit the hoop. Use Arrange to "
                            "change its size.", WARN_COLOUR)

    def _set_title(self, name: str):
        self._title_name = name
        self.l_file.configure(text=name)
        self._update_title()

    def _update_title(self):
        """The title bar: the file name, marked with • while there are unsaved changes."""
        if self._title_name is None:
            self.root.title(TITLE)
            return
        mark = "• " if self.doc.dirty and self.doc.order_ids else ""
        self.root.title(f"{mark}{self._title_name} – {TITLE}")

    def new_design(self, confirm: bool = True):
        if confirm and not self._confirm_discard():
            return
        self.doc.clear()
        self._set_title("New design")
        self.sync()
        self.set_status("New design: open or import artwork, or add text.")

    def ask_import(self):
        path = filedialog.askopenfilename(parent=self.root, title="Import into this design",
                                          filetypes=[t for t in self.OPEN_TYPES
                                                     if t[0] != PROJECTS_LABEL])
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
        self._fix_dpi(design)
        self.add_objects(design.objects, keep_place=any(o.stitches for o in design.objects),
                         texts=design.texts)
        self.doc.note_source(path)

    def add_objects(self, objects, keep_place: bool = False, fills=None, texts=None):
        """Add objects to the design and select them."""
        new_ids = self.doc.add_objects(objects, keep_place, fills, texts)
        self.sync(select=new_ids)
        log.info("Added %d object(s)", len(new_ids))
        self.set_status(f"Added {len(new_ids)} object(s). They are selected: drag them into place.")
        return new_ids

    # ---------------------------------------------------------------- projects
    def _settings_dict(self) -> dict:
        """The Stitching-tab settings, as saved in a project."""
        return {"hoop": [self.v_hoop_w.get(), self.v_hoop_h.get()],
                "margin": self.v_margin.get(), "stitch_len": self.v_stitch.get(),
                "keep_order": self.v_keep.get(), "knockout": self.v_knockout.get(),
                "machine_spm": self.v_spm.get(), "pes_version": self.v_pes.get()}

    def save_project(self, save_as: bool = False) -> bool:
        doc = self.doc
        if not doc.order_ids:
            self.set_status("Nothing to save yet.", WARN_COLOUR)
            return False
        path = doc.project_path
        if save_as or not path:
            base = os.path.splitext(os.path.basename(doc.svg_path or "design"))[0]
            folder = os.path.dirname(doc.svg_path) if doc.svg_path else os.path.expanduser("~")
            path = filedialog.asksaveasfilename(
                parent=self.root, title="Save project", defaultextension=PROJECT_EXTENSION,
                initialfile=base + PROJECT_EXTENSION, initialdir=folder,
                filetypes=[("Stitchwright project", "*" + PROJECT_EXTENSION)])
            if not path:
                return False
        try:
            save_project(path, doc.to_project(self._settings_dict()))
        except OSError as e:
            messagebox.showerror(TITLE, f"Could not save {path}:\n\n{e}", parent=self.root)
            return False
        doc.mark_saved(path)
        self._set_title(os.path.basename(path))
        log.info("Saved project %s (%d objects)", path, len(doc.order_ids))
        self.set_status(f"Saved project {path}. Open it again with File → Open.", OK_COLOUR)
        return True

    def open_project(self, path: str):
        try:
            proj = load_project(path)
        except (OSError, ValueError, TypeError, KeyError) as e:
            messagebox.showerror(TITLE, f"Could not open {path}:\n\n{e}", parent=self.root)
            return
        st = self.doc.load_project(proj, path)
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
        self.v_preset.set(self._preset_for_hoop())
        self._set_title(os.path.basename(path))
        log.info("Opened project %s (%d objects)", path, len(self.doc.order_ids))
        self.sync()
        self.doc.mark_saved()           # setting the variables above isn't a change
        self._update_title()

    def _preset_for_hoop(self) -> str:
        """The hoop preset matching the hoop size boxes, or "Custom"."""
        try:
            size = (float(self.v_hoop_w.get()), float(self.v_hoop_h.get()))
        except ValueError:
            return "Custom"
        for name, preset in HOOP_PRESETS.items():
            if size == preset:
                return name
        return "Custom"

    def _confirm_discard(self) -> bool:
        """True if it's fine to throw away the current design (asks to save changes)."""
        if not self.doc.dirty or not self.doc.order_ids:
            return True
        ans = messagebox.askyesnocancel(TITLE, "Save the changes to this design as a project "
                                        "first?", parent=self.root)
        if ans is None:
            return False
        return self.save_project() if ans else True

    def ask_import_image(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Import a picture",
            filetypes=[("Pictures", "*.png *.jpg *.jpeg *.bmp *.gif"), ("All files", "*.*")])
        if path:
            self.import_image(path)

    def _trace_picture(self, path: str):
        """Ask how to trace a picture, then trace it. Returns (objects, fill), or None if
        the user cancelled or nothing came of it (the reason has been shown)."""
        width = min(80.0, self.doc.hoop[0] - 2 * self.doc.margin)
        try:
            res = ImageDialog(self.root, self.ui, path, width).wait()
        except OSError as e:
            messagebox.showerror(TITLE, f"Could not open {path}:\n\n{e}", parent=self.root)
            return None
        if res is None:
            return None
        settings, fill = res
        try:
            objects, warnings = trace_image(path, settings)
        except (OSError, ValueError) as e:
            messagebox.showerror(TITLE, f"Could not trace {path}:\n\n{e}", parent=self.root)
            return None
        if not objects:
            self.set_status("; ".join(warnings) or "Nothing to stitch in that picture.", WARN_COLOUR)
            return None
        return objects, fill

    def import_image(self, path: str, replace_design: bool = False):
        """Trace a picture into filled shapes and add them to the design, or (with
        replace_design) start a new design from them. The design is only replaced once
        the picture has been traced, so cancelling or a bad file loses nothing."""
        traced = self._trace_picture(path)
        if traced is None:
            return
        if replace_design:
            self.new_design(confirm=False)
        objects, fill = traced
        new_ids = self.doc.add_traced(objects, fill)
        self.sync(select=new_ids)
        if self.doc.svg_path is None:
            self.doc.note_source(path)
            self._set_title(os.path.basename(path))
        self.set_status(f"Picture traced into {len(new_ids)} colour(s). Check the shapes, "
                        "then adjust threads or fills as needed.", OK_COLOUR)

    def exit_app(self):
        if self._confirm_discard():
            self.root.destroy()

    def save(self):
        if self._job is not None:            # settings changed moments ago: re-plan first
            self.root.after_cancel(self._job)
            self.refresh()
        if self.plan is None:
            if not self.doc.order_ids:
                self.set_status("Nothing to save yet: open a design or add text first.",
                                WARN_COLOUR)
            else:
                why = self.plan_error or "the design has no stitches."
                self.set_status(f"Can't save the PES yet: {why}", ERROR_COLOUR)
            return
        if self.outside:
            messagebox.showwarning(TITLE, "Part of the design is outside the sewing area. "
                                   "Move or shrink it first (Arrange → Fit to hoop).",
                                   parent=self.root)
            return
        if self.doc.svg_path:
            stem, folder = os.path.splitext(os.path.basename(self.doc.svg_path))[0], os.path.dirname(self.doc.svg_path)
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
        rep = self.plan.report
        log.info("Wrote PES %s (%d stitches, %d colours)", path, rep.stitch_count, rep.color_blocks)
        self.set_status(f"Saved {path}.{tip}", OK_COLOUR)

    # ---------------------------------------------------------------- objects list
    def fill_objects(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self._images.clear()
        size = int(16 * self.ui)
        for oid in self.doc.order_ids:
            o = self.obj(oid)
            rgb = self.chosen_rgb(o)
            t = thread_for_rgb(rgb)
            img = swatch(self.root, [to_hex(o.source_color), to_hex(rgb)], size)
            self._images.append(img)
            skipped = oid in self.doc.skipped
            outline = "(no outline)" if oid in self.doc.no_outline else \
                (f"{t.number}  {t.name}" if t else to_hex(rgb))
            if oid in self.doc.satins and oid not in self.doc.no_outline:
                outline += f"  (satin {self.doc.satins[oid].width:g})"
            fill = "—"
            if oid in self.doc.fills:
                frgb = self.doc.fills[oid].color or rgb
                ft = thread_for_rgb(frgb)
                fill = f"{ft.number}  {ft.name}" if ft else to_hex(frgb)
            if oid in self.doc.texts:
                first = self.doc.texts[oid].text.strip().split("\n")[0]
                kind = f"Text “{first[:16]}{'…' if len(first) > 16 else ''}”"
            else:
                kind = o.kind
            if oid in self.doc.groups:
                kind += f"  [{self.doc.groups[oid]}]"
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
        if self.doc.order_ids:
            self.tree.selection_set(self.doc.order_ids)
            self.set_status(f"Selected all {len(self.doc.order_ids)} objects.")

    def select_none(self):
        if self.drag and self.drag["mode"] == "box":     # Esc during a box drag cancels it
            self.drag = None
            self.canvas.delete("box")
        self.tree.selection_remove(self.tree.selection())

    def change_thread(self):
        sel = self._need_selection()
        if not sel:
            return
        first = self.obj(sel[0])
        others = self.doc.same_source_colour(first.obj_id) if len(sel) == 1 else []
        title = (f"Thread for {first.obj_id}" if len(sel) == 1
                 else f"Thread for {len(sel)} objects")
        picker = ThreadPicker(self.root, self.ui, title, thread_for_rgb(self.chosen_rgb(first)),
                              to_hex(first.source_color), len(others))
        result = picker.wait()
        if result is None:
            return
        ids = sel + (others if picker.apply_all.get() else [])
        self.doc.set_thread(ids, None if result == "reset" else result.rgb)
        self.sync()

    def toggle_skip(self):
        sel = self._need_selection()
        if sel:
            self.doc.toggle_skip(sel)
            self.sync()

    def delete_selected(self):
        sel = self._need_selection()
        if not sel:
            return
        self.doc.delete(sel)
        self.sync()
        self.set_status(f"Deleted {len(sel)} object(s). Undo (Ctrl+Z) brings them back.")

    # ---------------------------------------------------------------- copy / paste

    def copy_selection(self):
        sel = self._need_selection()
        if sel:
            self.doc.copy(sel)
            self.set_status(f"Copied {len(sel)} object(s). Paste with Ctrl+V.")

    def paste(self):
        try:
            new_ids = self.doc.paste()
        except DocError as e:
            return self._doc_error(e)
        self.sync(select=new_ids)
        self.set_status(f"Pasted {len(new_ids)} object(s), 5 mm down and right. Drag them into place.")

    def duplicate(self):
        sel = self._need_selection()
        if sel:
            new_ids = self.doc.duplicate(sel)
            self.sync(select=new_ids)
            self.set_status(f"Duplicated {len(new_ids)} object(s), 5 mm down and right.")

    # ---------------------------------------------------------------- groups

    def expand_groups(self, ids) -> list[str]:
        return self.doc.expand_groups(ids)

    def group_selection(self):
        sel = self._need_selection()
        if not sel:
            return
        try:
            gid = self.doc.group(sel)
        except DocError as e:
            return self._doc_error(e)
        self.sync()
        self.set_status(f"Grouped {len(sel)} objects as {gid}: clicking one selects them all.")

    def ungroup_selection(self):
        n = self.doc.ungroup(self._need_selection())
        if n:
            self.sync()
            self.set_status(f"Ungrouped {n} object(s).")

    # ---------------------------------------------------------------- flip / align
    def flip(self, horizontal: bool):
        self._arrange(self.doc.flip, self.target(), horizontal)

    def _arrange(self, op, ids, *args, **kw):
        """Run a document arranging operation, then redraw (or say why it couldn't)."""
        if not ids:
            return
        try:
            op(ids, *args, **kw)
        except DocError as e:
            return self._doc_error(e)
        self.sync()

    def align(self, how: str):
        sel = self._need_selection()
        self._arrange(self.doc.align, sel, how)

    def distribute(self, horizontal: bool):
        sel = self._need_selection()
        self._arrange(self.doc.distribute, sel, horizontal)

    def reset_all(self):
        if self.doc.order_ids:
            self.doc.reset_all()
            self.sync()

    # ---------------------------------------------------------------- fill

    def edit_fill(self):
        sel = self._need_selection()
        if not sel:
            return
        try:
            ids, fillable = self.doc.stitch_targets(sel)
        except DocError as e:
            return self._doc_error(e)
        first = (fillable or ids)[0]
        title = f"Fill and outline: {first}" if len(ids) == 1 else \
            f"Fill and outline: {len(ids)} objects"
        res = FillDialog(self.root, self.ui, title, self.doc.fills.get(first),
                         first not in self.doc.no_outline, self.chosen_rgb(self.obj(first)),
                         self.doc.satins.get(first), can_fill_shape=bool(fillable)).wait()
        if res is None:
            return
        left_out = self.doc.set_stitches(ids, res["fill"], res["outline"], res["satin"])
        self.sync()
        if left_out:
            self.set_status(f"Not closed shapes, so not filled: {', '.join(left_out)}", WARN_COLOUR)

    # ---------------------------------------------------------------- text
    def add_text(self):
        last = self.doc.last_text
        spec = TextDialog(self.root, self.ui, last and replace(last, text=""), editing=False).wait()
        if spec is None:
            return
        try:
            oid, missing = self.doc.add_text(spec)
        except DocError as e:
            return self.set_status(str(e), ERROR_COLOUR)
        self.sync(select=[oid])
        self.tree.see(oid)
        if missing:
            self.set_status(f"Not in this font, left out: {missing}", WARN_COLOUR)
        elif not self.outside:
            self.set_status("Text added in the middle of the hoop: drag it into place. "
                            "Double-click it to edit.", OK_COLOUR)

    def edit_text(self, oid: str):
        old = self.doc.texts[oid]
        spec = TextDialog(self.root, self.ui, old, editing=True).wait()
        if spec is None or spec == old:
            return
        try:
            missing = self.doc.edit_text(oid, spec)
        except DocError as e:
            return self.set_status(str(e), ERROR_COLOUR)
        self.sync()
        if missing:
            self.set_status(f"Not in this font, left out: {missing}", WARN_COLOUR)

    # ---------------------------------------------------------------- arranging
    def apply_transform(self, m: Affine, ids=None):
        """Apply a placement change from a drag or a nudge (hoop coordinates)."""
        ids = list(ids if ids is not None else self.target())
        if ids:
            self.doc.transform(ids, m)
            self.sync()
    def _fmt(self, v: float) -> str:
        return f"{v / 25.4:.2f}" if self.v_units.get() == "in" else f"{v:.1f}"

    def _len_text(self, v: float) -> str:
        return f"{v / 25.4:.2f} in" if self.v_units.get() == "in" else f"{v:.1f} mm"

    def _len(self, var: tk.StringVar, label: str):
        v = self._num(var, label)
        return None if v is None else (v * 25.4 if self.v_units.get() == "in" else v)

    def set_units(self):
        u = self.v_units.get()
        self.l_size.configure(text=f"Size ({u})")
        self.l_pos.configure(text=f"Position ({u})")
        self.update_arrange_fields()
        self.refresh()

    def update_arrange_fields(self):
        sel = self.selection()
        if not self.doc.order_ids:
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
        if not self.target() or (self.v_w.get() == self._shown.get("w")
                                 and self.v_h.get() == self._shown.get("h")):
            return
        w, h = self._len(self.v_w, "Width"), self._len(self.v_h, "Height")
        if w is None or h is None:
            return
        try:
            self.doc.resize(self.target(), w, h, self.v_ratio.get(),
                            width_changed=self.v_w.get() != self._shown.get("w"))
        except DocError as e:
            self.set_status(str(e), ERROR_COLOUR)
            self.update_arrange_fields()
            return
        self.sync()

    def apply_position(self):
        if not self.target() or (self.v_x.get() == self._shown.get("x")
                                 and self.v_y.get() == self._shown.get("y")):
            return
        x, y = self._len(self.v_x, "X"), self._len(self.v_y, "Y")
        if x is not None and y is not None:
            self._arrange(self.doc.move_to, self.target(), x, y)

    def rotate_by(self, degrees: float):
        self._arrange(self.doc.rotate, self.target(), degrees)

    def rotate_by_entry(self):
        deg = self._num(self.v_rot, "Angle")
        if deg is not None:
            self.rotate_by(deg)

    def centre_target(self):
        self._arrange(self.doc.centre, self.target())

    def fit_target(self):
        # leave room for stitches that reach past the outlines (satin, fill pull)
        reach = self.plan.report.object_bounds if self.plan is not None else None
        self._arrange(self.doc.fit, self.target(), reach)

    def reset_target(self):
        self._arrange(self.doc.reset, self.target())

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
        for oid in reversed(self.doc.order_ids):
            if oid in self.doc.skipped:
                continue
            for pts in self.placed.get(oid, []):
                d = min((segment_distance(p, a, b) for a, b in zip(pts, pts[1:])), default=1e9)
                if d <= tol:
                    score = d
                elif len(pts) > 3 and math.dist(pts[0], pts[-1]) < 1e-6 and point_in_polygon(p, pts):
                    score = tol + polygon_area(pts)          # prefer the smallest shape clicked inside
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
        for oid in self.doc.order_ids:
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
        if not cursor and self.doc.order_ids and self.hit_test(e.x, e.y)[1]:
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
        if not self.doc.order_ids:
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
            msg = f"Move {self._len_text(mx)} across, {self._len_text(my)} down"
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
    def _setting_changed(self, replan: bool = True):
        """A Stitching-tab setting changed: it's saved in the project, so the design now
        has unsaved changes (opening a project sets these too, then clears the flag)."""
        self.doc.mark_changed()
        self._update_title()
        if replan:
            self.schedule()

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
            self.doc.set_hoop(hoop, margin)

    def refresh(self):
        self._job = None
        try:
            self._read_hoop()
            settings = Settings(stitch_len=float(self.v_stitch.get()), keep_order=self.v_keep.get(),
                                knockout=self.v_knockout.get(),
                                machine_spm=max(100, int(float(self.v_spm.get()))))
            res = self.doc.plan(settings)
        except ValueError:
            res = PlanResult(None, "Hoop, margin, stitch length and machine speed must be numbers.",
                             self.doc.build_design()[1])
        self.placed = res.placed
        self.plan = res.plan
        self.plan_error = res.error
        self.outside, self.outside_stitches_only = res.outside, res.outside_stitches_only
        self.track = Track(res.plan.pattern) if res.plan else Track()
        self.sim_reset()
        self.fill_order()
        self.update_arrange_fields()
        self.draw_preview()
        if not self.doc.order_ids:
            self.b_save.configure(state="disabled")
            self.l_info.configure(text="")
            return
        if res.plan is None:
            self.b_save.configure(state="disabled")
            self.l_info.configure(text="")
            self.set_status(res.error, ERROR_COLOUR)
            return
        rep = res.plan.report
        self.l_info.configure(
            text=f"{self._len_text(rep.width_mm)[:-3]} × {self._len_text(rep.height_mm)}"
                 f"  ·  {rep.stitch_count} stitches"
                 f"  ·  {rep.color_blocks} colours  ·  about {format_minutes(rep.total_minutes)}")
        if self.outside:
            self.b_save.configure(state="disabled")
            names = ", ".join(self.outside)
            if self.outside_stitches_only == self.outside:
                msg = (f"The stitches of {names} reach outside the sewing area (a satin "
                       f"column or fill edge goes a little past the outline). Move it in, "
                       f"or use Arrange → Fit to hoop.")
            else:
                msg = f"Outside the sewing area: {names}. Drag it in, or use Arrange → Fit to hoop."
            self.set_status(msg, ERROR_COLOUR)
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
        try:
            moved = self.doc.move_stop(self.plan.report.thread_map, int(sel[0]), step)
        except DocError as e:
            return self._doc_error(e)
        if moved is None:
            return
        self.sync()
        for n, tu in enumerate(self.plan.report.thread_map if self.plan else []):
            if moved in Document.stop_keys(tu):          # keep the moved stop selected
                self.order.selection_set(str(n))
                break

    def auto_order(self):
        if self.doc.color_order:
            self.doc.auto_order()
            self.sync()
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
        if not self.doc.order_ids:
            cv.create_text(cw / 2, ch / 2, text="Open an SVG or add text to start.",
                           fill="#777777", font=ui_font(cv, size=11))
            return
        hw, hh = self.doc.hoop
        self.px = max(0.2, min((cw - 24) / hw, (ch - 24) / hh)) * self.zoom
        vx, vy = self.view_c
        self.origin = (cw / 2 - vx * self.px, ch / 2 - vy * self.px)
        self.l_zoom.configure(text=f"{self.zoom:.0%}")
        ss = 1 if (fast or self.sim_playing) else 2      # 2x then shrink smooths the lines
        img = render_view(self.track, self._view(ss), self.doc.hoop, self.doc.margin, self._style(),
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
        if abs(new - self.zoom) < 1e-9 or not self.doc.order_ids:
            return
        mx, my = self.from_canvas(cx, cy)
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.zoom = new
        px = max(0.2, min((cw - 24) / self.doc.hoop[0], (ch - 24) / self.doc.hoop[1])) * new
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
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("stitchwright")  # own taskbar icon
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
    log_path = setup_logging()
    log.info("Stitchwright %s started", __version__)
    app = App(root, argv[0] if argv else None)
    app.log_path = log_path
    root.report_callback_exception = app.on_exception
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
