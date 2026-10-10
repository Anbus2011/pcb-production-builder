import pytest

from panelcheck.model.layers import (
    DRILL,
    LayerGroup,
    LayerKind,
    LayerType,
    Side,
    altium_layer_color,
    default_color,
    file_function_from,
    kind_from_extension,
    kind_from_file_function,
    z_order,
)


@pytest.mark.parametrize(
    "name, kind",
    [
        ("x.GTL", LayerKind(LayerType.COPPER, Side.TOP)),
        ("x.gbl", LayerKind(LayerType.COPPER, Side.BOTTOM)),
        ("x.G1", LayerKind(LayerType.COPPER, Side.INNER, 1)),
        ("x.G30", LayerKind(LayerType.COPPER, Side.INNER, 30)),
        ("x.GP2", LayerKind(LayerType.PLANE, Side.INNER, 2)),
        ("x.GTO", LayerKind(LayerType.SILK, Side.TOP)),
        ("x.GBO", LayerKind(LayerType.SILK, Side.BOTTOM)),
        ("x.GTS", LayerKind(LayerType.MASK, Side.TOP)),
        ("x.GBS", LayerKind(LayerType.MASK, Side.BOTTOM)),
        ("x.GTP", LayerKind(LayerType.PASTE, Side.TOP)),
        ("x.GBP", LayerKind(LayerType.PASTE, Side.BOTTOM)),
        ("x.GKO", LayerKind(LayerType.KEEPOUT)),
        ("x.GM1", LayerKind(LayerType.MECHANICAL, Side.NONE, 1)),
        ("x.GM13", LayerKind(LayerType.MECHANICAL, Side.NONE, 13)),
        ("x.GD1", LayerKind(LayerType.DRILL_DRAWING, Side.NONE, 1)),
        ("x.GG1", LayerKind(LayerType.DRILL_GUIDE, Side.NONE, 1)),
        ("x.GPT", LayerKind(LayerType.PAD_MASTER, Side.TOP)),
        ("dir/sub/x.GTL", LayerKind(LayerType.COPPER, Side.TOP)),
        ("x.gbr", None),
        ("x.TXT", None),
    ],
)
def test_kind_from_extension(name, kind):
    assert kind_from_extension(name) == kind


@pytest.mark.parametrize(
    "ff, kind",
    [
        ("Copper,L1,Top", LayerKind(LayerType.COPPER, Side.TOP)),
        ("Copper,L4,Bot", LayerKind(LayerType.COPPER, Side.BOTTOM)),
        ("Copper,L3,Inr", LayerKind(LayerType.COPPER, Side.INNER, 2)),
        ("Copper,L2,Inr,Plane", LayerKind(LayerType.PLANE, Side.INNER, 1)),
        (("Soldermask", "Top"), LayerKind(LayerType.MASK, Side.TOP)),
        ("Paste,Bot", LayerKind(LayerType.PASTE, Side.BOTTOM)),
        ("Legend,Top", LayerKind(LayerType.SILK, Side.TOP)),
        ("Profile,NP", LayerKind(LayerType.OUTLINE)),
        ("Plated,1,4,PTH", DRILL),
        ("Other,Mechanical_5", LayerKind(LayerType.MECHANICAL, Side.NONE, 5)),
        ("Bogus", None),
    ],
)
def test_kind_from_file_function(ff, kind):
    assert kind_from_file_function(ff) == kind


def test_file_function_prefers_real_attribute_then_altium_comment():
    assert file_function_from({".FileFunction": ("Copper", "L1", "Top")}, []) == ("Copper", "L1", "Top")
    assert file_function_from({}, ["", "#@! TF.FileFunction,Legend,Bot"]) == "Legend,Bot"
    assert file_function_from({}, ["#@! TF.FilePolarity,Positive"]) is None


def test_altium_layer_color_is_bgr():
    assert altium_layer_color(["Layer_Color=255"]) == "#FF0000"  # top copper red
    assert altium_layer_color(["Layer_Color=16711680"]) == "#0000FF"  # bottom copper blue
    assert altium_layer_color(["Layer_Color=8388736"]) == "#800080"  # top mask purple
    assert altium_layer_color(["nothing"]) is None


def test_default_colors_follow_table():
    assert default_color(LayerKind(LayerType.COPPER, Side.TOP)) == "#FF0000"
    assert default_color(LayerKind(LayerType.SILK, Side.TOP)) == "#FFFF00"
    assert default_color(LayerKind(LayerType.KEEPOUT)) == "#FF00FF"


def test_groups():
    assert LayerKind(LayerType.MASK, Side.BOTTOM).group is LayerGroup.BOTTOM
    assert LayerKind(LayerType.PLANE, Side.INNER, 1).group is LayerGroup.INNER
    assert DRILL.group is LayerGroup.DRILL
    assert LayerKind(LayerType.MECHANICAL, Side.NONE, 1).group is LayerGroup.MECHANICAL


def _stack(bottom_view):
    k = LayerKind
    names = {
        "bot_silk": k(LayerType.SILK, Side.BOTTOM),
        "bot_cu": k(LayerType.COPPER, Side.BOTTOM),
        "mid2": k(LayerType.COPPER, Side.INNER, 2),
        "mid1": k(LayerType.COPPER, Side.INNER, 1),
        "top_cu": k(LayerType.COPPER, Side.TOP),
        "top_mask": k(LayerType.MASK, Side.TOP),
        "top_silk": k(LayerType.SILK, Side.TOP),
        "drill": DRILL,
        "outline": k(LayerType.MECHANICAL, Side.NONE, 1),
    }
    return [n for n, _ in sorted(names.items(), key=lambda kv: z_order(kv[1], bottom_view))]


def test_draw_order_top_view():
    assert _stack(False) == [
        "bot_silk", "bot_cu", "mid2", "mid1", "top_cu", "top_mask", "top_silk", "drill", "outline",
    ]  # fmt: skip


def test_draw_order_bottom_view():
    assert _stack(True) == [
        "top_silk", "top_mask", "top_cu", "mid1", "mid2", "bot_cu", "bot_silk", "drill", "outline",
    ]  # fmt: skip
