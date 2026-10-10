import math
import zipfile

from gerbonara.graphic_objects import Flash, Line
from gerbonara.utils import MM

from conftest import MC60A, needs_testdata
from panelcheck.io.gerber_loader import SourceFile, artwork_layers, load_layer, load_layers, sniff
from panelcheck.model.layers import (
    ID_CONTENT,
    ID_EXTENSION,
    ID_X2,
    LayerKind,
    LayerType,
    Side,
)

GERBER = """G04 test*
%FSLAX25Y25*%
%MOMM*%
{attr}%ADD10C,0.50000*%
D10*
X0Y0D03*
X100000Y0D03*
M02*
"""

EXCELLON = """M48
;Layer_Color=9474304
INCH,LZ
T1C0.0100
%
T01
X0001Y0001
M30
"""


def test_sniff():
    assert sniff(GERBER.format(attr="").encode()) == "gerber"
    assert sniff(EXCELLON.encode()) == "excellon"
    assert sniff(b"Output: NC Drill Files\nType  : NC Drill\n") is None
    assert sniff(b"\x00\x01binary") is None


def test_extension_identifies_gerber():
    la = load_layer(SourceFile("board.GBS", GERBER.format(attr="").encode()))
    assert la.kind == LayerKind(LayerType.MASK, Side.BOTTOM)
    assert la.id_source == ID_EXTENSION
    assert la.parsed is not None and la.error is None


def test_x2_attribute_beats_extension():
    data = GERBER.format(attr="%TF.FileFunction,Copper,L4,Bot*%\n").encode()
    la = load_layer(SourceFile("renamed_by_board_house.GTL", data))
    assert la.kind == LayerKind(LayerType.COPPER, Side.BOTTOM)
    assert la.id_source == ID_X2


def test_altium_comment_x2_form():
    data = GERBER.format(attr="G04 #@! TF.FileFunction,Legend,Top*\n").encode()
    la = load_layer(SourceFile("silk.gbr", data))
    assert la.kind == LayerKind(LayerType.SILK, Side.TOP)
    assert la.id_source == ID_X2


def test_unknown_gerber_is_parsed_for_manual_assignment():
    la = load_layer(SourceFile("panel_layer_7.art", GERBER.format(attr="").encode()))
    assert la.kind.type is LayerType.UNKNOWN
    assert la.id_source == ID_CONTENT
    assert la.parsed is not None


def test_excellon_any_extension_and_layer_color():
    la = load_layer(SourceFile("drill.whatever", EXCELLON.encode()))
    assert la.kind.type is LayerType.DRILL
    assert la.file_color == "#009190"
    assert la.color == "#009190"


def test_reports_are_not_parsed():
    cases = [
        ("x.DRR", b"report"),
        ("Status Report.Txt", b"Output: Gerber Files\n"),
        ("pnp.csv", b"Designator,X"),
    ]
    for name, data in cases:
        la = load_layer(SourceFile(name, data))
        assert la.kind.type is LayerType.REPORT and la.parsed is None and not la.visible


def test_corrupt_gerber_never_raises():
    bad = b"%FSLAX25Y25*%\n%MOMM*%\nG04 x*\n%ADD10C,0.5*%\nD99*\nX0Y0D03*\nM02*\n"
    la = load_layer(SourceFile("bad.GTL", bad))
    assert la.error or la.parsed is not None


def test_zip_loading(tmp_path):
    z = tmp_path / "set.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("gerbers/b.GTL", GERBER.format(attr=""))
        zf.writestr("gerbers/b.TXT", EXCELLON)
        zf.writestr("readme.html", "<html/>")
    kinds = {la.name: la.kind.type for la in load_layers(z)}
    assert kinds == {
        "gerbers/b.GTL": LayerType.COPPER,
        "gerbers/b.TXT": LayerType.DRILL,
        "readme.html": LayerType.REPORT,
    }


# ---- DK's real Altium output --------------------------------------------------------------------


@needs_testdata
def test_mc60a_identification():
    layers = {la.name: la for la in load_layers(MC60A)}
    expect = {
        "MC60A_HV_V8E.GTL": (LayerType.COPPER, Side.TOP, None, "#FF0000"),
        "MC60A_HV_V8E.GBL": (LayerType.COPPER, Side.BOTTOM, None, "#0000FF"),
        "MC60A_HV_V8E.G1": (LayerType.COPPER, Side.INNER, 1, "#BC8E00"),
        "MC60A_HV_V8E.G2": (LayerType.COPPER, Side.INNER, 2, "#70DBFA"),
        "MC60A_HV_V8E.GTO": (LayerType.SILK, Side.TOP, None, "#FFFF00"),
        "MC60A_HV_V8E.GBO": (LayerType.SILK, Side.BOTTOM, None, "#808000"),
        "MC60A_HV_V8E.GTS": (LayerType.MASK, Side.TOP, None, "#800080"),
        "MC60A_HV_V8E.GBS": (LayerType.MASK, Side.BOTTOM, None, "#FF00FF"),
        "MC60A_HV_V8E.GTP": (LayerType.PASTE, Side.TOP, None, "#808080"),
        "MC60A_HV_V8E.GBP": (LayerType.PASTE, Side.BOTTOM, None, "#800000"),
        "MC60A_HV_V8E.GM1": (LayerType.MECHANICAL, Side.NONE, 1, "#BFBF00"),
        "MC60A_HV_V8E-RoundHoles.TXT": (LayerType.DRILL, Side.NONE, None, "#009190"),
        "MC60A_HV_V8E-SlotHoles.TXT": (LayerType.DRILL, Side.NONE, None, "#009190"),
    }
    for name, (t, side, idx, color) in expect.items():
        la = layers[name]
        assert (la.kind.type, la.kind.side, la.kind.index) == (t, side, idx), name
        assert la.color == color, name
        assert la.error is None, name
    assert {la.name for la in artwork_layers(list(layers.values()))} == set(expect)
    reports = [
        "Status Report.Txt",
        "MC60A_HV_V8E.REP",
        "Pick Place for MC60A_HV_V8E.csv",
        "MC60A_HV_V8E.DRR",
        "MC60A_HV_V8E.LDP",
        "MC60A_HV_V8E.EXTREP",
    ]
    for name in reports:
        assert layers[name].kind.type is LayerType.REPORT, name


@needs_testdata
def test_mc60a_default_visibility_is_top_side():
    """Top, drill and mechanical layers start checked; bottom and inner layers start unchecked."""
    shown = {la.name.rsplit("_V8E", 1)[-1] for la in artwork_layers(load_layers(MC60A)) if la.visible}
    assert shown == {".GTL", ".GTO", ".GTS", ".GTP", ".GM1", "-RoundHoles.TXT", "-SlotHoles.TXT"}


@needs_testdata
def test_mc60a_drill_matches_drr_and_copper():
    layers = {la.name: la for la in load_layers(MC60A)}
    holes = [o.converted(MM) for o in layers["MC60A_HV_V8E-RoundHoles.TXT"].parsed.objects]
    slots = [o for o in layers["MC60A_HV_V8E-SlotHoles.TXT"].parsed.objects if isinstance(o, Line)]
    assert len(holes) == 732  # DRR total 736 minus the 4 slots
    assert len(slots) == 4
    npth = [h for h in holes if h.aperture.plated is False]
    assert len(npth) == 5
    assert all(math.isclose(h.aperture.unit.convert_to(MM, h.aperture.diameter), 3.048) for h in npth)
    pads = [
        (o.converted(MM).x, o.converted(MM).y)
        for o in layers["MC60A_HV_V8E.GTL"].parsed.objects
        if isinstance(o, Flash)
    ]
    # Every plated hole sits on a top copper flash: proves the INCH,LZ coordinate format is read right.
    for h in holes:
        if h.aperture.plated:
            assert min(math.hypot(h.x - x, h.y - y) for x, y in pads) < 0.005
