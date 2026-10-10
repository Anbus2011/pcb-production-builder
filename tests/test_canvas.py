import pytest
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QMouseEvent

from panelcheck.ui.canvas import Canvas


@pytest.fixture
def canvas(qapp):
    c = Canvas()
    c.resize(800, 600)
    c.set_content_rect(QRectF(0, 0, 100, 80))
    c.show()
    qapp.processEvents()
    c.zoom_to_fit()
    yield c
    c.close()


def test_y_up_and_bottom_view_mirrors_x(canvas):
    a, b = canvas.mapFromScene(QPointF(10, 10)), canvas.mapFromScene(QPointF(20, 20))
    assert b.x() > a.x() and b.y() < a.y()  # +Y is up on screen
    canvas.set_mirrored(True)
    a, b = canvas.mapFromScene(QPointF(10, 10)), canvas.mapFromScene(QPointF(20, 20))
    assert b.x() < a.x() and b.y() < a.y()  # X mirrored, Y still up


def test_zoom_keeps_point_under_cursor(canvas):
    pos = QPointF(200, 150)
    before = canvas.mapToScene(pos.toPoint())
    canvas.zoom_at(3.0, pos)
    after = canvas.mapToScene(pos.toPoint())
    assert after.x() == pytest.approx(before.x(), abs=2 / canvas.px_per_mm)
    assert after.y() == pytest.approx(before.y(), abs=2 / canvas.px_per_mm)


def _mouse(kind, pos, button, buttons):
    p = QPointF(*pos)
    return QMouseEvent(kind, p, p, button, buttons, Qt.KeyboardModifier.NoModifier)


@pytest.mark.parametrize("button", [Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton])
def test_drag_pans_board_with_mouse(canvas, button):
    target = QPointF(50, 40)
    before = canvas.mapFromScene(target)
    canvas.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, (300, 300), button, button))
    canvas.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, (360, 270), Qt.MouseButton.NoButton, button))
    canvas.mouseReleaseEvent(
        _mouse(QEvent.Type.MouseButtonRelease, (360, 270), button, Qt.MouseButton.NoButton)
    )
    after = canvas.mapFromScene(target)
    assert (after.x() - before.x(), after.y() - before.y()) == (60, -30)  # board follows the mouse
    # After release, moving without a button no longer pans.
    canvas.mouseMoveEvent(
        _mouse(QEvent.Type.MouseMove, (500, 500), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton)
    )
    assert canvas.mapFromScene(target) == after


def test_releasing_other_button_does_not_end_pan(canvas):
    right, left = Qt.MouseButton.RightButton, Qt.MouseButton.LeftButton
    canvas.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, (300, 300), right, right))
    canvas.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, (300, 300), left, right))
    before = canvas.mapFromScene(QPointF(50, 40))
    canvas.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, (320, 300), Qt.MouseButton.NoButton, right))
    assert canvas.mapFromScene(QPointF(50, 40)).x() - before.x() == 20


def test_shift_hover_snaps_and_click_sets_home(canvas):
    from panelcheck.model.layers import Layer, LayerKind, LayerType, Side
    from panelcheck.model.snap import SnapIndex, SnapTarget

    layer = Layer("a.GTL", LayerKind(LayerType.COPPER, Side.TOP), "test", "#FF0000")
    pad = SnapTarget(30.0, 20.0, "pad", layer, 1.0, 1.0)
    idx = SnapIndex([pad])
    canvas.set_snap_provider(lambda x, y, r: idx.nearest(x, y, r))
    picked, moved = [], []
    canvas.homePicked.connect(lambda x, y, t: picked.append((x, y, t)))
    canvas.cursorMoved.connect(lambda x, y: moved.append((x, y)))

    near = canvas.mapFromScene(QPointF(30.3, 20.2))  # a few px off the pad centre
    pos = (near.x(), near.y())
    none, left = Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton

    # Without Shift: no snap, and a click only produces a hint.
    canvas.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, pos, none, none))
    assert canvas.snap_target() is None
    assert moved[-1] != (30.0, 20.0)
    canvas.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, pos, left, left))
    assert picked == []

    # With Shift: snapped, reported coordinates are the pad centre, click picks it.
    shift = Qt.KeyboardModifier.ShiftModifier
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(*pos), QPointF(*pos), none, none, shift)
    canvas.mouseMoveEvent(move)
    assert canvas.snap_target() is pad
    assert moved[-1] == (30.0, 20.0)
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(*pos), QPointF(*pos), left, left, shift)
    canvas.mousePressEvent(press)
    assert picked == [(30.0, 20.0, pad)]

    # Too far away (beyond the ~12 px snap radius): no snap.
    far = canvas.mapFromScene(QPointF(30.0 + 40 / canvas.px_per_mm, 20.0))
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(far), QPointF(far), none, none, shift)
    canvas.mouseMoveEvent(move)
    assert canvas.snap_target() is None


def test_zoom_to_fit_centres_content(canvas):
    centre = canvas.mapToScene(canvas.viewport().rect().center())
    assert centre.x() == pytest.approx(50, abs=1)
    assert centre.y() == pytest.approx(40, abs=1)
