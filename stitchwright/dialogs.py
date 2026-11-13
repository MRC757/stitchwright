"""The window's dialogs: thread picker, text, fill and outline, picture import;
and small Tk helpers they share with the main window (fonts, colour swatches)."""
from __future__ import annotations

import math
import os
import tkinter as tk
from dataclasses import replace
from tkinter import filedialog, ttk
from tkinter import font as tkfont

from PIL import Image, ImageTk

from .fill import FillSpec, SatinSpec
from .fontpreview import is_symbol_font, sample_image
from .geometry import bounds
from .image_import import TraceSettings
from .image_import import preview as image_preview
from .palette import Thread, brother_threads, nearest_brother, thread_for_rgb, to_hex
from .text import HERSHEY_PREFIX, TextSpec, default_font, installed_fonts, text_polylines

OK_COLOUR, WARN_COLOUR, ERROR_COLOUR = "#2e7d32", "#9a6700", "#c62828"
SELECT_COLOUR = "#1a73e8"


_FONTS: dict = {}


def ui_font(widget, size: int | None = None, bold: bool = False) -> tkfont.Font:
    """The platform's default UI font (Segoe UI on Windows), optionally resized or bold.
    Fonts are cached: Tk forgets a named font when its Python object is garbage-collected."""
    key = (str(widget.tk), size, bold)
    f = _FONTS.get(key)
    if f is None:
        f = tkfont.nametofont("TkDefaultFont", root=widget).copy()
        if size:
            f.configure(size=size)
        if bold:
            f.configure(weight="bold")
        _FONTS[key] = f
    return f


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
                           font=ui_font(cv, bold=selected))
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


class FontList(ttk.Frame):
    """A scrolling list of fonts, each name drawn in its own font, with a search box.
    Rows are drawn only when they scroll into view (a computer can have hundreds of
    fonts) and kept. on_select(name) is called when a font is chosen (click, arrow keys,
    Enter in the search box); on_hover(name or None) as the mouse moves over the rows."""

    def __init__(self, parent, fonts: dict, ui: float, selected: str,
                 on_select=None, on_hover=None, rows: int = 7):
        super().__init__(parent)
        self.fonts = fonts
        self.names = list(fonts)
        self.shown = list(self.names)
        self.selected = selected
        self.on_select, self.on_hover = on_select, on_hover
        self.row = int(34 * ui)
        self._photos: dict = {}              # name -> PhotoImage, or None if it can't be drawn
        self._drawn: set = set()
        self._hover = None

        self.v_search = tk.StringVar()
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 3))
        ttk.Label(top, text="Search").pack(side="left")
        self.e_search = ttk.Entry(top, textvariable=self.v_search)
        self.e_search.pack(side="left", fill="x", expand=True, padx=(6, 0))
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True)
        self.cv = tk.Canvas(box, width=int(340 * ui), height=self.row * rows, bg="white",
                            highlightthickness=1, highlightbackground="#cccccc",
                            yscrollincrement=self.row, takefocus=1)
        sb = ttk.Scrollbar(box, orient="vertical", command=self._yview)
        self.cv.configure(yscrollcommand=sb.set)
        self.cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        self.v_search.trace_add("write", lambda *a: self._filter())
        self.cv.bind("<Configure>", lambda e: self._layout())
        self.cv.bind("<MouseWheel>", self._wheel)
        self.cv.bind("<Button-4>", lambda e: self._scroll(-3))       # mouse wheel on Linux
        self.cv.bind("<Button-5>", lambda e: self._scroll(3))
        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<Leave>", lambda e: self._set_hover(None))
        for w in (self.cv, self.e_search):
            w.bind("<Up>", lambda e: self._step(-1))
            w.bind("<Down>", lambda e: self._step(1))
            w.bind("<Prior>", lambda e: self._step(-rows))
            w.bind("<Next>", lambda e: self._step(rows))
        self.cv.bind("<Home>", lambda e: self._step(-len(self.shown)))
        self.cv.bind("<End>", lambda e: self._step(len(self.shown)))
        self.e_search.bind("<Return>", self._search_enter)
        self._layout()                       # scroll area now, so see() works before it's shown

    # ------------------------------------------------------------ drawing
    def photo(self, name: str):
        """The picture of name in its own font (cached); None if the font can't be drawn."""
        if name not in self._photos:
            path, idx = self.fonts[name]
            img = sample_image(path, idx, name, self.row - 6)
            self._photos[name] = None if img is None else ImageTk.PhotoImage(img, master=self)
        return self._photos[name]

    def _layout(self):
        self.cv.delete("all")
        self._drawn.clear()
        w = max(self.cv.winfo_width(), int(self.cv["width"]))
        self.cv.configure(scrollregion=(0, 0, w, max(1, len(self.shown)) * self.row))
        if not self.shown:
            self.cv.create_text(10, self.row / 2, anchor="w", text="No font matches the search",
                                fill="#888888", font=ui_font(self))
        self._draw_visible()
        self._mark()

    def _draw_visible(self):
        top = self.cv.canvasy(0)
        h = max(self.cv.winfo_height(), int(self.cv["height"]))
        first = max(0, int(top // self.row) - 1)
        last = min(len(self.shown), int((top + h) // self.row) + 2)
        for i in range(first, last):
            name = self.shown[i]
            if name not in self._drawn:
                self._drawn.add(name)
                self._draw_row(i, name)

    def _draw_row(self, i: int, name: str):
        y = i * self.row + self.row / 2
        img = self.photo(name)
        path, idx = self.fonts[name]
        if img is None:
            self.cv.create_text(8, y, anchor="w", text=f"{name}  (can't show this font)",
                                fill="#888888", font=ui_font(self))
            return
        self.cv.create_image(6, y, anchor="w", image=img)
        if is_symbol_font(path, idx):        # its pictures don't spell the name: say it too
            self.cv.create_text(16 + img.width(), y, anchor="w", text=name,
                                fill="#777777", font=ui_font(self))

    def _mark(self):
        self.cv.delete("sel")
        if self.selected in self.shown:
            i = self.shown.index(self.selected)
            w = max(self.cv.winfo_width(), int(self.cv["width"]))
            self.cv.create_rectangle(0, i * self.row, w, (i + 1) * self.row,
                                     fill="#d4e6fb", outline="", tags="sel")
            self.cv.tag_lower("sel")

    # ------------------------------------------------------------ scrolling
    def _yview(self, *args):
        self.cv.yview(*args)
        self._draw_visible()

    def _scroll(self, rows: int):
        self.cv.yview_scroll(rows, "units")
        self._draw_visible()

    def _wheel(self, event):
        self._scroll(-3 if event.delta > 0 else 3)
        return "break"

    def see(self, name: str):
        """Scroll so that name is in view."""
        if name not in self.shown:
            return
        i = self.shown.index(name)
        top = self.cv.canvasy(0)
        h = max(self.cv.winfo_height(), int(self.cv["height"]))
        rows_in_view = max(1, h // self.row)
        if i * self.row < top or (i + 1) * self.row > top + h:
            first = max(0, i - rows_in_view // 2)
            self.cv.yview_moveto(first / max(1, len(self.shown)))
        self._draw_visible()

    # ------------------------------------------------------------ choosing
    def select(self, name: str, notify: bool = True):
        self.selected = name
        self._mark()
        self.see(name)
        if notify and self.on_select:
            self.on_select(name)

    def add(self, name: str):
        """Add a font (e.g. from a font file) and choose it."""
        if name not in self.names:
            self.names.append(name)
        self._photos.pop(name, None)
        self.v_search.set("")                # shows the whole list, with the new font
        self._filter()
        self.select(name)

    def _index_at(self, y: int) -> int | None:
        i = int(self.cv.canvasy(y) // self.row)
        return i if 0 <= i < len(self.shown) else None

    def _click(self, event):
        self.cv.focus_set()
        i = self._index_at(event.y)
        if i is not None:
            self.select(self.shown[i])

    def _motion(self, event):
        i = self._index_at(event.y)
        self._set_hover(None if i is None else self.shown[i])

    def _set_hover(self, name):
        if name != self._hover:
            self._hover = name
            if self.on_hover:
                self.on_hover(name)

    def _step(self, delta: int):
        if not self.shown:
            return "break"
        i = self.shown.index(self.selected) if self.selected in self.shown else -1
        i = max(0, min(len(self.shown) - 1, i + delta if i >= 0 else 0))
        self.select(self.shown[i])
        return "break"

    def _filter(self):
        typed = self.v_search.get().strip().lower()
        self.shown = [n for n in self.names if typed in n.lower()]
        self.cv.yview_moveto(0)
        self._layout()
        if self.selected in self.shown:
            self.see(self.selected)

    def _search_enter(self, event):
        if self.shown:
            self.select(self.selected if self.selected in self.shown else self.shown[0])
        return "break"


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
        self._hover: str | None = None       # font under the mouse in the list: previewed

        d = default_font()
        if spec is None:
            spec = TextSpec("", d[1] if d else "", d[0] if d else "", d[2] if d else 0)
        elif (d and not spec.font_path.startswith(HERSHEY_PREFIX)
              and not os.path.exists(spec.font_path)):
            # the font isn't on this computer (e.g. a project from elsewhere): use the default
            spec = replace(spec, font_path=d[1], font_name=d[0], font_index=d[2])
        if spec.font_name and spec.font_name not in self.fonts and spec.font_path:
            self.fonts[spec.font_name] = (spec.font_path, spec.font_index)
            self.names.append(spec.font_name)

        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Text").grid(row=0, column=0, sticky="nw", pady=2)
        self.t_text = tk.Text(f, width=44, height=3, wrap="none", undo=True,
                              font=ui_font(self, size=11))
        self.t_text.grid(row=0, column=1, columnspan=3, sticky="we", pady=2)
        self.t_text.insert("1.0", spec.text)

        self.v_font = tk.StringVar(value=spec.font_name)
        ttk.Label(f, text="Font").grid(row=1, column=0, sticky="nw", pady=(6, 2))
        self.font_list = FontList(f, self.fonts, ui, spec.font_name,
                                  on_select=self._font_chosen, on_hover=self._font_hover)
        self.font_list.grid(row=1, column=1, columnspan=2, sticky="we", pady=2)
        ttk.Button(f, text="Font file…", command=self._browse).grid(row=1, column=3, sticky="nw",
                                                                   padx=(6, 0), pady=(2, 0))

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
                  "each letter as one clean line: best for small lettering. Point at a font to\n"
                  "see your text in it below; click it (or use the arrow keys) to choose it.").grid(
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
        # Ctrl+Enter means OK: stop the text box also treating it as Enter (a new line)
        self.t_text.bind("<Control-Return>", lambda e: (self._ok(), "break")[1])
        for v in (self.v_height, self.v_spacing, self.v_align, self.v_arc, self.v_mono):
            v.trace_add("write", lambda *a: self._schedule())
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-Return>", lambda e: self._ok())
        _centre_on(self, parent)
        self.update_idletasks()
        self.font_list.see(spec.font_name)
        self._preview()

    def _font_chosen(self, name: str):
        self.v_font.set(name)
        self._schedule()

    def _font_hover(self, name: str | None):
        self._hover = name
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
            self.font_list.add(name)          # chooses it, which updates v_font and the preview

    def _spec(self, font: str | None = None) -> TextSpec:
        """The settings as typed, in the chosen font (or in `font`, for a preview)."""
        name = (font or self.v_font.get()).strip()
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
        """Draw the text in the chosen font, or in the font under the mouse in the list.
        Before any text is typed, a sample shows what the font looks like."""
        self._job = None
        cv = self.cv
        cv.delete("all")
        hover = self._hover if self._hover in self.fonts else None
        try:
            spec = self._spec(hover)
            sample = not spec.text.strip()
            if sample:
                spec = replace(spec, text="AaBbCc 123", monogram=False)
            polys, missing = text_polylines(spec)
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
        msg = "Sample (type the text to add)" if sample else f"{x1 - x0:.1f} × {y1 - y0:.1f} mm"
        if hover and hover != self.v_font.get():
            msg = f"Preview in {hover} (click to choose it)   ·   " + msg
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
                ("Row spacing (mm)", self.v_spacing, "0.2 covers solidly; larger is lighter"),
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


class ImageDialog(tk.Toplevel):
    """Settings for turning a picture into stitches, with a live preview of the
    colour reduction. After wait(), .result is None or (TraceSettings, fill: bool)."""

    def __init__(self, parent, ui: float, path: str, width_mm: float):
        super().__init__(parent)
        self.title(f"Import picture: {os.path.basename(path)}")
        self.transient(parent)
        self.resizable(False, False)
        self.ui, self.path = ui, path
        self.result = None
        self._job = None
        self._photo = None
        self.v_colours = tk.StringVar(value="4")
        self.v_width = tk.StringVar(value=f"{width_mm:g}")
        self.v_bg = tk.BooleanVar(value=True)
        self.v_detail = tk.StringVar(value="Normal")
        self.v_fill = tk.BooleanVar(value=True)
        grey = dict(foreground="#666666")
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        size = int(360 * ui)
        self.cv = tk.Canvas(f, width=size, height=size, bg="white", highlightthickness=1,
                            highlightbackground="#cccccc")
        self.cv.grid(row=0, column=0, rowspan=8, padx=(0, 12))
        ttk.Label(f, text="Thread colours").grid(row=0, column=1, sticky="w")
        ttk.Spinbox(f, from_=1, to=8, textvariable=self.v_colours, width=5,
                    command=self._schedule).grid(row=0, column=2, sticky="w")
        ttk.Label(f, text="Width (mm)").grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Entry(f, textvariable=self.v_width, width=7).grid(row=1, column=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(f, text="Leave out the background colour", variable=self.v_bg,
                        command=self._schedule).grid(row=2, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(f, text="Detail").grid(row=3, column=1, sticky="w", pady=(6, 0))
        ttk.Combobox(f, textvariable=self.v_detail, values=("Fine", "Normal", "Coarse"),
                     state="readonly", width=8).grid(row=3, column=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(f, text="Fill the areas (else outlines only)",
                        variable=self.v_fill).grid(row=4, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(f, **grey, justify="left", wraplength=int(250 * ui), text=(
            "Simple pictures with a few flat colours work best: logos, clip art, drawings. "
            "Photos don't turn into good embroidery.")).grid(row=5, column=1, columnspan=2,
                                                             sticky="w", pady=(10, 0))
        self.info = ttk.Label(f, text="", foreground="#555555", wraplength=int(250 * ui))
        self.info.grid(row=6, column=1, columnspan=2, sticky="nw", pady=(8, 0))
        bf = ttk.Frame(f)
        bf.grid(row=7, column=1, columnspan=2, sticky="se")
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bf, text="Import", command=self._ok).pack(side="right", padx=6)
        for v in (self.v_colours, self.v_detail):
            v.trace_add("write", lambda *a: self._schedule())
        self.bind("<Escape>", lambda e: self.destroy())
        _centre_on(self, parent)
        self._preview()

    def _settings(self) -> TraceSettings:
        try:
            colours = int(self.v_colours.get())
            width = float(self.v_width.get())
        except ValueError:
            raise ValueError("Colours and width must be numbers") from None
        if not 1 <= colours <= 8:
            raise ValueError("Choose 1 to 8 colours")
        if width <= 0:
            raise ValueError("Width must be more than 0")
        return TraceSettings(colours, width, self.v_bg.get(), self.v_detail.get().lower())

    def _schedule(self):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(250, self._preview)

    def _preview(self):
        self._job = None
        try:
            s = self._settings()
            img = image_preview(self.path, s)
        except (ValueError, OSError) as e:
            self.info.configure(text=str(e), foreground=ERROR_COLOUR)
            return
        size = int(self.cv["width"])
        img.thumbnail((size - 8, size - 8), Image.NEAREST)
        self._photo = ImageTk.PhotoImage(img, master=self)
        self.cv.delete("all")
        self.cv.create_image(size / 2, size / 2, image=self._photo)
        names = []
        colours = sorted({c for c in img.getdata() if c != (255, 255, 255)})
        for c in colours[:8]:
            t = nearest_brother(c)
            names.append(f"{t.number} {t.name}")
        self.info.configure(text="Threads: " + (", ".join(dict.fromkeys(names)) or "none"),
                            foreground="#555555")

    def _ok(self):
        try:
            s = self._settings()
        except ValueError as e:
            self.info.configure(text=str(e), foreground=ERROR_COLOUR)
            return
        self.result = (s, self.v_fill.get())
        self.destroy()

    def wait(self):
        self.grab_set()
        self.focus_set()
        self.wait_window()
        return self.result
