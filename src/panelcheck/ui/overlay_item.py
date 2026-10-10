"""Pick-and-place overlay: a fixed-size "+" at every placed part centroid, in the side color, plus
the part's footprint from ODB++ (outline, pin-1 dot, optional pad outlines) once zoomed in.

Markers are drawn in viewport pixels (constant size at any zoom) at positions mapped through the
view transform, so bottom view mirrors their placement but not their labels. Footprints are
QPainterPaths in scene mm drawn with cosmetic pens. The near side is drawn last (on top).
Parts that failed the ODB++ cross-check get a red ring at every zoom.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem

from panelcheck.model.layers import BOTTOM_MARKER_COLOR, TOP_MARKER_COLOR, Side
from panelcheck.model.placement import PlacedPart, Polygon

MARKER_ARM_PX = 6
LABEL_MIN_PX_PER_MM = 25.0  # an 0603 is ~40 px long here, room for its designator
FOOTPRINT_MIN_PX_PER_MM = 6.0  # an 0603's pads span ~13 px here
PIN1_DOT_PX = 3.0
HALO_COLOR = QColor(0, 0, 0, 200)
HIGHLIGHT_COLOR = QColor("#FFFFFF")
FLAG_COLOR = QColor("#FF3030")


def _path(polys) -> QPainterPath:
    path = QPainterPath()
    for poly in polys:
        path.addPolygon(QPolygonF([QPointF(x, y) for x, y in poly]))
        path.closeSubpath()
    return path


class _SidePaths:
    def __init__(self, placed: list[PlacedPart]):
        fps = [p.footprint for p in placed if p.footprint is not None]
        body: list[Polygon] = [poly for fp in fps if fp.outline_is_body for poly in fp.outline]
        padbox: list[Polygon] = [poly for fp in fps if not fp.outline_is_body for poly in fp.outline]
        self.body = _path(body)
        self.padbox = _path(padbox)
        self.pads = _path([poly for fp in fps for poly in fp.pads])
        self.pin1 = [fp.pin1 for fp in fps if fp.pin1 is not None]

    def bounds(self) -> QRectF:
        return self.body.boundingRect().united(self.padbox.boundingRect()).united(self.pads.boundingRect())


class PlacementOverlay(QGraphicsItem):
    def __init__(self):
        super().__init__()
        self._placed: list[PlacedPart] = []
        self._paths: dict[Side, _SidePaths] = {}
        self._flagged: set[str] = set()
        self._bounds = QRectF()
        self._colors = {Side.TOP: TOP_MARKER_COLOR, Side.BOTTOM: BOTTOM_MARKER_COLOR}
        self._shown = {Side.TOP: True, Side.BOTTOM: True}
        self._pads_shown = True
        self._near = Side.TOP  # the side being viewed: drawn last, and the only one with footprints
        self._highlight: PlacedPart | None = None
        self.setZValue(10)  # above the board

    # ---- content -----------------------------------------------------------------------------

    def set_placed(self, placed: list[PlacedPart], flagged: set[str] | None = None) -> None:
        """``flagged``: designators that failed the ODB++ cross-check (drawn with a red ring)."""
        self.prepareGeometryChange()
        self._placed = list(placed)
        self._flagged = set(flagged or ())
        self._highlight = None
        self._paths = {s: _SidePaths([p for p in self._placed if p.part.side is s]) for s in self._colors}
        if self._placed:
            xs = [p.x for p in self._placed]
            ys = [p.y for p in self._placed]
            box = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
            for sp in self._paths.values():
                box = box.united(sp.bounds())
            self._bounds = box.adjusted(-1, -1, 1, 1)
        else:
            self._bounds = QRectF()
        self.update()

    def placed(self) -> list[PlacedPart]:
        return list(self._placed)

    def has_footprints(self) -> bool:
        return any(p.footprint is not None for p in self._placed)

    def color(self, side: Side) -> str:
        return self._colors[side]

    def set_color(self, side: Side, color: str) -> None:
        self._colors[side] = color
        self.update()

    def side_shown(self, side: Side) -> bool:
        return self._shown[side]

    def set_side_shown(self, side: Side, shown: bool) -> None:
        self._shown[side] = shown
        if self._highlight is not None and self._highlight.part.side is side and not shown:
            self._highlight = None
        self.update()

    def set_near_side(self, side: Side) -> None:
        self._near = side
        self.update()

    def pads_shown(self) -> bool:
        return self._pads_shown

    def set_pads_shown(self, shown: bool) -> None:
        self._pads_shown = shown
        self.update()

    def set_highlight(self, placed: PlacedPart | None) -> None:
        if placed is not self._highlight:
            self._highlight = placed
            self.update()

    def nearest(self, x: float, y: float, radius: float) -> PlacedPart | None:
        """Nearest shown marker within ``radius`` mm."""
        best, best_d = None, radius
        for p in self._placed:
            if self._shown[p.part.side]:
                d = math.hypot(p.x - x, p.y - y)
                if d <= best_d:
                    best, best_d = p, d
        return best

    def boundingRect(self) -> QRectF:
        return self._bounds

    # ---- painting ----------------------------------------------------------------------------

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if not self._placed:
            return
        t = painter.worldTransform()
        px_per_mm = abs(t.m22())
        near = self._near
        far = Side.BOTTOM if near is Side.TOP else Side.TOP
        sides = [s for s in (far, near) if self._shown[s]]
        # Footprints only for the side being viewed; the far side's pads seen through the board
        # are easily mistaken for near-side ones. The far side keeps its "+" markers.
        footprints = px_per_mm >= FOOTPRINT_MIN_PX_PER_MM and self._shown[near]

        painter.save()
        if footprints:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)  # crisp 1-px lines
            painter.setBrush(Qt.BrushStyle.NoBrush)
            self._paint_footprints(painter, near)

        painter.resetTransform()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        view = QRectF(painter.window()).adjusted(-40, -40, 40, 40)
        labels = px_per_mm >= LABEL_MIN_PX_PER_MM
        font = QFont(painter.font())
        font.setPointSizeF(8)
        painter.setFont(font)
        a = MARKER_ARM_PX
        for side in sides:
            color = QColor(self._colors[side])
            if footprints and side is near:
                self._paint_pin1(painter, t, view, side, color)
            pts = []
            for p in self._placed:
                if p.part.side is side:
                    c = t.map(QPointF(p.x, p.y))
                    if view.contains(c):
                        pts.append((math.floor(c.x()) + 0.5, math.floor(c.y()) + 0.5, p))
            if not pts:
                continue
            lines = []
            for x, y, _ in pts:
                lines.append(QLineF(x - a, y, x + a, y))
                lines.append(QLineF(x, y - a, x, y + a))
            painter.setPen(QPen(HALO_COLOR, 3))
            painter.drawLines(lines)
            painter.setPen(QPen(color, 1))
            painter.drawLines(lines)
            if labels:
                for x, y, p in pts:
                    _label(painter, QPointF(x + a + 2, y - a), p.part.designator, color)
            flagged = [(x, y) for x, y, p in pts if p.part.designator in self._flagged]
            if flagged:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(QPen(FLAG_COLOR, 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                for x, y in flagged:
                    painter.drawEllipse(QPointF(x, y), a + 4, a + 4)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if self._highlight is not None:
            c = t.map(QPointF(self._highlight.x, self._highlight.y))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(HIGHLIGHT_COLOR, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(c, a + 3, a + 3)
            _label(painter, QPointF(c.x() + a + 4, c.y() - a - 2), self._highlight.part.designator,
                   HIGHLIGHT_COLOR)  # fmt: skip
        painter.restore()

    def _paint_footprints(self, painter: QPainter, side: Side) -> None:
        """Pad outlines (when shown) or else the dashed pads' box, plus any real body contour, in
        scene mm with cosmetic (pixel-width) pens. The pads' box only stands in for hidden pads:
        on big parts (heatsink, connectors) it spans the board and is just clutter next to them."""
        paths = self._paths.get(side)
        if paths is None:
            return
        color = QColor(self._colors[side])

        def pen(c: QColor, width: float, style=Qt.PenStyle.SolidLine) -> QPen:
            p = QPen(c, width, style)
            p.setCosmetic(True)
            return p

        outlines = [(paths.body, Qt.PenStyle.SolidLine)]
        if self._pads_shown and not paths.pads.isEmpty():
            pad_color = QColor(color)
            pad_color.setAlpha(220)
            painter.setPen(pen(pad_color, 1))
            painter.drawPath(paths.pads)
        else:
            outlines.insert(0, (paths.padbox, Qt.PenStyle.DashLine))
        for path, style in outlines:
            if path.isEmpty():
                continue
            painter.setPen(pen(HALO_COLOR, 3))
            painter.drawPath(path)
            painter.setPen(pen(color, 1, style))
            painter.drawPath(path)

    def _paint_pin1(self, painter: QPainter, t, view: QRectF, side: Side, color: QColor) -> None:
        paths = self._paths.get(side)
        if paths is None or not paths.pin1:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(HALO_COLOR, 1.5))
        painter.setBrush(color)
        for x, y in paths.pin1:
            c = t.map(QPointF(x, y))
            if view.contains(c):
                painter.drawEllipse(c, PIN1_DOT_PX, PIN1_DOT_PX)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)


def _label(painter: QPainter, pos: QPointF, text: str, color: QColor) -> None:
    painter.setPen(HALO_COLOR)
    painter.drawText(pos + QPointF(1, 1), text)
    painter.setPen(color)
    painter.drawText(pos, text)
