import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TESTDATA = Path(__file__).resolve().parent.parent / "testdata"
MC60A = TESTDATA / "Gerber_MC60A_HV"

needs_testdata = pytest.mark.skipif(not MC60A.is_dir(), reason="DK's real files are not in testdata/")

# A 0603 package as Altium writes it: pin 1 at the reference point, pin 2 at +X, pads' box as RC.
PKG_0603 = """PKG 0603 1.397 -0.381 -0.381 1.778 0.381
RC -0.381 -0.381 2.159 0.762
PIN 1 S 0 0 0 U U
CT
OB -0.381 0.381 I
OS 0.381 0.381
OS 0.381 -0.381
OS -0.381 -0.381
OS -0.381 0.381
OE
CE
PIN 2 S 1.397 0 0 U U
CT
OB 1.016 0.381 I
OS 1.778 0.381
OS 1.778 -0.381
OS 1.016 -0.381
OS 1.016 0.381
OE
CE
"""


def write_odb(root: Path, comps, packages: str = PKG_0603, units: str = "MM") -> Path:
    """Minimal Altium-style ODB++ job. ``comps``: (side "top"/"bot", designator, pkg index, x, y,
    odb rotation) tuples; no per-pin TOP records, so pin 1 comes from the package."""
    (root / "matrix").mkdir(parents=True)
    (root / "matrix" / "matrix").write_text("STEP {\n    NAME=PCB\n}\n")
    eda = root / "steps" / "pcb" / "eda"
    eda.mkdir(parents=True)
    (eda / "data").write_text(f"HDR test\nUNITS={units}\n#\n{packages}")
    for side in ("top", "bot"):
        lines = [f"UNITS={units}", "@0 .comp_mount_type"]
        for s, des, pkg, x, y, rot in comps:
            if s == side:
                lines.append(f"CMP {pkg} {x} {y} {rot} N {des} VAL ;0=1")
        d = root / "steps" / "pcb" / "layers" / f"comp_+_{side}"
        d.mkdir(parents=True)
        (d / "components").write_text("\n".join(lines) + "\n")
    return root


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
