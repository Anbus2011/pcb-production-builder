"""Snap targets: pad flash centers and drill/slot centers, with a grid index for nearest lookup."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from gerbonara.graphic_objects import Flash, Line
from gerbonara.utils import MM

from panelcheck.model.layers import Layer, LayerType, Side

PAD_LAYER_TYPES = {LayerType.COPPER, LayerType.PLANE, LayerType.PASTE, LayerType.MASK, LayerType.PAD_MASTER,
                   LayerType.UNKNOWN}  # fmt: skip

# Lower wins when several targets share (almost) the same center.
_PRIORITY = {"hole": 0, "slot": 0, "pad": 1}
_TYPE_PRIORITY = {LayerType.DRILL: 0, LayerType.COPPER: 1, LayerType.PLANE: 1, LayerType.PASTE: 2}
_SAME_POINT_MM = 0.001


@dataclass(frozen=True, eq=False)
class SnapTarget:
    x: float
    y: float
    kind: str  # "pad" | "hole" | "slot"
    layer: Layer
    width: float  # mm (diameter for holes)
    height: float  # mm
    plated: bool | None = None

    def describe(self) -> str:
        name = self.layer.name.replace("\\", "/").rsplit("/", 1)[-1]
        if self.kind == "hole":
            plating = {True: " PTH", False: " NPTH"}.get(self.plated, "")
            return f"hole Ø{self.width:.3f} mm{plating} ({name})"
        if self.kind == "slot":
            return f"slot {self.width:.3f} mm ({name})"
        return f"pad {self.width:.3f} × {self.height:.3f} mm ({name})"


def targets_from_layer(layer: Layer) -> list[SnapTarget]:
    """Snap targets in one parsed layer (board mm). Clear-polarity flashes are skipped."""
    if layer.parsed is None:
        return []
    out: list[SnapTarget] = []
    is_drill = layer.kind.type is LayerType.DRILL or layer.is_drill_file
    for obj in layer.parsed.objects:
        try:
            if isinstance(obj, Flash):
                if not obj.polarity_dark:
                    continue
                o = obj.converted(MM)
                (x0, y0), (x1, y1) = obj.aperture.bounding_box(MM)
                plated = getattr(obj.aperture, "plated", None)
                kind = "hole" if is_drill else "pad"
                out.append(SnapTarget(o.x, o.y, kind, layer, x1 - x0, y1 - y0, plated))
            elif is_drill and isinstance(obj, Line):
                o = obj.converted(MM)
                d = obj.aperture.equivalent_width(MM)
                cx, cy = (o.x1 + o.x2) / 2, (o.y1 + o.y2) / 2
                length = math.hypot(o.x2 - o.x1, o.y2 - o.y1) + d
                out.append(
                    SnapTarget(cx, cy, "slot", layer, d, length, getattr(obj.aperture, "plated", None))
                )
        except Exception:  # an odd aperture shouldn't stop the rest of the layer
            continue
    return out


def default_filter(t: SnapTarget) -> bool:
    """Snap only to visible pad-bearing layers and drill files."""
    if not t.layer.visible:
        return False
    return t.kind != "pad" or t.layer.kind.type in PAD_LAYER_TYPES


class SnapIndex:
    def __init__(self, targets: Iterable[SnapTarget], cell_mm: float = 2.0):
        self.cell = cell_mm
        self._grid: dict[tuple[int, int], list[SnapTarget]] = {}
        self.count = 0
        for t in targets:
            self._grid.setdefault(self._key(t.x, t.y), []).append(t)
            self.count += 1

    @classmethod
    def from_layers(cls, layers: Iterable[Layer]) -> SnapIndex:
        return cls(t for la in layers for t in targets_from_layer(la))

    def _key(self, x: float, y: float) -> tuple[int, int]:
        return math.floor(x / self.cell), math.floor(y / self.cell)

    def nearest(
        self,
        x: float,
        y: float,
        radius: float,
        accept: Callable[[SnapTarget], bool] = default_filter,
        near_side: Side | None = None,
    ) -> SnapTarget | None:
        """Nearest accepted target within ``radius`` mm. Coincident targets prefer holes, then copper.

        With ``near_side`` (the side being viewed), holes and pads on that side win over closer pads
        on inner layers or the far side; those are used only when nothing on the near side is in range.
        """
        if near_side is not None:
            t = self.nearest(
                x, y, radius, lambda t: accept(t) and t.layer.kind.side in (near_side, Side.NONE)
            )
            if t is not None:
                return t
        n = math.ceil(radius / self.cell)
        kx, ky = self._key(x, y)
        best: tuple | None = None
        best_t: SnapTarget | None = None
        for i in range(kx - n, kx + n + 1):
            for j in range(ky - n, ky + n + 1):
                for t in self._grid.get((i, j), ()):
                    d = math.hypot(t.x - x, t.y - y)
                    if d > radius or not accept(t):
                        continue
                    key = (
                        math.floor(d / _SAME_POINT_MM),
                        _PRIORITY[t.kind],
                        _TYPE_PRIORITY.get(t.layer.kind.type, 3),
                    )
                    if best is None or key < best:
                        best, best_t = key, t
        return best_t
