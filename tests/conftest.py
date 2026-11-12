import os
import sys
import tkinter as tk

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


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
