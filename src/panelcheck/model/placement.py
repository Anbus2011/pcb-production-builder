"""Pick-and-place data: parts with board-coordinate centroids (mm, Y-up, Altium origin)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from panelcheck.model.geometry import Transform2D
from panelcheck.model.layers import Side


@dataclass(frozen=True)
class Part:
    designator: str
    comment: str
    side: Side  # TOP or BOTTOM
    footprint: str
    x: float  # mm, board coordinates; bottom-side X is as seen from the top (verified, MC60A_HV)
    y: float
    rotation: float  # degrees CCW, [0, 360)
    description: str = ""

    def describe(self) -> str:
        bits = [self.designator, self.comment, self.footprint, self.side.value, f"{self.rotation:g}°"]
        return "  ".join(b for b in bits if b)


@dataclass
class PnPFile:
    path: Path
    parts: list[Part]
    coord_set: str  # the column set the centroids came from: "Center", "Mid", "Ref" or "Pad"
    coord_sets: list[str]  # every complete X/Y column set in the file
    source_unit: str  # "mm", "mil", "in" or "mixed"
    header_line: int  # 1-based line number of the column header row
    info: dict[str, str] = field(default_factory=dict)  # "Key: value" lines from the preamble
    warnings: list[str] = field(default_factory=list)

    def count(self, side: Side) -> int:
        return sum(1 for p in self.parts if p.side is side)


Point = tuple[float, float]
Polygon = tuple[Point, ...]


@dataclass(frozen=True)
class Footprint:
    """A part's footprint geometry (from ODB++), in board or panel mm."""

    package: str
    side: Side
    outline: tuple[Polygon, ...]  # body contour, or the pads' bounding box (see outline_is_body)
    outline_is_body: bool
    pads: tuple[Polygon, ...]
    pin1: Point | None  # center of pin 1's pad

    def transformed(self, t: Transform2D) -> Footprint:
        def poly(p: Polygon) -> Polygon:
            return tuple(t.apply(x, y) for x, y in p)

        return Footprint(
            package=self.package,
            side=self.side,
            outline=tuple(poly(p) for p in self.outline),
            outline_is_body=self.outline_is_body,
            pads=tuple(poly(p) for p in self.pads),
            pin1=t.apply(*self.pin1) if self.pin1 is not None else None,
        )


@dataclass(frozen=True)
class PlacedPart:
    """One part of one board instance, in panel coordinates."""

    part: Part
    instance: int
    x: float
    y: float
    rotation: float
    footprint: Footprint | None = None


def place(
    parts: list[Part], instances: list[Transform2D], footprints: dict[str, Footprint] | None = None
) -> list[PlacedPart]:
    footprints = footprints or {}
    out = []
    for n, t in enumerate(instances):
        for p in parts:
            x, y = t.apply(p.x, p.y)
            fp = footprints.get(p.designator)
            fp = fp.transformed(t) if fp is not None and fp.side is p.side else None
            out.append(PlacedPart(p, n, x, y, t.rotate_angle(p.rotation), fp))
    return out
