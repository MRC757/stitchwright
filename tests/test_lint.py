"""pyflakes over the package and the tests: no unused imports or undefined names."""
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_pyflakes_is_clean():
    api = pytest.importorskip("pyflakes.api")
    from pyflakes.reporter import Reporter
    import io
    out, err = io.StringIO(), io.StringIO()
    count = api.checkRecursive([os.path.join(ROOT, "stitchwright"), os.path.join(ROOT, "tests")],
                               Reporter(out, err))
    assert count == 0, out.getvalue() + err.getvalue()


def test_requirements_pins_the_same_libraries_as_pyproject():
    """requirements.txt pins the build's versions of exactly the libraries pyproject.toml
    depends on, so neither list can gain or lose one without the other."""
    import re
    tomllib = pytest.importorskip("tomllib")          # Python 3.11 and later

    def name(spec):
        return re.split(r"[<>=!~\[; ]", spec.strip(), maxsplit=1)[0].lower().replace("_", "-")

    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        wanted = {name(d) for d in tomllib.load(f)["project"]["dependencies"]}
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip() and not ln.startswith("#")]
    assert {name(ln) for ln in lines} == wanted
    assert all("==" in ln for ln in lines), "requirements.txt should pin exact versions"


def test_exe_version_matches_the_package():
    """version_info.txt (the .exe's file properties) must say the same version."""
    import re
    from stitchwright import __version__
    text = open(os.path.join(ROOT, "version_info.txt"), encoding="utf-8").read()
    nums = tuple(int(n) for n in __version__.split("."))
    assert f"'FileVersion', '{__version__}'" in text
    assert f"'ProductVersion', '{__version__}'" in text
    for field in ("filevers", "prodvers"):
        m = re.search(field + r"=\((\d+), (\d+), (\d+), \d+\)", text)
        assert m and tuple(int(g) for g in m.groups()) == nums, field
