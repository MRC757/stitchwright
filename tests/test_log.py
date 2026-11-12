import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from svg2pes.log import RepeatGuard, log, setup_logging  # noqa: E402


def test_setup_logging_writes_to_the_given_folder(tmp_path):
    path = setup_logging(str(tmp_path / "logs"))
    assert path == str(tmp_path / "logs" / "svg2pes.log")
    log.info("hello from the test")
    for h in log.handlers:
        h.flush()
    assert "hello from the test" in open(path, encoding="utf-8").read()
    # calling again does not duplicate handlers
    setup_logging(str(tmp_path / "logs"))
    assert sum(1 for h in log.handlers if hasattr(h, "baseFilename")) == 1
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()


def test_repeat_guard_stops_dialog_storms():
    g = RepeatGuard(quiet=1.0)
    assert g.should_show(10.0)
    assert not g.should_show(10.5)        # same fault repeating
    assert g.should_show(11.2)            # a while later: show again


def test_errors_in_window_handlers_are_logged_and_reported(tmp_path, monkeypatch, tk_root, window):
    import tkinter as tk
    from tkinter import messagebox
    from svg2pes import gui

    path = setup_logging(str(tmp_path))
    shown = []
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: shown.append(a))
    app = gui.App(window)
    app.log_path = path
    old = tk_root.report_callback_exception
    tk_root.report_callback_exception = app.on_exception
    try:
        def broken():
            raise RuntimeError("deliberate test failure")

        b = tk.Button(window, command=broken)
        b.invoke()                       # Tk routes the error to report_callback_exception
        b.invoke()                       # straight away again: logged, but no second dialog
        window.update()
    finally:
        tk_root.report_callback_exception = old
    for h in log.handlers:
        h.flush()
    text = open(path, encoding="utf-8").read()
    assert text.count("deliberate test failure") >= 2 and "Traceback" in text
    assert len(shown) == 1 and "deliberate test failure" in shown[0][1]
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
