"""Shared test fixtures and helpers (import helpers with `from conftest import ...`)."""
import math
import os
import sys
import tkinter as tk

import pyembroidery
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "samples", "sample.svg")
RED = (0xED, 0x17, 0x1F)
TEXT_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="60mm" height="40mm" viewBox="0 0 60 40">'
            '<text x="10" y="20" font-family="Arial" font-size="10" fill="#d62828">Hi</text>'
            '<text x="30" y="35" font-family="NoSuchFont" font-size="6" text-anchor="middle">Mid</text>'
            '</svg>')


def write_svg(tmp_path, body, w=60, h=40, name="t.svg"):
    p = tmp_path / name
    p.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" '
                 f'viewBox="0 0 {w} {h}">{body}</svg>')
    return str(p)


def stitch_lengths(pattern):
    """Lengths (mm) of every needle-to-needle stitch, skipping zero-length ones."""
    out, last = [], None
    for x, y, c in pattern.stitches:
        c &= 0xFF
        if c == pyembroidery.STITCH:
            if last is not None and (x, y) != last:
                out.append(math.dist((x, y), last) / 10)
            last = (x, y)
        elif c in (pyembroidery.JUMP, pyembroidery.TRIM, pyembroidery.COLOR_CHANGE):
            last = None if c != pyembroidery.JUMP else (x, y)
    return out


def any_font():
    """(name, path, index) of an installed outline font, or skip the test."""
    from svg2pes.text import default_font
    f = default_font()
    if f is None:
        pytest.skip("no fonts installed")
    return f


def square(x0, y0, x1, y1):
    from svg2pes.svg_import import Polyline
    return Polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], True)


@pytest.fixture(scope="session")
def tk_root():
    """One Tk interpreter for the whole test run: creating a second tk.Tk() in the same
    process fails on some Windows Pythons (Tcl can't re-read init.tcl). Tests build
    their windows on Toplevels of this root."""
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def window(tk_root):
    """A fresh Toplevel for one test, destroyed afterwards."""
    top = tk.Toplevel(tk_root)
    top.withdraw()
    yield top
    try:
        top.destroy()
    except tk.TclError:
        pass
