import math

import pytest

from conftest import MC60A, TESTDATA, needs_testdata
from panelcheck.io.pnp_altium import PnPError, looks_like_pnp, parse_pnp
from panelcheck.model.geometry import MM_PER_MIL, Transform2D
from panelcheck.model.layers import Side
from panelcheck.model.placement import place

DK_PNP = TESTDATA / "Pick Place for MC60A_HV_V8E.csv"

ALTIUM_PREAMBLE = (
    "Altium Designer Pick and Place Locations\r\n"
    "Z:\\Projects\\board\\Pick Place for board.csv\r\n"
    "\r\n"
    "========================================================================================\r\n"
    "File Design Information:\r\n"
    "\r\n"
    "Date:       09/10/26\r\n"
    "Units used: {unit}\r\n"
    "\r\n"
)


def write(tmp_path, text, name="pnp.csv"):
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return p


# ---- DK's real file ------------------------------------------------------------------------------


@needs_testdata
def test_dk_file():
    pnp = parse_pnp(DK_PNP)
    assert pnp.header_line == 13
    assert pnp.coord_set == "Center" and pnp.coord_sets == ["Center"]
    assert pnp.source_unit == "mm"
    assert len(pnp.parts) == 174
    assert (pnp.count(Side.TOP), pnp.count(Side.BOTTOM)) == (96, 78)
    assert pnp.info["Units used"] == "mm" and pnp.info["Variant"] == "No variations"
    assert "Z" not in pnp.info  # the "Z:\..." path line is not an info key
    assert pnp.warnings == []
    by = {p.designator: p for p in pnp.parts}
    h1 = by["H1"]
    assert (h1.comment, h1.side, h1.footprint, h1.x, h1.y, h1.rotation) == (
        "HEATSINK", Side.TOP, "MC60AHV-HEATSINK-V7", 39.37, 35.179, 0.0)  # fmt: skip
    assert by["R21"].side is Side.BOTTOM and by["R21"].x == pytest.approx(17.2085)
    assert by["CN3"].description == ""
    assert {p.rotation for p in pnp.parts} == {0.0, 90.0, 180.0, 270.0}
    assert looks_like_pnp(DK_PNP)


@needs_testdata
def test_dk_drill_txt_is_not_pnp():
    assert not looks_like_pnp(MC60A / "MC60A_HV_V8E-RoundHoles.TXT")


@needs_testdata
def test_dk_centroids_land_on_own_side_pads():
    """With Home at the Altium origin, part centroids sit on pads of their own side, unmirrored."""
    from panelcheck.io.gerber_loader import artwork_layers, load_layers
    from panelcheck.model.layers import LayerType
    from panelcheck.model.snap import SnapIndex

    idx = SnapIndex.from_layers(artwork_layers(load_layers(MC60A)))
    pnp = parse_pnp(DK_PNP)
    two_pin = [p for p in pnp.parts if p.footprint in ("0603", "0805", "1206")]
    assert len(two_pin) > 50
    for p in two_pin:
        # An 0603's pads are ~0.8 mm from its centroid; the nearest pad on its own side is that close.
        t = idx.nearest(p.x, p.y, 1.6, lambda t, s=p.side: t.kind == "pad" and t.layer.kind.side is s
                        and t.layer.kind.type is LayerType.PASTE)  # fmt: skip
        assert t is not None, p


# ---- synthetic edge cases ------------------------------------------------------------------------


def test_header_after_preamble_and_quoted_commas(tmp_path):
    text = ALTIUM_PREAMBLE.format(unit="mm") + (
        '"Designator","Comment","Layer","Footprint","Center-X(mm)","Center-Y(mm)","Rotation","Description"\r\n'
        '"C1","0.1uF","TopLayer","0603","1.5","-2.25","90","Ceramic, 0603, 25V"\r\n'
        '"R1","10K","BottomLayer","0603","10","20","-90",""\r\n'
        "\r\n"
    )
    pnp = parse_pnp(write(tmp_path, text))
    assert pnp.header_line == 10
    c1, r1 = pnp.parts
    assert (c1.x, c1.y, c1.rotation, c1.description) == (1.5, -2.25, 90.0, "Ceramic, 0603, 25V")
    assert r1.side is Side.BOTTOM and r1.rotation == 270.0


def test_mil_columns_convert_to_mm(tmp_path):
    text = ALTIUM_PREAMBLE.format(unit="mil") + (
        "Designator,Layer,Center-X(mil),Center-Y(mil),Rotation\nU1,TopLayer,1000,-500,45\n"
    )
    pnp = parse_pnp(write(tmp_path, text))
    assert pnp.source_unit == "mil"
    assert pnp.parts[0].x == pytest.approx(25.4) and pnp.parts[0].y == pytest.approx(-12.7)


def test_units_line_used_when_columns_have_no_suffix(tmp_path):
    text = ALTIUM_PREAMBLE.format(unit="mil") + "Designator,Layer,Mid X,Mid Y,Rotation\nU1,T,100,200,0\n"
    pnp = parse_pnp(write(tmp_path, text))
    assert pnp.coord_set == "Mid" and pnp.source_unit == "mil"
    assert pnp.parts[0].x == pytest.approx(100 * MM_PER_MIL)


def test_value_suffix_wins(tmp_path):
    text = "Designator,Layer,Center X,Center Y,Rotation\nU1,TopLayer,12.5mm,400mil,0\n"
    p = parse_pnp(write(tmp_path, text)).parts[0]
    assert (p.x, p.y) == pytest.approx((12.5, 10.16))


def test_unknown_units_assume_mm_with_warning(tmp_path):
    pnp = parse_pnp(write(tmp_path, "Designator,Layer,Center X,Center Y\nU1,Top,1,2\n"))
    assert pnp.parts[0].x == 1.0
    assert any("assumed mm" in w for w in pnp.warnings)


def test_coord_sets_prefer_center_then_mid_and_selectable(tmp_path):
    text = (
        "Designator,Layer,Ref X(mm),Ref Y(mm),Pad X(mm),Pad Y(mm),Mid X(mm),Mid Y(mm),Rotation\n"
        "U1,TopLayer,1,1,2,2,3,3,0\n"
    )
    path = write(tmp_path, text)
    pnp = parse_pnp(path)
    assert pnp.coord_sets == ["Mid", "Ref", "Pad"]
    assert pnp.coord_set == "Mid" and pnp.parts[0].x == 3
    assert parse_pnp(path, "Pad").parts[0].x == 2
    with pytest.raises(PnPError):
        parse_pnp(path, "Center")


def test_tab_delimited_txt(tmp_path):
    text = (
        "Units used: mm\n"
        "Designator\tComment\tLayer\tCenter-X(mm)\tCenter-Y(mm)\tRotation\n"
        "D1\tLED\tBottomLayer\t5\t6\t180\n"
    )
    pnp = parse_pnp(write(tmp_path, text, "pnp.txt"))
    assert pnp.parts[0].side is Side.BOTTOM and pnp.parts[0].comment == "LED"


def test_whitespace_with_quoted_fields(tmp_path):
    text = '"Designator" "Layer" "Center-X(mm)" "Center-Y(mm)" "Rotation"\n"J1" "TopLayer" "1.0" "2.0" "0"\n'
    pnp = parse_pnp(write(tmp_path, text, "pnp.txt"))
    assert (pnp.parts[0].designator, pnp.parts[0].y) == ("J1", 2.0)


def test_bad_rows_warn_and_skip(tmp_path):
    text = (
        "Designator,Layer,Center-X(mm),Center-Y(mm),Rotation\n"
        "U1,TopLayer,1,1,0\n"
        ",TopLayer,1,1,0\n"
        "U2,Inner,1,1,0\n"
        "U3,TopLayer,abc,1,0\n"
        "U4,TopLayer,1,1,xx\n"
        "U1,BottomLayer,2,2,0\n"
    )
    pnp = parse_pnp(write(tmp_path, text))
    assert [p.designator for p in pnp.parts] == ["U1", "U4", "U1"]
    assert pnp.parts[1].rotation == 0.0
    joined = "\n".join(pnp.warnings)
    for needle in ("no designator", "unknown layer", "bad X", "bad rotation", "Duplicate designators: U1"):
        assert needle in joined


def test_missing_header_or_columns_raise(tmp_path):
    with pytest.raises(PnPError, match="Designator"):
        parse_pnp(write(tmp_path, "just some text\n1,2,3\n"))
    with pytest.raises(PnPError, match="X/Y"):
        parse_pnp(write(tmp_path, "Designator,Layer,Rotation\nU1,Top,0\n"))
    with pytest.raises(PnPError, match="no parts"):
        parse_pnp(write(tmp_path, "Designator,Layer,Center X,Center Y\n"))


def test_cp1252_file(tmp_path):
    p = tmp_path / "pnp.csv"
    p.write_bytes("Designator,Comment,Layer,Center X(mm),Center Y(mm)\nC1,1µF,Top,1,2\n".encode("cp1252"))
    assert parse_pnp(p).parts[0].comment == "1µF"


# ---- placement -----------------------------------------------------------------------------------


def test_place_applies_instance_transform(tmp_path):
    pnp = parse_pnp(write(tmp_path, "Designator,Layer,Center X(mm),Center Y(mm),Rotation\nU1,Top,10,0,270\n"))
    a, b = place(pnp.parts, [Transform2D(5, 5), Transform2D(100, 0, 90)])
    assert (a.instance, a.x, a.y, a.rotation) == (0, 15, 5, 270)
    assert b.instance == 1 and (b.x, b.y) == pytest.approx((100, 10)) and b.rotation == 0
    assert math.isclose(Transform2D(0, 0, 45).apply(1, 0)[1], math.sqrt(0.5))
