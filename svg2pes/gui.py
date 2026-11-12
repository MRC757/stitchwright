"""Desktop window for svg2pes: open an SVG, choose Brother threads, preview, save PES.

Run with:  python -m svg2pes gui [design.svg]   (or double-click svg2pes_gui.pyw)
"""
from __future__ import annotations

import copy
import math
import os
import re
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from .convert import ConversionError, Plan, plan_design, write_pes_file
from .palette import Thread, brother_threads, nearest_brother, thread_for_rgb, to_hex
from .preview import BACKGROUND, render_image
from .stitching import Settings
from .svg_import import Design, DesignObject, load_svg

TITLE = "svg2pes – SVG to Brother PES"
OK_COLOUR, WARN_COLOUR, ERROR_COLOUR = "#2e7d32", "#9a6700", "#c62828"


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
                outline="#1a73e8" if selected else "", width=2, tags=(tag,))
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
        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(px, 0)}+{max(py, 0)}")

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


class App:
    def __init__(self, root: tk.Tk, path: str | None = None):
        self.root = root
        self.ui = max(1.0, root.winfo_fpixels("1i") / 96)
        self.design: Design | None = None
        self.svg_path: str | None = None
        self.colours: dict[str, tuple[int, int, int]] = {}   # obj_id -> chosen thread rgb
        self.skipped: set[str] = set()
        self.plan: Plan | None = None
        self.hoop = (100.0, 100.0)
        self.margin = 2.0
        self._images: list[tk.PhotoImage] = []   # keep swatches alive
        self._order_images: list[tk.PhotoImage] = []
        self._photo = None
        self._job = None
        self._draw_job = None

        self.v_hoop_w = tk.StringVar(value="100")
        self.v_hoop_h = tk.StringVar(value="100")
        self.v_margin = tk.StringVar(value="2")
        self.v_width = tk.StringVar(value="")
        self.v_fit = tk.BooleanVar(value=True)
        self.v_stitch = tk.StringVar(value="2.5")
        self.v_keep = tk.BooleanVar(value=False)
        self.v_pes = tk.StringVar(value="1")
        self.v_jumps = tk.BooleanVar(value=True)
        self.v_status = tk.StringVar()
        for v in (self.v_hoop_w, self.v_hoop_h, self.v_margin, self.v_width, self.v_fit,
                  self.v_stitch, self.v_keep):
            v.trace_add("write", lambda *a: self.schedule())
        self.v_jumps.trace_add("write", lambda *a: self.draw_preview())

        self._build()
        self.set_status("Open an SVG to start (Ctrl+O).")
        if path:
            self.root.after(50, lambda: self.open(path))

    # ---------------------------------------------------------------- layout
    def _build(self):
        r, ui = self.root, self.ui
        r.title(TITLE)
        r.geometry(f"{int(1180 * ui)}x{int(760 * ui)}")
        r.minsize(int(900 * ui), int(600 * ui))
        style = ttk.Style(r)
        style.configure("Treeview", rowheight=int(22 * ui))

        bar = ttk.Frame(r, padding=(8, 8, 8, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="Open SVG…", command=self.ask_open).pack(side="left")
        self.b_save = ttk.Button(bar, text="Save PES…", command=self.save, state="disabled")
        self.b_save.pack(side="left", padx=6)
        self.l_file = ttk.Label(bar, text="No file open", foreground="#555555")
        self.l_file.pack(side="left", padx=8)

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
        self.tree = ttk.Treeview(f_obj, columns=cols, show="tree headings", height=8,
                                 selectmode="extended")
        self.tree.heading("#0", text="#", anchor="w")
        self.tree.column("#0", width=int(88 * ui), stretch=False)
        for c, text, w in (("id", "Object", 120), ("kind", "Shape", 70),
                           ("thread", "Thread", 150), ("sew", "Sew", 44)):
            self.tree.heading(c, text=text, anchor="w")
            self.tree.column(c, width=int(w * ui), stretch=c == "thread")
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
        ttk.Button(btns, text="Reset all", command=self.reset_all).pack(side="left")
        ttk.Label(f_obj, foreground="#666666",
                  text="Double-click an object to choose its thread; Space to sew/skip.") \
            .grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Return>", lambda e: self.change_thread())
        self.tree.bind("<space>", lambda e: self.toggle_skip())
        self.tree.bind("<Delete>", lambda e: self.toggle_skip())

        # Settings ----------------------------------------------------------
        f_set = ttk.LabelFrame(left, text=" Size and stitching ", padding=6)
        f_set.pack(fill="x", pady=(8, 0))
        num = dict(width=7)
        ttk.Label(f_set, text="Hoop (mm)").grid(row=0, column=0, sticky="w")
        hf = ttk.Frame(f_set)
        hf.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Entry(hf, textvariable=self.v_hoop_w, **num).pack(side="left")
        ttk.Label(hf, text=" × ").pack(side="left")
        ttk.Entry(hf, textvariable=self.v_hoop_h, **num).pack(side="left")
        ttk.Label(f_set, text="PE540D: 100 × 100", foreground="#666666").grid(row=0, column=2, sticky="w", padx=6)

        ttk.Label(f_set, text="Margin (mm)").grid(row=1, column=0, sticky="w")
        ttk.Entry(f_set, textvariable=self.v_margin, **num).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(f_set, text="kept clear inside the hoop", foreground="#666666").grid(row=1, column=2, sticky="w", padx=6)

        ttk.Label(f_set, text="Width (mm)").grid(row=2, column=0, sticky="w")
        ttk.Entry(f_set, textvariable=self.v_width, **num).grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(f_set, text="blank = size in the SVG", foreground="#666666").grid(row=2, column=2, sticky="w", padx=6)

        ttk.Checkbutton(f_set, text="Shrink to fit the hoop if too big",
                        variable=self.v_fit).grid(row=3, column=0, columnspan=3, sticky="w", pady=2)

        ttk.Label(f_set, text="Stitch length (mm)").grid(row=4, column=0, sticky="w")
        ttk.Spinbox(f_set, textvariable=self.v_stitch, from_=1.0, to=7.0, increment=0.1,
                    **num).grid(row=4, column=1, sticky="w", pady=2)
        ttk.Label(f_set, text="2–3 mm suits most outlines", foreground="#666666").grid(row=4, column=2, sticky="w", padx=6)

        ttk.Checkbutton(f_set, text="Keep SVG drawing order (more colour changes)",
                        variable=self.v_keep).grid(row=5, column=0, columnspan=3, sticky="w", pady=2)

        ttk.Label(f_set, text="PES version").grid(row=6, column=0, sticky="w")
        ttk.Combobox(f_set, textvariable=self.v_pes, values=("1", "6"), state="readonly",
                     width=5).grid(row=6, column=1, sticky="w", pady=2)
        ttk.Label(f_set, text="1 is tested on the PE540D", foreground="#666666").grid(row=6, column=2, sticky="w", padx=6)

        # Sewing order ------------------------------------------------------
        f_ord = ttk.LabelFrame(left, text=" Sewing order (thread changes) ", padding=6)
        f_ord.pack(fill="both", expand=True, pady=(8, 0))
        self.order = ttk.Treeview(f_ord, columns=("thread", "objects"), show="tree headings",
                                  height=5, selectmode="none")
        self.order.heading("#0", text="Stop", anchor="w")
        self.order.column("#0", width=int(70 * ui), stretch=False)
        self.order.heading("thread", text="Thread", anchor="w")
        self.order.column("thread", width=int(150 * ui), stretch=False)
        self.order.heading("objects", text="Objects", anchor="w")
        self.order.column("objects", width=int(200 * ui))
        self.order.pack(fill="both", expand=True)

        # Preview -----------------------------------------------------------
        self.canvas = tk.Canvas(right, bg=to_hex(BACKGROUND), highlightthickness=1,
                                highlightbackground="#cccccc")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._schedule_draw())
        under = ttk.Frame(right)
        under.pack(fill="x", pady=(6, 0))
        self.l_info = ttk.Label(under, text="")
        self.l_info.pack(side="left")
        ttk.Checkbutton(under, text="Show jumps", variable=self.v_jumps).pack(side="right")

        r.bind_all("<Control-o>", lambda e: self.ask_open())
        r.bind_all("<Control-s>", lambda e: self.save())

    # ---------------------------------------------------------------- helpers
    def set_status(self, text: str, colour: str = "#333333"):
        self.v_status.set(text)
        self.l_status.configure(foreground=colour)

    def _obj(self, obj_id: str) -> DesignObject:
        return next(o for o in self.design.objects if o.obj_id == obj_id)

    def chosen_rgb(self, o: DesignObject):
        return self.colours.get(o.obj_id) or nearest_brother(o.source_color).rgb

    def _on_double(self, event):
        if self.tree.identify_row(event.y):
            self.change_thread()

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
        self.design, self.svg_path = design, path
        self.colours.clear()
        self.skipped.clear()
        self.l_file.configure(text=os.path.basename(path))
        self.root.title(f"{os.path.basename(path)} – {TITLE}")
        self.fill_objects()
        self.refresh()

    def save(self):
        if self._job is not None:            # settings changed moments ago: re-plan first
            self.root.after_cancel(self._job)
            self.refresh()
        if self.plan is None:
            return
        stem = os.path.splitext(os.path.basename(self.svg_path))[0]
        short = re.sub(r"[^A-Za-z0-9]", "", stem)[:8] or "design"
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save PES for the machine", defaultextension=".pes",
            initialfile=short + ".pes", initialdir=os.path.dirname(self.svg_path),
            filetypes=[("Brother PES", "*.pes")])
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

    # ---------------------------------------------------------------- objects
    def fill_objects(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self._images.clear()
        size = int(16 * self.ui)
        for o in self.design.objects:
            rgb = self.chosen_rgb(o)
            t = thread_for_rgb(rgb)
            img = swatch(self.root, [to_hex(o.source_color), to_hex(rgb)], size)
            self._images.append(img)
            skipped = o.obj_id in self.skipped
            self.tree.insert("", "end", iid=o.obj_id, text=f" {o.index}", image=img,
                             values=(o.obj_id, o.kind, f"{t.number}  {t.name}" if t else to_hex(rgb),
                                     "skip" if skipped else "sew"),
                             tags=("skipped",) if skipped else ())
        keep = [i for i in sel if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)

    def _selected(self) -> list[DesignObject]:
        if self.design is None:
            return []
        objs = [self._obj(i) for i in self.tree.selection()]
        if not objs:
            self.set_status("Select one or more objects in the list first.", WARN_COLOUR)
        return objs

    def change_thread(self):
        objs = self._selected()
        if not objs:
            return
        first = objs[0]
        others = []
        if len(objs) == 1:
            others = [o for o in self.design.objects
                      if o.source_color == first.source_color and o is not first]
        title = (f"Thread for {first.obj_id}" if len(objs) == 1
                 else f"Thread for {len(objs)} objects")
        picker = ThreadPicker(self.root, self.ui, title, thread_for_rgb(self.chosen_rgb(first)),
                              to_hex(first.source_color), len(others))
        result = picker.wait()
        if result is None:
            return
        targets = objs + (others if picker.apply_all.get() else [])
        for o in targets:
            if result == "reset":
                self.colours.pop(o.obj_id, None)
            else:
                self.colours[o.obj_id] = result.rgb
        self.fill_objects()
        self.refresh()

    def toggle_skip(self):
        objs = self._selected()
        if not objs:
            return
        skip = any(o.obj_id not in self.skipped for o in objs)
        for o in objs:
            (self.skipped.add if skip else self.skipped.discard)(o.obj_id)
        self.fill_objects()
        self.refresh()

    def reset_all(self):
        if self.design is None:
            return
        self.colours.clear()
        self.skipped.clear()
        self.fill_objects()
        self.refresh()

    # ---------------------------------------------------------------- planning
    def schedule(self, delay: int = 300):
        if self._job is not None:
            self.root.after_cancel(self._job)
        self._job = self.root.after(delay, self.refresh)

    def _read_settings(self):
        def num(var, label, blank_ok=False):
            text = var.get().strip()
            if not text and blank_ok:
                return None
            try:
                return float(text)
            except ValueError:
                raise ValueError(f"{label} must be a number, not {text!r}") from None

        hoop = (num(self.v_hoop_w, "Hoop width"), num(self.v_hoop_h, "Hoop height"))
        return dict(hoop_mm=hoop, margin_mm=num(self.v_margin, "Margin"),
                    width_mm=num(self.v_width, "Width", blank_ok=True), fit=self.v_fit.get(),
                    settings=Settings(stitch_len=num(self.v_stitch, "Stitch length"),
                                      keep_order=self.v_keep.get()))

    def refresh(self):
        self._job = None
        if self.design is None:
            self.draw_preview()
            return
        try:
            opts = self._read_settings()
            d = copy.deepcopy(self.design)
            for o in d.objects:
                o.color = self.colours.get(o.obj_id, o.source_color)
                o.skipped = o.obj_id in self.skipped
            self.hoop, self.margin = opts["hoop_mm"], opts["margin_mm"]
            plan = plan_design(d, **opts)
        except (ConversionError, ValueError) as e:
            self.plan = None
            self.b_save.configure(state="disabled")
            self.fill_order()
            self.l_info.configure(text="")
            self.draw_preview(message=str(e))
            self.set_status(str(e), ERROR_COLOUR)
            return
        self.plan = plan
        self.b_save.configure(state="normal")
        self.fill_order()
        rep = plan.report
        self.l_info.configure(
            text=f"{rep.width_mm:.1f} × {rep.height_mm:.1f} mm   ·   scale {rep.scale:.0%}   ·   "
                 f"{rep.stitch_count} stitches   ·   {rep.color_blocks} colour(s)   ·   "
                 f"{rep.jump_count} jumps")
        warnings = sorted(set(rep.warnings))
        if warnings:
            self.set_status("Note: " + "   ·   ".join(warnings), WARN_COLOUR)
        else:
            self.set_status("Ready. Save the PES when the preview looks right (Ctrl+S).", OK_COLOUR)
        self.draw_preview()

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

    # ---------------------------------------------------------------- preview
    def _schedule_draw(self):
        if self._draw_job is not None:
            self.root.after_cancel(self._draw_job)
        self._draw_job = self.root.after(80, self.draw_preview)

    def draw_preview(self, message: str | None = None):
        self._draw_job = None
        cv = self.canvas
        cv.delete("all")
        cw, ch = cv.winfo_width(), cv.winfo_height()
        if cw < 20 or ch < 20:
            return
        if self.plan is None:
            text = message or "Open an SVG to see the stitch preview here."
            cv.create_text(cw / 2, ch / 2, text=text, width=cw - 60, fill="#777777",
                           font=("Segoe UI", 11), justify="center")
            return
        hw, hh = self.hoop
        px = max(1.0, min((cw - 24) / hw, (ch - 24) / hh))
        # draw at 2x and shrink, which smooths the lines
        big = render_image(self.plan.pattern, self.hoop, px * 2, self.margin, self.v_jumps.get())
        img = big.resize((max(1, round(hw * px)), max(1, round(hh * px))), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(img, master=self.root)
        cv.create_image(cw / 2, ch / 2, image=self._photo)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    App(root, argv[0] if argv else None)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
