"""Units and coordinate helpers.

Internal units are millimetres. Board/panel coordinates are Y-up (Gerber/Altium convention); the
only place that maps them to Qt's Y-down screen space is :func:`view_scale_factors`.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

MM_PER_INCH = 25.4
MM_PER_MIL = 0.0254


class DisplayUnit(StrEnum):
    MM = "mm"
    MIL = "mil"


def mm_to(value_mm: float, unit: DisplayUnit) -> float:
    return value_mm / MM_PER_MIL if unit is DisplayUnit.MIL else value_mm


def format_length(value_mm: float, unit: DisplayUnit) -> str:
    if unit is DisplayUnit.MIL:
        return f"{mm_to(value_mm, unit):.1f}"
    return f"{value_mm:.3f}"


def view_scale_factors(px_per_mm: float, mirrored: bool) -> tuple[float, float]:
    """Scale factors for the view transform: board mm (Y-up) -> screen pixels (Y-down).

    This is the single place where Y is flipped. ``mirrored`` (bottom view, or the M toggle)
    additionally mirrors the display in X; stored coordinates are never changed.
    """
    return (-px_per_mm if mirrored else px_per_mm), -px_per_mm


@dataclass(frozen=True)
class Transform2D:
    """Board instance transform: ``panel_xy = R(theta) · board_xy + origin`` (theta in degrees, CCW)."""

    origin_x: float = 0.0
    origin_y: float = 0.0
    theta: float = 0.0

    def apply(self, x: float, y: float) -> tuple[float, float]:
        if self.theta == 0:
            return x + self.origin_x, y + self.origin_y
        c, s = math.cos(math.radians(self.theta)), math.sin(math.radians(self.theta))
        return c * x - s * y + self.origin_x, s * x + c * y + self.origin_y

    def rotate_angle(self, rotation: float) -> float:
        """Part rotation on the panel: ``part_rot + theta``, normalized to [0, 360)."""
        return normalize_angle(rotation + self.theta)


def normalize_angle(deg: float) -> float:
    a = deg % 360.0
    return 0.0 if a == 360.0 else a


def signed_area(points: list[tuple[float, float]]) -> float:
    """Shoelace signed area; positive for counter-clockwise (Y-up) polygons."""
    a = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2


def arc_points(
    cx: float,
    cy: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    clockwise: bool,
    max_error: float = 0.002,
) -> Iterator[tuple[float, float]]:
    """Polyline approximation of a circular arc, including both end points.

    Start and end at the same point is a full circle (Gerber convention in multi-quadrant mode).
    ``max_error`` is the maximum sagitta in mm.
    """
    r = math.hypot(x1 - cx, y1 - cy)
    a1 = math.atan2(y1 - cy, x1 - cx)
    a2 = math.atan2(y2 - cy, x2 - cx)
    if math.isclose(x1, x2, abs_tol=1e-9) and math.isclose(y1, y2, abs_tol=1e-9):
        sweep = -2 * math.pi if clockwise else 2 * math.pi
    else:
        sweep = a2 - a1
        if clockwise and sweep > 0:
            sweep -= 2 * math.pi
        elif not clockwise and sweep < 0:
            sweep += 2 * math.pi
    if r < 1e-9:
        yield (x1, y1)
        yield (x2, y2)
        return
    err = min(max_error, r)
    step = 2 * math.acos(max(-1.0, 1 - err / r))
    n = max(2, math.ceil(abs(sweep) / step)) if step > 0 else 2
    for i in range(n + 1):
        a = a1 + sweep * i / n
        yield (cx + r * math.cos(a), cy + r * math.sin(a))
