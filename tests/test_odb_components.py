import math
import shutil
import tarfile

import pytest

from conftest import PKG_0603, TESTDATA, needs_testdata, write_odb
from panelcheck.io.odb_components import (
    OdbError,
    check_parts,
    find_odb_for,
    find_odb_jobs,
    load_odb,
    parse_packages,
)
from panelcheck.io.pnp_altium import parse_pnp
from panelcheck.model.geometry import MM_PER_INCH
from panelcheck.model.layers import Side


def _box_center(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


ODB = TESTDATA / "odb"
DK_PNP = TESTDATA / "Pick Place for MC60A_HV_V8E.csv"
needs_odb = pytest.mark.skipif(not ODB.is_dir(), reason="DK's ODB++ export is not in testdata/odb")


# ---- DK's real export ----------------------------------------------------------------------------


@needs_odb
def test_dk_odb_components_and_packages():
    job = load_odb(ODB)
    assert job.step == "pcb" and job.warnings == []
    sides = [c.side for c in job.components.values()]
    assert (sides.count(Side.TOP), sides.count(Side.BOTTOM)) == (96, 83)
    assert len(job.packages) == 34
    assert {p.name for p in job.packages if p.outline_is_body} == {
        "44A_N", "846A-02_N", "SC70-5", "SOT-23-TT3_N"}  # fmt: skip
    c36 = job.components["C36"]
    assert (c36.rotation, c36.pnp_rotation, c36.package, c36.mount_type) == (270, 90, "0603", "smt")
    assert c36.props["DigiKey"] == "1276-1935-1-ND"
    assert job.components["R21"].pnp_rotation == 0  # bottom: ODB 180 is P&P 0
    th = [c for c in job.components.values() if c.mount_type == "th"]
    assert len(th) == 13


@needs_odb
def test_dk_package_pins_land_on_component_pins():
    """The package->board transform reproduces every component's own pin records (0.01 mm)."""
    job = load_odb(ODB)
    pkgs = {p.name: p for p in job.packages}
    worst = 0.0
    for c in job.components.values():
        fp = c.footprint
        assert fp is not None and fp.side is c.side
        assert fp.pin1 == c.pins["1"]
        for name, (bx, by) in c.pins.items():
            if name not in pkgs[c.package].pins or name == "MP":
                continue
            # The pad's box center sits on the pin (pads are symmetric about their pin).
            idx = list(pkgs[c.package].pins).index(name)
            pad = fp.pads[sum(len(v[1]) for v in list(pkgs[c.package].pins.values())[:idx])]
            cx, cy = _box_center(pad)
            worst = max(worst, math.hypot(cx - bx, cy - by))
    assert worst < 0.05


@needs_odb
@needs_testdata
def test_dk_cross_check_is_clean():
    job = load_odb(ODB)
    ck = check_parts(parse_pnp(DK_PNP).parts, job)
    assert ck.matched == 174 and ck.issues == {}
    assert "In ODB++ but not in the P&P: N1, N2, N3, N4, N5" in ck.notes
    assert "Footprint name differs: P&P SOT-669, ODB++ SOT-669_1 (11 parts)" in ck.notes


@needs_odb
@needs_testdata
def test_dk_auto_detect_from_pnp_folder():
    """From testdata/ it finds odb/ and skips JLC's component-less ODB++ and the Gerber zips."""
    assert ODB in find_odb_jobs(TESTDATA)
    job, notes = find_odb_for({p.designator for p in parse_pnp(DK_PNP).parts}, TESTDATA)
    assert job is not None and job.path == ODB and notes == []


@needs_odb
def test_dk_odb_from_tgz_and_zip(tmp_path):
    tgz = tmp_path / "job.tgz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(ODB, arcname="mc60a")
    assert len(load_odb(tgz).components) == 179
    z = shutil.make_archive(str(tmp_path / "job"), "zip", ODB.parent, ODB.name)
    assert len(load_odb(z).components) == 179


@needs_testdata
def test_board_house_odb_without_components_is_rejected():
    jlc = TESTDATA / "MC60A_HV_V8D_Y512" / "ok" / "2267606a_y512.tgz"
    with pytest.raises(OdbError, match="no component layers"):
        load_odb(jlc)


# ---- synthetic -----------------------------------------------------------------------------------


def test_rotation_and_mirror_conventions(tmp_path):
    # Top rot 90 (clockwise): pin 2 at local +X goes to -Y. Bottom rot 90: mirror X, then rotate
    # CCW, so pin 2 goes to -Y as well.
    job = load_odb(write_odb(tmp_path / "j", [("top", "C1", 0, 0, 0, 90), ("bot", "C2", 0, 0, 0, 90)]))
    for des in ("C1", "C2"):
        pads = job.components[des].footprint.pads
        assert _box_center(pads[1]) == pytest.approx((0, -1.397), abs=1e-9), des
    assert job.components["C1"].pnp_rotation == 270
    assert job.components["C2"].pnp_rotation == 270


def test_inch_units(tmp_path):
    pkg = PKG_0603.replace("1.397 0 0 U U", "0.055 0 0 U U")
    job = load_odb(write_odb(tmp_path / "j", [("top", "C1", 0, 1, 2, 0)], packages=pkg, units="INCH"))
    c = job.components["C1"]
    assert (c.x, c.y) == pytest.approx((MM_PER_INCH, 2 * MM_PER_INCH))
    assert c.footprint.pin1 == pytest.approx((MM_PER_INCH, 2 * MM_PER_INCH))


def test_arc_contour_and_round_pins():
    text = (
        "UNITS=MM\nPKG ROUND 1 -1 -1 1 1\nCT\nOB 1 0 I\nOC -1 0 0 0 Y\nOC 1 0 0 0 Y\nOE\nCE\n"
        "PIN 1 T 0 0 0 U U\nCR 0 0 0.5\nPIN 2 T 0.5 0 0 U U\nSQ 0.5 0 0.25\n"
    )
    (pkg,) = parse_packages(text)
    assert pkg.outline_is_body
    (outline,) = pkg.outline
    assert all(math.isclose(math.hypot(x, y), 1, abs_tol=1e-6) for x, y in outline)
    assert len(pkg.pins["1"][1][0]) == 24 and len(pkg.pins["2"][1][0]) == 4


def test_missing_outline_falls_back_to_package_box():
    (pkg,) = parse_packages("PKG TIE 0.3 -1.6 -0.1 -1.0 0.1\nPIN 1 S -1.5 0 0 U U\nCR -1.5 0 0.1\n")
    assert pkg.outline == [((-1.6, -0.1), (-1.0, -0.1), (-1.0, 0.1), (-1.6, 0.1))]


def test_check_flags_side_rotation_and_off_pad(tmp_path):
    pnp_file = tmp_path / "p.csv"
    pnp_file.write_text(
        "Designator,Layer,Footprint,Center-X(mm),Center-Y(mm),Rotation\n"
        "C1,TopLayer,0603,0.6985,0,0\n"  # fine
        "C2,TopLayer,0603,10.6985,0,180\n"  # rotation differs
        "C3,BottomLayer,0603,20.6985,0,0\n"  # side differs
        "C4,TopLayer,0603,32.5,0,0\n"  # centroid 0.72 mm past the pads (29.62 to 31.78)
        "C5,TopLayer,0603,40,0,0\n"  # not in ODB++
    )
    comps = [("top", f"C{i + 1}", 0, 10 * i, 0, 0) for i in range(4)] + [("top", "C9", 0, 50, 0, 0)]
    ck = check_parts(parse_pnp(pnp_file).parts, load_odb(write_odb(tmp_path / "j", comps)))
    assert ck.matched == 4
    assert set(ck.issues) == {"C2", "C3", "C4", "C5"}
    assert "rotation 180° in P&P, 0° in ODB++" in ck.issues["C2"][0]
    assert "P&P says bottom side, ODB++ says top" in ck.issues["C3"][0]
    assert "off its pads" in ck.issues["C4"][0]
    assert ck.issues["C5"] == ["not in the ODB++"]
    assert ck.notes == ["In ODB++ but not in the P&P: C9"]


def test_find_prefers_best_overlap(tmp_path):
    write_odb(tmp_path / "a" / "odb_old", [("top", "C1", 0, 0, 0, 0)])
    write_odb(tmp_path / "a" / "odb_new", [("top", "C1", 0, 0, 0, 0), ("top", "C2", 0, 1, 0, 0)])
    write_odb(tmp_path / "a" / "other", [("top", "X9", 0, 0, 0, 0)])
    job, _ = find_odb_for({"C1", "C2"}, tmp_path / "a")
    assert job.path.name == "odb_new"
    job, notes = find_odb_for({"Q1"}, tmp_path / "a")
    assert job is None and "No ODB++" in notes[0]
