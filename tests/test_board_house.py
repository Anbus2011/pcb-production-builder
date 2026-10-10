"""Board-house (JLC jlccam) panel output: extensionless layer files named tl/bl/l2/…/drl/ko/vcut."""

import pytest

from conftest import TESTDATA
from panelcheck.io.gerber_loader import (
    ID_LAYER_NAME,
    SourceFile,
    artwork_subfolders,
    load_layer,
    load_layers,
)
from panelcheck.model.layers import (
    DRILL,
    LayerGroup,
    LayerKind,
    LayerType,
    Side,
    kind_from_layer_name,
    layer_name_from_comments,
)
from panelcheck.model.snap import SnapIndex

PANEL = TESTDATA / "MC60A_HV_V8D_Y512"
needs_panel = pytest.mark.skipif(
    not (PANEL / "ok").is_dir(), reason="DK's JLC panel files are not in testdata/"
)

JLC_GERBER = """G04 -- output software:jlccam pro v3.2.3 *
G04 -- layer:{layer}*
%FSLAX26Y26*%
%MOIN*%
%ADD10C,0.010000*%
G75*
G90*
G54D10*
%LPD*%
D10*
X1000000Y1000000D03*
M02*
"""


@pytest.mark.parametrize(
    "name, kind",
    [
        ("tl", LayerKind(LayerType.COPPER, Side.TOP)),
        ("bl", LayerKind(LayerType.COPPER, Side.BOTTOM)),
        ("l1", LayerKind(LayerType.COPPER, Side.TOP)),
        ("l2", LayerKind(LayerType.COPPER, Side.INNER, 1)),
        ("l3", LayerKind(LayerType.COPPER, Side.INNER, 2)),
        ("to", LayerKind(LayerType.SILK, Side.TOP)),
        ("bo", LayerKind(LayerType.SILK, Side.BOTTOM)),
        ("ts", LayerKind(LayerType.MASK, Side.TOP)),
        ("bs", LayerKind(LayerType.MASK, Side.BOTTOM)),
        ("gtp", LayerKind(LayerType.PASTE, Side.TOP)),
        ("drl", DRILL),
        ("ko", LayerKind(LayerType.OUTLINE)),
        ("vcut", LayerKind(LayerType.VCUT)),
        ("sk", LayerKind(LayerType.MISC_DRILL)),
        ("TL", LayerKind(LayerType.COPPER, Side.TOP)),
        ("readme", None),
    ],
)
def test_kind_from_layer_name(name, kind):
    assert kind_from_layer_name(name) == kind


def test_layer_name_from_jlc_header():
    assert layer_name_from_comments([" -- output software:jlccam pro v3.2.3 ", " -- layer:bs"]) == "bs"
    assert layer_name_from_comments(["Layer_Color=255"]) is None


def test_header_name_wins_over_renamed_file():
    la = load_layer(SourceFile("panel_top_copper_v2", JLC_GERBER.format(layer="tl").encode()))
    assert la.kind == LayerKind(LayerType.COPPER, Side.TOP)
    assert la.id_source == ID_LAYER_NAME


def test_file_name_used_when_header_has_no_layer_name():
    data = JLC_GERBER.replace("G04 -- layer:{layer}*\n", "").encode()
    assert load_layer(SourceFile("vcut", data)).kind.type is LayerType.VCUT
    assert load_layer(SourceFile("bs.gbr", data)).kind == LayerKind(LayerType.MASK, Side.BOTTOM)


def test_jlc_gerber_drill_and_misc_drill():
    drl = load_layer(SourceFile("drl", JLC_GERBER.format(layer="drl").encode()))
    assert drl.kind.type is LayerType.DRILL and drl.visible
    sk = load_layer(SourceFile("sk", JLC_GERBER.format(layer="sk").encode()))
    assert sk.kind.type is LayerType.MISC_DRILL and not sk.visible
    assert sk.kind.group is LayerGroup.DRILL


def test_benign_parser_warnings_are_dropped():
    la = load_layer(SourceFile("tl", JLC_GERBER.format(layer="tl").encode()))
    assert not any("G90" in w for w in la.warnings)


def test_artwork_subfolder_discovery(tmp_path):
    (tmp_path / "ok").mkdir()
    (tmp_path / "ok" / "tl").write_text(JLC_GERBER.format(layer="tl"))
    (tmp_path / "YG").mkdir()
    (tmp_path / "YG" / "order.json").write_text("{}")
    assert artwork_subfolders(tmp_path) == [tmp_path / "ok"]


@needs_panel
def test_mc60a_panel_identification():
    layers = {la.name: la for la in load_layers(PANEL / "ok")}
    expect = {
        "tl": LayerKind(LayerType.COPPER, Side.TOP),
        "l2": LayerKind(LayerType.COPPER, Side.INNER, 1),
        "l3": LayerKind(LayerType.COPPER, Side.INNER, 2),
        "bl": LayerKind(LayerType.COPPER, Side.BOTTOM),
        "to": LayerKind(LayerType.SILK, Side.TOP),
        "bo": LayerKind(LayerType.SILK, Side.BOTTOM),
        "ts": LayerKind(LayerType.MASK, Side.TOP),
        "bs": LayerKind(LayerType.MASK, Side.BOTTOM),
        "drl": DRILL,
        "ko": LayerKind(LayerType.OUTLINE),
        "vcut": LayerKind(LayerType.VCUT),
        "sk": LayerKind(LayerType.MISC_DRILL),
    }
    for name, kind in expect.items():
        assert layers[name].kind == kind, name
        assert layers[name].error is None, name
    for name in ("2267606a_y512.tgz", "2267606a_y512.ddw"):
        assert layers[name].kind.type is LayerType.REPORT
    assert artwork_subfolders(PANEL) == [PANEL / "ok"]


@needs_panel
def test_mc60a_panel_snaps_to_holes_on_both_boards():
    layers = [la for la in load_layers(PANEL / "ok") if la.parsed is not None]
    idx = SnapIndex.from_layers(layers)
    # Altium NPTH at (76.835, 18.034); the ODB++ step-repeat puts board 1's outline corner at (5, 5) mm
    # and board 2's at (90.979, 5), and the Altium outline corner is at (-3.683, -3.302).
    for board_x in (5.0, 90.979):
        x, y = 76.835 + 3.683 + board_x, 18.034 + 3.302 + 5.0
        t = idx.nearest(x + 0.2, y - 0.2, 1.0)
        assert t is not None and t.kind == "hole", board_x
        assert (t.x, t.y) == pytest.approx((x, y), abs=0.01)
