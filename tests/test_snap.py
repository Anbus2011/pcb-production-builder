import math

import pytest

from conftest import MC60A, needs_testdata
from panelcheck.io.gerber_loader import SourceFile, load_layer, load_layers
from panelcheck.model.layers import Layer, LayerKind, LayerType, Side
from panelcheck.model.snap import SnapIndex, SnapTarget, targets_from_layer

GERBER = """%FSLAX25Y25*%
%MOMM*%
%ADD10R,1.00000X0.50000*%
%ADD11C,0.30000*%
D10*
X1000000Y1000000D03*
X1200000Y1000000D03*
D11*
X0Y0D02*
X500000Y0D01*
%LPC*%
D10*
X3000000Y3000000D03*
M02*
"""

EXCELLON = """M48
METRIC,LZ
;TYPE=PLATED
T1C0.800
;TYPE=NON_PLATED
T2C3.000
%
T01
X10.000Y10.000
T02
X20.000Y20.000
M30
"""


def _layer(name, kind):
    return Layer(name, kind, "test", "#FFFFFF")


def _t(x, y, kind="pad", layer=None):
    layer = layer or _layer("a.GTL", LayerKind(LayerType.COPPER, Side.TOP))
    return SnapTarget(x, y, kind, layer, 1.0, 1.0)


def test_targets_from_gerber_flashes_only():
    la = load_layer(SourceFile("b.GTL", GERBER.encode()))
    ts = targets_from_layer(la)
    # two dark flashes; the trace (D01) and the clear-polarity flash are not targets
    assert sorted((round(t.x, 3), round(t.y, 3)) for t in ts) == [(10.0, 10.0), (12.0, 10.0)]
    assert all(t.kind == "pad" for t in ts)
    assert ts[0].width == pytest.approx(1.0) and ts[0].height == pytest.approx(0.5)


def test_targets_from_excellon_with_plating():
    la = load_layer(SourceFile("b.TXT", EXCELLON.encode()))
    ts = {(round(t.x), round(t.y)): t for t in targets_from_layer(la)}
    assert ts[(10, 10)].kind == "hole" and ts[(10, 10)].plated is True
    assert ts[(20, 20)].plated is False and ts[(20, 20)].width == pytest.approx(3.0)
    assert "NPTH" in ts[(20, 20)].describe()


def test_nearest_within_radius():
    idx = SnapIndex([_t(0, 0), _t(5, 0), _t(5.3, 0)])
    assert idx.nearest(4.9, 0.1, 1.0).x == 5
    assert idx.nearest(5.2, 0, 1.0).x == 5.3
    assert idx.nearest(2.5, 0, 1.0) is None
    assert idx.nearest(2.5, 0, 3.0).x in (0, 5)  # radius spanning several grid cells


def test_hole_preferred_over_coincident_pad():
    drill = _layer("d.TXT", LayerKind(LayerType.DRILL))
    pad, hole = _t(1, 1), _t(1, 1, "hole", drill)
    assert SnapIndex([pad, hole]).nearest(1.2, 1.2, 1).kind == "hole"


def test_hidden_layers_and_silk_are_ignored():
    hidden = _layer("h.GTL", LayerKind(LayerType.COPPER, Side.TOP))
    hidden.visible = False
    silk = _layer("s.GTO", LayerKind(LayerType.SILK, Side.TOP))
    idx = SnapIndex([_t(0, 0, layer=hidden), _t(0, 0, layer=silk)])
    assert idx.nearest(0, 0, 1) is None
    hidden.visible = True
    assert idx.nearest(0, 0, 1).layer is hidden


def test_near_side_preferred_then_falls_back():
    top = _layer("a.GTL", LayerKind(LayerType.COPPER, Side.TOP))
    bot = _layer("a.GBL", LayerKind(LayerType.COPPER, Side.BOTTOM))
    drill = _layer("d.TXT", LayerKind(LayerType.DRILL))
    idx = SnapIndex(
        [_t(0.0, 0, layer=top), _t(0.5, 0, layer=bot), _t(5, 0, layer=bot), _t(9, 0, "hole", drill)]
    )
    assert idx.nearest(0.45, 0, 1).layer is bot  # plain nearest
    assert idx.nearest(0.45, 0, 1, near_side=Side.TOP).layer is top  # top view: top pad wins
    assert idx.nearest(0.05, 0, 1, near_side=Side.BOTTOM).layer is bot  # bottom view: bottom pad wins
    assert idx.nearest(5.1, 0, 1, near_side=Side.TOP).layer is bot  # nothing on top in range: fall back
    assert idx.nearest(9.1, 0, 1, near_side=Side.BOTTOM).kind == "hole"  # holes belong to both sides


@needs_testdata
def test_mc60a_snaps_to_real_pads_and_holes():
    layers = [la for la in load_layers(MC60A) if la.parsed is not None]
    for la in layers:
        la.visible = True  # bottom layers start hidden, and snapping only targets visible layers
    idx = SnapIndex.from_layers(layers)
    assert idx.count > 2000
    # NPTH mounting hole at (76.835, 18.034) from the drill file
    t = idx.nearest(76.6, 18.2, 1.0)
    assert t.kind == "hole" and t.plated is False
    assert (t.x, t.y) == pytest.approx((76.835, 18.034), abs=0.001)
    # U4 (SOT23-5, bottom) pin at X 19.90 Y 11.43 on GBP/GBL
    t = idx.nearest(19.8, 11.5, 0.5)
    assert t.kind == "pad" and math.isclose(t.y, 11.43, abs_tol=0.01)
