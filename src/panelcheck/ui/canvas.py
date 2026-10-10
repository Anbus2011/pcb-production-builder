"""Canvas: QGraphicsView with cursor-anchored zoom, drag-pan, zoom-to-fit, bottom view, a full-view
crosshair cursor, Shift-snapping to pads/holes, and the Home (0,0) marker.

Scene coordinates are board mm, Y-up. The view transform (from ``view_scale_factors``) is the only
place the Y axis is flipped, and the only place the bottom-view X mirror happens.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QMouseEvent, QPainter, QPen, QTransform, QWheelEvent
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsScene, QGraphicsView

from panelcheck.model.geometry import view_scale_factors
from panelcheck.model.layers import BACKGROUND_COLOR
from panelcheck.model.snap import SnapTarget

MIN_PX_PER_MM = 0.05
MAX_PX_PER_MM = 5000.0
WHEEL_STEP = 1.25
SETTLE_MS = 160
SNAP_RADIUS_PX = 12

CURSOR_ARM_PX = 10  # half-length of the drawn "+"; the crosshair lines start where the arms end
CURSOR_COLOR = QColor("#FFFFFF")
CROSSHAIR_COLOR = QColor("#00FF00")
SNAP_COLOR = QColor("#00FF00")
HOME_COLOR = QColor("#FFFFFF")

SnapProvider = Callable[[float, float, float], SnapTarget | None]  # (x mm, y mm, radius mm)


class Canvas(QGraphicsView):
    cursorMoved = Signal(float, float)  # board mm (the snapped point while snapped)
    cursorLeft = Signal()
    snapChanged = Signal(object)  # SnapTarget | None
    homePicked = Signal(float, float, object)  # board mm, SnapTarget
    hint = Signal(str)
    viewChanged = Signal()  # zoom or side changed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QBrush(QColor(BACKGROUND_COLOR)))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

        self.px_per_mm = 4.0
        self.mirrored = False  # X mirror of the display (bottom view); stored coordinates never change
        self._content = QRectF()
        self._pan_last: QPoint | None = None
        self._pan_button: Qt.MouseButton | None = None
        self._space_down = False
        self._shift_down = False
        self._cursor_vp: QPointF | None = None  # mouse position in viewport pixels
        self._snap: SnapTarget | None = None
        self._snap_provider: SnapProvider | None = None
        self._home: QPointF | None = None
        self._interacting = False
        self._settle = QTimer(self, singleShot=True, interval=SETTLE_MS)
        self._settle.timeout.connect(self._on_settled)
        self._prefetch_items: list = []
        self._prefetch = QTimer(self, interval=1)
        self._prefetch.timeout.connect(self._prefetch_tick)
        self._apply_transform()
        # Shift must work even when the layer tree has keyboard focus.
        if (app := QApplication.instance()) is not None:
            app.installEventFilter(self)

    # ---- state -------------------------------------------------------------------------------

    def is_interacting(self) -> bool:
        return self._interacting

    def schedule_prefetch(self, item) -> None:
        """Let ``item`` render off-screen tiles in small time slices while the UI is idle."""
        if item not in self._prefetch_items:
            self._prefetch_items.append(item)
        if not self._prefetch.isActive():
            self._prefetch.start()

    def _prefetch_tick(self) -> None:
        if self._interacting or self._pan_last is not None:
            return  # stay out of the way while the user is moving the view
        self._prefetch_items = [it for it in self._prefetch_items if it.prefetch_step()]
        if not self._prefetch_items:
            self._prefetch.stop()

    def set_content_rect(self, rect: QRectF) -> None:
        """Board/panel extent in mm. The scene rect is made much larger so panning is never clamped."""
        self._content = QRectF(rect)
        size = max(rect.width(), rect.height(), 50.0)
        self.setSceneRect(rect.adjusted(-20 * size, -20 * size, 20 * size, 20 * size))

    def set_snap_provider(self, provider: SnapProvider | None) -> None:
        self._snap_provider = provider
        self._refresh_cursor()

    def set_home(self, point: QPointF | None) -> None:
        self._home = QPointF(point) if point is not None else None
        self.viewport().update()

    def home(self) -> QPointF | None:
        return QPointF(self._home) if self._home is not None else None

    def snap_target(self) -> SnapTarget | None:
        return self._snap

    def _apply_transform(self) -> None:
        sx, sy = view_scale_factors(self.px_per_mm, self.mirrored)
        self.setTransform(QTransform.fromScale(sx, sy))

    def _on_settled(self) -> None:
        self._interacting = False
        self.viewport().update()

    def _mark_interacting(self) -> None:
        self._interacting = True
        self._settle.start()

    # ---- zoom / view -------------------------------------------------------------------------

    def zoom_at(self, factor: float, viewport_pos: QPointF) -> None:
        new = min(MAX_PX_PER_MM, max(MIN_PX_PER_MM, self.px_per_mm * factor))
        if new == self.px_per_mm:
            return
        anchor = self.mapToScene(viewport_pos.toPoint())
        self.px_per_mm = new
        self._apply_transform()
        self._keep_scene_point_at(anchor, viewport_pos)
        self.viewChanged.emit()
        self._refresh_cursor()

    def _keep_scene_point_at(self, scene_pt: QPointF, viewport_pos: QPointF) -> None:
        now = self.mapFromScene(scene_pt)
        delta = now - viewport_pos.toPoint()
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + delta.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + delta.y())

    def zoom_to_fit(self) -> None:
        r = self._content
        if r.isNull() or r.isEmpty():
            return
        vw, vh = max(self.viewport().width(), 1), max(self.viewport().height(), 1)
        self.px_per_mm = min(vw / r.width(), vh / r.height()) * 0.92
        self._apply_transform()
        self.centerOn(r.center())
        self.viewport().update()
        self.viewChanged.emit()
        self._refresh_cursor()

    def set_mirrored(self, mirrored: bool) -> None:
        if mirrored == self.mirrored:
            return
        center = self.mapToScene(self.viewport().rect().center())
        self.mirrored = mirrored
        self._apply_transform()
        self.centerOn(center)
        self.viewChanged.emit()
        self._refresh_cursor()

    # ---- cursor and snapping -----------------------------------------------------------------

    def _scene_at(self, vp: QPointF) -> QPointF:
        return self.viewportTransform().inverted()[0].map(vp)

    def _vp_at(self, scene: QPointF) -> QPointF:
        return self.viewportTransform().map(scene)

    def _refresh_cursor(self) -> None:
        """Recompute the snap target and report the cursor position after any change."""
        snap = None
        if self._cursor_vp is not None:
            p = self._scene_at(self._cursor_vp)
            if self._shift_down and self._pan_last is None and self._snap_provider is not None:
                snap = self._snap_provider(p.x(), p.y(), SNAP_RADIUS_PX / self.px_per_mm)
            if snap is not None:
                self.cursorMoved.emit(snap.x, snap.y)
            else:
                self.cursorMoved.emit(p.x(), p.y())
        if snap is not self._snap:
            self._snap = snap
            self.snapChanged.emit(snap)
        self.viewport().update()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and event.key() == Qt.Key.Key_Shift:
            down = event.type() == QEvent.Type.KeyPress
            if down != self._shift_down:
                self._shift_down = down
                self._refresh_cursor()
        elif event.type() == QEvent.Type.ApplicationDeactivate and self._shift_down:
            self._shift_down = False  # the key release goes to another app
            self._refresh_cursor()
        return False

    def _crosshair_visible(self) -> bool:
        return self._cursor_vp is not None and self._pan_last is None and not self._space_down

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        painter.save()
        painter.resetTransform()
        if self._home is not None:
            self._draw_home(painter, self._vp_at(self._home))
        if self._crosshair_visible():
            if self._snap is not None:
                c = self._vp_at(QPointF(self._snap.x, self._snap.y))
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(QPen(SNAP_COLOR, 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(c, CURSOR_ARM_PX - 2, CURSOR_ARM_PX - 2)
            else:
                c = self._cursor_vp
            self._draw_crosshair(painter, c)
        painter.restore()

    def _draw_crosshair(self, painter: QPainter, c: QPointF) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.viewport().width(), self.viewport().height()
        x, y = math.floor(c.x()) + 0.5, math.floor(c.y()) + 0.5
        a = CURSOR_ARM_PX
        pen = QPen(CROSSHAIR_COLOR, 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawLine(QPointF(0, y), QPointF(x - a, y))
        painter.drawLine(QPointF(x + a, y), QPointF(w, y))
        painter.drawLine(QPointF(x, 0), QPointF(x, y - a))
        painter.drawLine(QPointF(x, y + a), QPointF(x, h))
        painter.setPen(QPen(CURSOR_COLOR, 2))
        painter.drawLine(QPointF(x - a, y), QPointF(x + a, y))
        painter.drawLine(QPointF(x, y - a), QPointF(x, y + a))

    def _draw_home(self, painter: QPainter, c: QPointF) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(HOME_COLOR, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(c, 9, 9)
        painter.drawLine(QPointF(c.x() - 15, c.y()), QPointF(c.x() + 15, c.y()))
        painter.drawLine(QPointF(c.x(), c.y() - 15), QPointF(c.x(), c.y() + 15))
        painter.setBrush(HOME_COLOR)
        painter.drawEllipse(c, 2.5, 2.5)
        font = QFont(painter.font())
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QPointF(c.x() + 13, c.y() - 12), "HOME 0,0")

    # ---- events ------------------------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:
        steps = event.angleDelta().y() / 120.0
        if steps == 0:
            return
        self._mark_interacting()
        self.zoom_at(WHEEL_STEP**steps, event.position())
        event.accept()

    def _start_pan(self, event: QMouseEvent) -> bool:
        """Pan with right-drag, middle-drag, or Space + left-drag."""
        button = event.button()
        if button in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton) or (
            button == Qt.MouseButton.LeftButton and self._space_down
        ):
            self._pan_last = event.position().toPoint()
            self._pan_button = button
            self._update_cursor()
            self._refresh_cursor()
            return True
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._start_pan(event):
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self._snap is not None:
                self.homePicked.emit(self._snap.x, self._snap.y, self._snap)
            else:
                self.hint.emit(
                    "Hold Shift over a pad or hole to snap to it, then left-click to set Home (0,0)."
                )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._pan_last is not None:
            d = pos.toPoint() - self._pan_last
            self._pan_last = pos.toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - d.y())
        self._cursor_vp = QPointF(pos)
        self._shift_down = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self._refresh_cursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._pan_last is not None and event.button() == self._pan_button:
            self._pan_last = None
            self._pan_button = None
            self._update_cursor()
            self._refresh_cursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_down = True
            self._update_cursor()
            self.viewport().update()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_down = False
            self._update_cursor()
            self.viewport().update()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self._cursor_vp = None
        self._refresh_cursor()
        self.cursorLeft.emit()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.viewport().update()

    def _update_cursor(self) -> None:
        if self._pan_last is not None:
            shape = Qt.CursorShape.ClosedHandCursor
        elif self._space_down:
            shape = Qt.CursorShape.OpenHandCursor
        else:
            shape = Qt.CursorShape.BlankCursor  # the "+" and crosshair are drawn in drawForeground
        self.viewport().setCursor(shape)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_cursor()
