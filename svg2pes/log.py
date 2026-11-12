"""Logging to a file, so problems in the window app (which has no console) can be reported.

The log lives in %LOCALAPPDATA%\\svg2pes\\svg2pes.log on Windows, ~/.svg2pes/svg2pes.log
elsewhere (or the temp folder if neither can be written), and rotates at about 1 MB.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import tempfile

LOG_NAME = "svg2pes.log"
log = logging.getLogger("svg2pes")


def default_log_dir() -> str:
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return os.path.join(os.environ["LOCALAPPDATA"], "svg2pes")
    return os.path.join(os.path.expanduser("~"), ".svg2pes")


def setup_logging(log_dir: str | None = None) -> str:
    """Send svg2pes log messages to a rotating file (and to stderr when there is one).
    Returns the log file's path. Safe to call more than once."""
    folder = log_dir or default_log_dir()
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, LOG_NAME)
        open(path, "a", encoding="utf-8").close()         # can we write here?
    except OSError:
        folder = tempfile.gettempdir()
        path = os.path.join(folder, LOG_NAME)
    for h in list(log.handlers):                            # calling twice: start fresh
        log.removeHandler(h)
        h.close()
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2,
                                              encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(fh)
    if sys.stderr is not None:                              # a windowed .exe has no stderr
        sh = logging.StreamHandler()
        sh.setLevel(logging.WARNING)
        sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        log.addHandler(sh)
    log.setLevel(logging.INFO)
    log.propagate = False
    return path


class RepeatGuard:
    """Says whether an error dialog should be shown: not again within `quiet` seconds
    of the last one, so one fault repeating in a loop can't bury the user in dialogs."""

    def __init__(self, quiet: float = 1.0):
        self.quiet = quiet
        self.last: float | None = None

    def should_show(self, now: float) -> bool:
        if self.last is not None and now - self.last < self.quiet:
            return False
        self.last = now
        return True
