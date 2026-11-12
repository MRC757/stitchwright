"""Double-click this file to open the svg2pes window (no console window)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from svg2pes.gui import main  # noqa: E402

raise SystemExit(main())
