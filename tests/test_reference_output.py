"""Tests: the embroidery files Stitchwright writes are exactly the same on every computer.

Each case converts a fixed design and compares the file byte for byte with the reference
copy in tests/reference/. CI runs this on Windows and on Linux, so a PES made by the Linux
version is the same file the PE540D has already sewn from the Windows version.

When the stitching is changed on purpose, write new reference files and check them in:

    STITCHWRIGHT_UPDATE_REFERENCE=1 python -m pytest tests/test_reference_output.py
"""
import os

import pytest

from stitchwright.convert import Choices, plan_design, write_machine_file
from stitchwright.fill import FillSpec, SatinSpec
from stitchwright.svg_import import load_svg

ROOT = os.path.join(os.path.dirname(__file__), "..")
REFERENCE = os.path.join(os.path.dirname(__file__), "reference")
SAMPLE = os.path.join(ROOT, "samples", "sample.svg")
FILLED = ["badge", "sun", "star"]

CASES = {
    "rows": Choices(fills={k: None for k in FILLED}, satins={"letterF": 2.0}),
    "contour": Choices(fills={k: None for k in FILLED}, fill_spec=FillSpec(pattern="contour")),
    "diamond": Choices(fills={k: None for k in FILLED},
                       fill_spec=FillSpec(pattern="diamond", angle=30),
                       satins={"letterF": SatinSpec(width=2.0, spacing=2.5, underlay=False,
                                                    style="bean")}),
    "wave": Choices(fills={"badge": None}, fill_spec=FillSpec(pattern="wave"),
                    satins={"star": SatinSpec(width=2.5, spacing=2.5, underlay=False,
                                              style="blanket")}),
    "crosshatch": Choices(fills={"sun": None}, fill_spec=FillSpec(pattern="crosshatch"),
                          satins={"badge": SatinSpec(width=2.0, spacing=1.5, underlay=False,
                                                     style="zigzag")}),
}


@pytest.mark.parametrize("ext", [".pes", ".dst"])
@pytest.mark.parametrize("case", list(CASES))
def test_output_is_the_same_on_every_computer(case, ext, tmp_path):
    plan = plan_design(load_svg(SAMPLE), CASES[case], placed=True)
    out = tmp_path / ("sample" + ext)         # the same name everywhere: PES stores it
    write_machine_file(plan, str(out))
    data = out.read_bytes()
    ref = os.path.join(REFERENCE, f"sample_{case}{ext}")
    if os.environ.get("STITCHWRIGHT_UPDATE_REFERENCE"):
        os.makedirs(REFERENCE, exist_ok=True)
        with open(ref, "wb") as f:
            f.write(data)
    if not os.path.exists(ref):
        pytest.fail(f"no reference file {os.path.basename(ref)}: see this file's docstring")
    with open(ref, "rb") as f:
        expected = f.read()
    if data != expected:
        first = next((i for i, (a, b) in enumerate(zip(data, expected)) if a != b),
                     min(len(data), len(expected)))
        pytest.fail(f"{case}{ext} differs from the reference from byte {first} "
                    f"({len(data)} bytes, reference {len(expected)})")
