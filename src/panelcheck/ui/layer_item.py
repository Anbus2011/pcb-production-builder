"""Rendering of Gerber/Excellon layers.

Each layer is compiled once into a :class:`RenderList`: consecutive primitives of the same
polarity form a *run*; each run is split into spatial chunks so off-screen geometry is skipped.
A layer renders into its own transparent buffer: dark runs paint the layer color, clear runs
erase with ``CompositionMode_Clear``. The buffer is then composited with the layer opacity.

:class:`BoardItem` composites all layers into cached screen tiles, so panning only renders the
newly exposed tiles, and idle time prefetches a ring of tiles around the view.

Coordinates are board mm, Y-up; the view transform does the flip.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from gerbonara import graphic_primitives as gp
from gerbonara.utils import MM
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsItem

from panelcheck.model.geometry import arc_points, signed_area
from panelcheck.model.layers import Layer, z_order

CHUNK_MM = 10.0
ARC_MAX_ERROR_MM = 0.002
TILE_PX = 256  # logical pixels per cache tile
TILE_KEEP = 3  # tiles kept beyond the visible range before eviction
PREFETCH_RING = 1  # tiles rendered ahead around the view when idle


@dataclass
class Chunk:
    bbox: QRectF
    fill: QPainterPath
    strokes: dict[float, QPainterPath]  # line width (mm) -> centre-line path, round caps/joins


@dataclass
class Run:
    dark: bool
    chunks: list[Chunk]


class _ChunkBuilder:
    __slots__ = ("x0", "y0", "x1", "y1", "polys", "circles", "strokes")

    def __init__(self):
        self.x0 = self.y0 = math.inf
        self.x1 = self.y1 = -math.inf
        self.polys: list[list[tuple[float, float]]] = []
        self.circles: list[tuple[float, float, float]] = []
        self.strokes: dict[float, list[list[tuple[float, float]]]] = {}

    def grow(self, x0, y0, x1, y1):
        self.x0, self.y0 = min(self.x0, x0), min(self.y0, y0)
        self.x1, self.y1 = max(self.x1, x1), max(self.y1, y1)

    def build(self) -> Chunk:
        fill = QPainterPath()
        fill.setFillRule(Qt.FillRule.WindingFill)
        # All sub-paths wind the same way (positive area), so the winding fill is their union.
        for pts in self.polys:
            fill.addPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
            fill.closeSubpath()
        for x, y, r in self.circles:
            fill.addEllipse(QPointF(x, y), r, r)
        strokes = {}
        for w, lines in self.strokes.items():
            p = QPainterPath()
            for pts in lines:
                p.moveTo(*pts[0])
                for pt in pts[1:]:
                    p.lineTo(*pt)
            strokes[w] = p
        return Chunk(QRectF(self.x0, self.y0, self.x1 - self.x0, self.y1 - self.y0), fill, strokes)


def _arcpoly_points(prim: gp.ArcPoly) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for (x1, y1), (x2, y2), (clockwise, (cx, cy)) in prim.segments:
        if clockwise is None:
            pts.append((x1, y1))
        else:
            pts.extend(list(arc_points(cx, cy, x1, y1, x2, y2, clockwise, ARC_MAX_ERROR_MM))[:-1])
    return pts


def _positive(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return pts if signed_area(pts) >= 0 else pts[::-1]


def _bounds(pts, pad=0.0):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


class RenderList:
    def __init__(self, runs: list[Run], bbox: QRectF, primitive_count: int, failed_objects: int):
        self.runs = runs
        self.bbox = bbox
        self.primitive_count = primitive_count
        self.failed_objects = failed_objects

    @classmethod
    def from_primitives(
        cls, prims: Iterable[gp.GraphicPrimitive], negative_extent: QRectF | None = None
    ) -> RenderList:
        """Compile primitives (mm). ``negative_extent`` marks a negative layer (internal plane):
        the extent is filled first and every primitive's polarity is inverted."""
        runs: list[Run] = []
        builders: dict[tuple[int, int], _ChunkBuilder] = {}
        dark_state: bool | None = None
        count = 0
        bx0 = by0 = math.inf
        bx1 = by1 = -math.inf

        def flush():
            if builders:
                runs.append(Run(bool(dark_state), [b.build() for b in builders.values()]))
                builders.clear()

        if negative_extent is not None:
            r = negative_extent
            b = _ChunkBuilder()
            b.polys.append([(r.left(), r.top()), (r.right(), r.top()), (r.right(), r.bottom()),
                            (r.left(), r.bottom())])  # fmt: skip
            b.grow(r.left(), r.top(), r.right(), r.bottom())
            runs.append(Run(True, [b.build()]))
            bx0, by0, bx1, by1 = r.left(), r.top(), r.right(), r.bottom()

        for prim in prims:
            dark = prim.polarity_dark != (negative_extent is not None)
            if dark != dark_state:
                flush()
                dark_state = dark
            geom = _prim_geometry(prim)
            if geom is None:
                continue
            kind, data, (x0, y0, x1, y1) = geom
            key = (math.floor((x0 + x1) / 2 / CHUNK_MM), math.floor((y0 + y1) / 2 / CHUNK_MM))
            b = builders.get(key)
            if b is None:
                b = builders[key] = _ChunkBuilder()
            b.grow(x0, y0, x1, y1)
            if kind == "poly":
                b.polys.append(data)
            elif kind == "circle":
                b.circles.append(data)
            else:
                w, pts = data
                b.strokes.setdefault(w, []).append(pts)
            bx0, by0, bx1, by1 = min(bx0, x0), min(by0, y0), max(bx1, x1), max(by1, y1)
            count += 1
        flush()
        bbox = QRectF() if count == 0 and negative_extent is None else QRectF(bx0, by0, bx1 - bx0, by1 - by0)
        return cls(runs, bbox, count, 0)

    @classmethod
    def from_layer(cls, layer: Layer, negative_extent: QRectF | None = None) -> RenderList:
        prims: list[gp.GraphicPrimitive] = []
        failed = 0
        for obj in layer.parsed.objects:
            try:
                prims.extend(obj.to_primitives(MM))
            except Exception:  # e.g. a stroke with a non-circular aperture
                failed += 1
        rl = cls.from_primitives(prims, negative_extent if layer.kind.is_negative else None)
        rl.failed_objects = failed
        return rl

    def render(self, painter: QPainter, color: QColor, visible: QRectF | None = None) -> None:
        """Paint into ``painter`` (whose transform maps board mm to the target). Clear runs erase."""
        brush = QBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        for run in self.runs:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
                if run.dark
                else QPainter.CompositionMode.CompositionMode_Clear
            )
            for ch in run.chunks:
                if visible is not None and not ch.bbox.intersects(visible):
                    continue
                if not ch.fill.isEmpty():
                    painter.fillPath(ch.fill, brush)
                for w, path in ch.strokes.items():
                    pen = QPen(
                        brush, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin
                    )
                    painter.strokePath(path, pen)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)


def _prim_geometry(prim):
    """-> (kind, data, bbox) where kind is 'poly' | 'circle' | 'stroke'."""
    if isinstance(prim, gp.Circle):
        if prim.r <= 0:
            return None
        return "circle", (prim.x, prim.y, prim.r), (prim.x - prim.r, prim.y - prim.r, prim.x + prim.r,
                                                     prim.y + prim.r)  # fmt: skip
    if isinstance(prim, gp.Line):
        pts = [(prim.x1, prim.y1), (prim.x2, prim.y2)]
        return "stroke", (prim.width, pts), _bounds(pts, prim.width / 2)
    if isinstance(prim, gp.Arc):
        pts = list(arc_points(prim.cx, prim.cy, prim.x1, prim.y1, prim.x2, prim.y2, prim.clockwise,
                              ARC_MAX_ERROR_MM))  # fmt: skip
        return "stroke", (prim.width, pts), _bounds(pts, prim.width / 2)
    if isinstance(prim, gp.Rectangle):
        pts = list(prim.to_arc_poly().outline)
        return "poly", _positive(pts), _bounds(pts)
    if isinstance(prim, gp.ArcPoly):
        pts = _arcpoly_points(prim)
        if len(pts) < 3:
            return None
        return "poly", _positive(pts), _bounds(pts)
    return None


# ---------------------------------------------------------------------------------------------


class CanvasHost(Protocol):
    def is_interacting(self) -> bool: ...
    def schedule_prefetch(self, item: BoardItem) -> None: ...


@dataclass
class LayerEntry:
    layer: Layer
    render_list: RenderList


class BoardItem(QGraphicsItem):
    """All Gerber/Excellon layers, composited bottom-to-top into cached screen tiles.

    Tiles live in "canvas pixel" space: the view's linear transform (scale/flip) without its
    translation, so panning only changes which tiles are needed. Any change to the linear part
    (zoom, top/bottom flip) starts a new tile set; while the user is still zooming the old tiles
    are drawn stretched, and a sharp set is rendered once zooming settles.
    """

    def __init__(self, host: CanvasHost):
        super().__init__()
        self._host = host
        self._entries: list[LayerEntry] = []
        self._bottom_view = False
        self._bounds = QRectF()
        self._tiles: dict[tuple[int, int], QImage | None] = {}
        self._linear: QTransform | None = None
        self._dpr = 1.0
        self._ring: list[tuple[int, int]] = []
        self._scratch: QImage | None = None
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)

    # ---- content -----------------------------------------------------------------------------

    def set_entries(self, entries: list[LayerEntry]) -> None:
        self.prepareGeometryChange()
        self._entries = list(entries)
        bounds = QRectF()
        for e in self._entries:
            if not e.render_list.bbox.isNull():
                bounds = bounds.united(e.render_list.bbox)
        self._bounds = bounds.adjusted(-0.01, -0.01, 0.01, 0.01) if not bounds.isNull() else QRectF()
        self._sort()
        self.invalidate()

    def entries(self) -> list[LayerEntry]:
        return list(self._entries)

    def replace_render_list(self, layer: Layer, render_list: RenderList) -> None:
        for e in self._entries:
            if e.layer is layer:
                e.render_list = render_list
        self.set_entries(self._entries)

    def set_bottom_view(self, bottom: bool) -> None:
        self._bottom_view = bottom
        self._sort()
        self.invalidate()

    def resort(self) -> None:
        self._sort()
        self.invalidate()

    def _sort(self) -> None:
        self._entries.sort(key=lambda e: z_order(e.layer.kind, self._bottom_view))

    def invalidate(self) -> None:
        """Drop all tiles (color, visibility, opacity or order changed)."""
        self._tiles.clear()
        self._ring.clear()
        self.update()

    def boundingRect(self) -> QRectF:
        return self._bounds

    # ---- painting ----------------------------------------------------------------------------

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if self._bounds.isNull():
            return
        device = painter.device()
        dpr = device.devicePixelRatioF()
        t = painter.worldTransform()
        viewport = QRectF(0, 0, device.width(), device.height())
        needed = viewport.intersected(t.mapRect(self._bounds))
        if needed.isEmpty():
            return
        linear = QTransform(t.m11(), t.m12(), t.m21(), t.m22(), 0, 0)

        if self._linear is None or dpr != self._dpr or not _same(self._linear, linear):
            if self._linear is not None and self._tiles and self._host.is_interacting():
                # Mid-zoom: stretch the existing tiles; sharp tiles follow when zooming settles.
                self._draw_tiles(painter, self._linear.inverted()[0] * t, self._tiles.items())
                return
            self._tiles.clear()
            self._ring.clear()
            self._linear, self._dpr = linear, dpr

        dx, dy = t.dx(), t.dy()
        i0, i1, j0, j1 = _tile_range(needed.translated(-dx, -dy))
        for key in _keys(i0, i1, j0, j1):
            if key not in self._tiles:
                self._tiles[key] = self._render_tile(*key)
        # Snap the tile grid to whole device pixels so tiles meet without seams.
        ox, oy = round(dx * dpr) / dpr, round(dy * dpr) / dpr
        visible = [(k, self._tiles[k]) for k in _keys(i0, i1, j0, j1)]
        self._draw_tiles(painter, QTransform.fromTranslate(ox, oy), visible)

        k = TILE_KEEP
        self._tiles = {
            key: img
            for key, img in self._tiles.items()
            if i0 - k <= key[0] <= i1 + k and j0 - k <= key[1] <= j1 + k
        }
        r = PREFETCH_RING
        self._ring = [key for key in _keys(i0 - r, i1 + r, j0 - r, j1 + r) if key not in self._tiles]
        if self._ring:
            self._host.schedule_prefetch(self)

    def prefetch_step(self, budget_s: float = 0.008) -> bool:
        """Render ring tiles until the time budget is spent. Returns True if more remain."""
        start = time.perf_counter()
        while self._ring and self._linear is not None:
            key = self._ring.pop()
            if key not in self._tiles:
                self._tiles[key] = self._render_tile(*key)
            if time.perf_counter() - start > budget_s:
                break
        return bool(self._ring)

    @staticmethod
    def _draw_tiles(painter: QPainter, transform: QTransform, tiles) -> None:
        painter.save()
        painter.setTransform(transform)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for (i, j), img in tiles:
            if img is not None:
                painter.drawImage(QPointF(i * TILE_PX, j * TILE_PX), img)
        painter.restore()

    def _render_tile(self, i: int, j: int) -> QImage | None:
        tile = QRectF(i * TILE_PX, j * TILE_PX, TILE_PX, TILE_PX)
        visible_mm = self._linear.inverted()[0].mapRect(tile)
        layers = [
            e
            for e in self._entries
            if e.layer.visible
            and not e.render_list.bbox.isNull()
            and e.render_list.bbox.intersects(visible_mm)
        ]
        if not layers:
            return None
        n = math.ceil(TILE_PX * self._dpr)
        img = QImage(n, n, QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(self._dpr)
        img.fill(Qt.GlobalColor.transparent)
        if self._scratch is None or self._scratch.width() != n:
            self._scratch = QImage(n, n, QImage.Format.Format_ARGB32_Premultiplied)
        scratch = self._scratch
        scratch.setDevicePixelRatio(self._dpr)
        xform = self._linear * QTransform.fromTranslate(-tile.left(), -tile.top())
        out = QPainter(img)
        for e in layers:
            scratch.fill(Qt.GlobalColor.transparent)
            p = QPainter(scratch)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setTransform(xform)
            e.render_list.render(p, QColor(e.layer.color), visible_mm)
            p.end()
            out.setOpacity(e.layer.opacity)
            out.drawImage(QPointF(0, 0), scratch)
        out.end()
        return img


def _tile_range(rect: QRectF) -> tuple[int, int, int, int]:
    eps = 1e-6
    return (
        math.floor(rect.left() / TILE_PX),
        math.floor((rect.right() - eps) / TILE_PX),
        math.floor(rect.top() / TILE_PX),
        math.floor((rect.bottom() - eps) / TILE_PX),
    )


def _keys(i0: int, i1: int, j0: int, j1: int) -> list[tuple[int, int]]:
    return [(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]


def _same(a: QTransform, b: QTransform) -> bool:
    pairs = ((a.m11(), b.m11()), (a.m12(), b.m12()), (a.m21(), b.m21()), (a.m22(), b.m22()))
    return all(math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12) for x, y in pairs)
