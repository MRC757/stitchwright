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
