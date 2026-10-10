import pytest
from gerbonara import graphic_primitives as gp
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QTransform

from conftest import MC60A, needs_testdata
from panelcheck.io.gerber_loader import load_layers
from panelcheck.ui.layer_item import RenderList

RED = QColor("#FF0000")
PX_PER_MM = 10
SIZE_MM = 20


def _render(rl: RenderList) -> QImage:
    n = SIZE_MM * PX_PER_MM
    img = QImage(n, n, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # board mm (Y-up, origin bottom-left) -> image pixels
    p.setTransform(QTransform(PX_PER_MM, 0, 0, -PX_PER_MM, 0, n))
    rl.render(p, RED)
    p.end()
    return img


def _alpha(img: QImage, x_mm: float, y_mm: float) -> int:
    return img.pixelColor(round(x_mm * PX_PER_MM), round((SIZE_MM - y_mm) * PX_PER_MM)).alpha()


def test_clear_polarity_erases_only_inside(qapp):
    square = gp.ArcPoly([(2, 2), (18, 2), (18, 18), (2, 18)], polarity_dark=True)
    hole = gp.Circle(10, 10, 3, polarity_dark=False)
    pad_in_hole = gp.Circle(10, 10, 1, polarity_dark=True)  # dark after clear draws again
    img = _render(RenderList.from_primitives([square, hole, pad_in_hole]))
    assert _alpha(img, 5, 5) == 255
    assert _alpha(img, 12, 10) == 0  # cleared ring
    assert _alpha(img, 10, 10) == 255  # pad drawn after the clear
    assert _alpha(img, 1, 1) == 0


def test_overlapping_shapes_union_regardless_of_winding(qapp):
    cw_region = gp.ArcPoly([(2, 2), (2, 12), (12, 12), (12, 2)])  # clockwise
    circle = gp.Circle(12, 12, 4)
    rect = gp.Rectangle(9, 9, 6, 6, 0)
    img = _render(RenderList.from_primitives([cw_region, circle, rect]))
    for pt in [(4, 4), (10, 10), (11, 11), (14, 14), (11, 7)]:
        assert _alpha(img, *pt) == 255, pt


def test_strokes_have_round_caps(qapp):
    img = _render(RenderList.from_primitives([gp.Line(5, 10, 15, 10, 2.0)]))
    assert _alpha(img, 10, 10) == 255
    assert _alpha(img, 4.3, 10) == 255  # inside the round cap
    assert _alpha(img, 10, 11.5) == 0


def test_region_with_arc_edge(qapp):
    # half disc: straight edge along y=5, then a CCW arc about (10,5) back over the top
    poly = gp.ArcPoly([(5, 5), (15, 5)], [None, (False, (10, 5))])
    img = _render(RenderList.from_primitives([poly]))
    assert _alpha(img, 10, 9) == 255
    assert _alpha(img, 10, 4) == 0


def test_negative_plane_inverts(qapp):
    antipad = gp.Circle(10, 10, 2)  # dark in the file = clearance on a negative plane
    img = _render(RenderList.from_primitives([antipad], negative_extent=QRectF(0, 0, 20, 20)))
    assert _alpha(img, 3, 3) == 255
    assert _alpha(img, 10, 10) == 0


def test_bbox(qapp):
    rl = RenderList.from_primitives([gp.Circle(5, 5, 1), gp.Line(0, 0, 10, 0, 2)])
    assert rl.bbox.left() == pytest.approx(-1)
    assert rl.bbox.top() == pytest.approx(-1)
    assert rl.bbox.right() == pytest.approx(11)
    assert rl.bbox.bottom() == pytest.approx(6)
    assert rl.primitive_count == 2


@needs_testdata
def test_mc60a_all_layers_compile(qapp):
    layers = load_layers(MC60A)
    for layer in layers:
        if layer.parsed is None:
            continue
        rl = RenderList.from_layer(layer)
        assert rl.failed_objects == 0, layer.name
        assert rl.primitive_count > 0, layer.name
    gtl = next(la for la in layers if la.name.endswith(".GTL"))
    assert any(not r.dark for r in RenderList.from_layer(gtl).runs), "GTL has LPC clear regions"
