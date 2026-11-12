"""pyflakes over the package and the tests: no unused imports or undefined names."""
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_pyflakes_is_clean():
    api = pytest.importorskip("pyflakes.api")
    from pyflakes.reporter import Reporter
    import io
    out, err = io.StringIO(), io.StringIO()
    count = api.checkRecursive([os.path.join(ROOT, "svg2pes"), os.path.join(ROOT, "tests")],
                               Reporter(out, err))
    assert count == 0, out.getvalue() + err.getvalue()


def test_exe_version_matches_the_package():
    """version_info.txt (the .exe's file properties) must say the same version."""
    import re
    from svg2pes import __version__
    text = open(os.path.join(ROOT, "version_info.txt"), encoding="utf-8").read()
    nums = tuple(int(n) for n in __version__.split("."))
    assert f"'FileVersion', '{__version__}'" in text
    assert f"'ProductVersion', '{__version__}'" in text
    for field in ("filevers", "prodvers"):
        m = re.search(field + r"=\((\d+), (\d+), (\d+), \d+\)", text)
        assert m and tuple(int(g) for g in m.groups()) == nums, field
