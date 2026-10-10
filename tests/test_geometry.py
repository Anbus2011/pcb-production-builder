import math

import pytest

from panelcheck.model.geometry import (
    DisplayUnit,
    arc_points,
    format_length,
    mm_to,
    signed_area,
    view_scale_factors,
)


def test_units():
    assert mm_to(25.4, DisplayUnit.MIL) == pytest.approx(1000)
    assert mm_to(1.5, DisplayUnit.MM) == 1.5
    assert format_length(1.0, DisplayUnit.MM) == "1.000"
    assert format_length(0.0254, DisplayUnit.MIL) == "1.0"


def test_view_flips_y_only_and_mirrors_x_in_bottom_view():
    assert view_scale_factors(10, False) == (10, -10)
    assert view_scale_factors(10, True) == (-10, -10)


def test_signed_area_orientation():
    ccw = [(0, 0), (2, 0), (2, 1), (0, 1)]
    assert signed_area(ccw) == pytest.approx(2)
    assert signed_area(ccw[::-1]) == pytest.approx(-2)


@pytest.mark.parametrize("clockwise", [False, True])
def test_quarter_arc_direction_and_error(clockwise):
    # From (1,0) to (0,1) about the origin: CCW is the short quarter, CW the long three quarters.
    pts = list(arc_points(0, 0, 1, 0, 0, 1, clockwise, max_error=0.001))
    assert pts[0] == pytest.approx((1, 0))
    assert pts[-1] == pytest.approx((0, 1), abs=1e-9)
    assert all(math.hypot(x, y) == pytest.approx(1) for x, y in pts)
    mid = pts[len(pts) // 2]
    if clockwise:
        assert mid[0] < 0 and mid[1] < 0  # passes through the third quadrant
    else:
        assert mid[0] > 0 and mid[1] > 0
    # chord sagitta stays within max_error
    for (x1, y1), (x2, y2) in zip(pts, pts[1:], strict=False):
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        assert 1 - math.hypot(mx, my) <= 0.001 + 1e-9


def test_full_circle_when_start_equals_end():
    pts = list(arc_points(5, 5, 6, 5, 6, 5, False))
    xs = [p[0] for p in pts]
    assert min(xs) == pytest.approx(4, abs=1e-3)
    assert pts[0] == pytest.approx(pts[-1])
