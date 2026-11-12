"""Double-click this file to open the Stitchwright window (no console window)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stitchwright.gui import main  # noqa: E402

raise SystemExit(main())
