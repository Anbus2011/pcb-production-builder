"""Layer model: types, sides, identification, colors and draw order."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Side(StrEnum):
    TOP = "top"
    BOTTOM = "bottom"
    INNER = "inner"
    NONE = "none"


class LayerType(StrEnum):
    COPPER = "copper"
    PLANE = "plane"  # negative internal plane
    SILK = "silk"
    MASK = "mask"
    PASTE = "paste"
    KEEPOUT = "keepout"
    OUTLINE = "outline"
    MECHANICAL = "mechanical"
    DRILL_DRAWING = "drill_drawing"
    DRILL_GUIDE = "drill_guide"
    PAD_MASTER = "pad_master"
    VCUT = "vcut"  # V-score lines on a board-house panel
    DRILL = "drill"
    MISC_DRILL = "misc_drill"  # board-house auxiliary drill layer (e.g. JLC "sk"); not real holes
    UNKNOWN = "unknown"  # parseable Gerber/Excellon of unknown function
    REPORT = "report"  # not artwork; listed greyed out, never parsed


class LayerGroup(StrEnum):
    TOP = "Top"
    BOTTOM = "Bottom"
    INNER = "Inner"
    DRILL = "Drill"
    MECHANICAL = "Mechanical"
    OTHER = "Other"


GROUP_ORDER = list(LayerGroup)

# How a layer's kind was decided, in priority order.
ID_X2 = "X2 FileFunction"
ID_EXTENSION = "file extension"
ID_LAYER_NAME = "board-house layer name"
ID_CONTENT = "file content"
ID_MANUAL = "manual"


@dataclass(frozen=True)
class LayerKind:
    type: LayerType
    side: Side = Side.NONE
    index: int | None = None  # mid-layer / plane / mechanical number

    @property
    def group(self) -> LayerGroup:
        if self.type in (LayerType.DRILL, LayerType.MISC_DRILL):
            return LayerGroup.DRILL
        if self.type in (LayerType.REPORT, LayerType.UNKNOWN):
            return LayerGroup.OTHER
        if self.type in (
            LayerType.MECHANICAL,
            LayerType.OUTLINE,
            LayerType.KEEPOUT,
            LayerType.VCUT,
            LayerType.DRILL_DRAWING,
            LayerType.DRILL_GUIDE,
        ):
            return LayerGroup.MECHANICAL
        if self.side is Side.TOP:
            return LayerGroup.TOP
        if self.side is Side.BOTTOM:
            return LayerGroup.BOTTOM
        if self.side is Side.INNER:
            return LayerGroup.INNER
        return LayerGroup.OTHER

    @property
    def is_artwork(self) -> bool:
        return self.type is not LayerType.REPORT

    @property
    def is_negative(self) -> bool:
        return self.type is LayerType.PLANE

    @property
    def hidden_by_default(self) -> bool:
        """Unchecked when loaded: bottom and inner layers (DK works top-first), plus drawings,
        guides and reports. Top, drill and mechanical layers start visible."""
        if self.side in (Side.BOTTOM, Side.INNER):
            return True
        return self.type in (
            LayerType.DRILL_DRAWING,
            LayerType.DRILL_GUIDE,
            LayerType.PAD_MASTER,
            LayerType.MISC_DRILL,
            LayerType.REPORT,
        )

    def label(self) -> str:
        n = "" if self.index is None else f" {self.index}"
        side = {Side.TOP: "Top ", Side.BOTTOM: "Bottom "}.get(self.side, "")
        match self.type:
            case LayerType.COPPER if self.side is Side.INNER:
                return f"Mid-Layer{n}"
            case LayerType.COPPER:
                return f"{side}Copper"
            case LayerType.PLANE:
                return f"Internal Plane{n}"
            case LayerType.SILK:
                return f"{side}Overlay"
            case LayerType.MASK:
                return f"{side}Solder Mask"
            case LayerType.PASTE:
                return f"{side}Paste"
            case LayerType.PAD_MASTER:
                return f"{side}Pad Master"
            case LayerType.KEEPOUT:
                return "Keep-Out"
            case LayerType.OUTLINE:
                return "Board Outline"
            case LayerType.MECHANICAL:
                return f"Mechanical{n}"
            case LayerType.DRILL_DRAWING:
                return f"Drill Drawing{n}"
            case LayerType.DRILL_GUIDE:
                return f"Drill Guide{n}"
            case LayerType.DRILL:
                return "Drill"
            case LayerType.MISC_DRILL:
                return "Misc Drill"
            case LayerType.VCUT:
                return "V-Cut"
            case LayerType.UNKNOWN:
                return "Unknown"
            case _:
                return "Report / other"


REPORT = LayerKind(LayerType.REPORT)
UNKNOWN = LayerKind(LayerType.UNKNOWN)
DRILL = LayerKind(LayerType.DRILL)


def assignable_kinds(inner_count: int = 30, mech_count: int = 16) -> list[LayerKind]:
    """Kinds offered in the right-click "Layer type" menu."""
    kinds = []
    for side in (Side.TOP, Side.BOTTOM):
        for t in (LayerType.COPPER, LayerType.MASK, LayerType.PASTE, LayerType.SILK):
            kinds.append(LayerKind(t, side))
    kinds += [LayerKind(LayerType.COPPER, Side.INNER, i) for i in range(1, inner_count + 1)]
    kinds += [LayerKind(LayerType.PLANE, Side.INNER, i) for i in range(1, 17)]
    kinds += [
        DRILL,
        LayerKind(LayerType.MISC_DRILL),
        LayerKind(LayerType.OUTLINE),
        LayerKind(LayerType.KEEPOUT),
    ]
    kinds += [LayerKind(LayerType.VCUT)]
    kinds += [LayerKind(LayerType.MECHANICAL, Side.NONE, i) for i in range(1, mech_count + 1)]
    kinds += [UNKNOWN]
    return kinds


# ---------------------------------------------------------------------------------------------
# Identification


_REPORT_EXTS = {
    ".drr", ".rep", ".rul", ".extrep", ".apr", ".apr_lib", ".ldp", ".drc", ".html", ".htm",
    ".csv", ".xlsx", ".xls", ".pdf", ".zip", ".log", ".ini", ".md", ".json", ".png", ".jpg",
}  # fmt: skip

_DRILL_EXTS = {".txt", ".drl", ".xln", ".drd", ".exc", ".nc"}

_FIXED_EXTS = {
    ".gtl": LayerKind(LayerType.COPPER, Side.TOP),
    ".gbl": LayerKind(LayerType.COPPER, Side.BOTTOM),
    ".gto": LayerKind(LayerType.SILK, Side.TOP),
    ".gbo": LayerKind(LayerType.SILK, Side.BOTTOM),
    ".gts": LayerKind(LayerType.MASK, Side.TOP),
    ".gbs": LayerKind(LayerType.MASK, Side.BOTTOM),
    ".gtp": LayerKind(LayerType.PASTE, Side.TOP),
    ".gbp": LayerKind(LayerType.PASTE, Side.BOTTOM),
    ".gpt": LayerKind(LayerType.PAD_MASTER, Side.TOP),
    ".gpb": LayerKind(LayerType.PAD_MASTER, Side.BOTTOM),
    ".gko": LayerKind(LayerType.KEEPOUT),
}

_NUMBERED_EXTS = [
    (re.compile(r"^\.g(\d+)$"), LayerType.COPPER, Side.INNER),
    (re.compile(r"^\.gp(\d+)$"), LayerType.PLANE, Side.INNER),
    (re.compile(r"^\.gm(\d+)$"), LayerType.MECHANICAL, Side.NONE),
    (re.compile(r"^\.gd(\d+)$"), LayerType.DRILL_DRAWING, Side.NONE),
    (re.compile(r"^\.gg(\d+)$"), LayerType.DRILL_GUIDE, Side.NONE),
]


def _ext(filename: str) -> str:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot >= 0 else ""


def is_report_extension(filename: str) -> bool:
    return _ext(filename) in _REPORT_EXTS


def is_drill_extension(filename: str) -> bool:
    return _ext(filename) in _DRILL_EXTS


def kind_from_extension(filename: str) -> LayerKind | None:
    """Altium extension -> kind, or None if the extension isn't an Altium Gerber extension."""
    ext = _ext(filename)
    if ext in _FIXED_EXTS:
        return _FIXED_EXTS[ext]
    for rx, t, side in _NUMBERED_EXTS:
        if m := rx.match(ext):
            return LayerKind(t, side, int(m.group(1)))
    return None


# Board-house / ODB++ layer names (JLC's jlccam names its panel Gerbers this way, without extensions).
_BOARD_HOUSE_NAMES = {
    "tl": LayerKind(LayerType.COPPER, Side.TOP),
    "top": LayerKind(LayerType.COPPER, Side.TOP),
    "bl": LayerKind(LayerType.COPPER, Side.BOTTOM),
    "bot": LayerKind(LayerType.COPPER, Side.BOTTOM),
    "bottom": LayerKind(LayerType.COPPER, Side.BOTTOM),
    "to": LayerKind(LayerType.SILK, Side.TOP),
    "bo": LayerKind(LayerType.SILK, Side.BOTTOM),
    "ts": LayerKind(LayerType.MASK, Side.TOP),
    "bs": LayerKind(LayerType.MASK, Side.BOTTOM),
    "tp": LayerKind(LayerType.PASTE, Side.TOP),
    "bp": LayerKind(LayerType.PASTE, Side.BOTTOM),
    "drl": DRILL,
    "drill": DRILL,
    "ko": LayerKind(LayerType.OUTLINE),
    "outline": LayerKind(LayerType.OUTLINE),
    "profile": LayerKind(LayerType.OUTLINE),
    "rout": LayerKind(LayerType.OUTLINE),
    "vcut": LayerKind(LayerType.VCUT),
    "v-cut": LayerKind(LayerType.VCUT),
    "vscore": LayerKind(LayerType.VCUT),
    "sk": LayerKind(LayerType.MISC_DRILL),
}
_LAYER_NAME_COMMENT_RX = re.compile(r"--\s*layer:\s*([^\s*]+)", re.I)


def layer_name_from_comments(comments: list[str]) -> str | None:
    """Layer name a CAM tool wrote into the header, e.g. jlccam's ``G04 -- layer:tl*``."""
    for c in comments:
        if m := _LAYER_NAME_COMMENT_RX.search(c):
            return m.group(1)
    return None


def kind_from_layer_name(name: str) -> LayerKind | None:
    """Board-house layer name (``tl``, ``l2``, ``drl``, ``vcut`` …) -> kind.

    ``lN`` is copper layer N of the stack, so ``l1`` is top copper and ``l2`` is Mid-Layer 1.
    Altium-style names (``gtl``, ``gbo`` …) are accepted too.
    """
    n = name.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    if n in _BOARD_HOUSE_NAMES:
        return _BOARD_HOUSE_NAMES[n]
    if m := re.fullmatch(r"l(\d+)", n):
        k = int(m.group(1))
        return (
            LayerKind(LayerType.COPPER, Side.TOP)
            if k == 1
            else LayerKind(LayerType.COPPER, Side.INNER, k - 1)
        )
    if n.startswith("g") and not n.startswith("."):
        return kind_from_extension("." + n)
    return None


def kind_from_file_function(value: str | tuple[str, ...] | list[str]) -> LayerKind | None:
    """Gerber X2 ``.FileFunction`` value -> kind.

    Accepts either gerbonara's parsed tuple or the raw comma-separated string.
    """
    parts = [p.strip() for p in (value.split(",") if isinstance(value, str) else value)]
    if not parts or not parts[0]:
        return None
    func = parts[0].lower()

    def side_of(s: str) -> Side:
        return {"top": Side.TOP, "bot": Side.BOTTOM, "inr": Side.INNER}.get(s.lower(), Side.NONE)

    if func == "copper" and len(parts) >= 3:
        side = side_of(parts[2])
        index = None
        if side is Side.INNER and (m := re.match(r"L(\d+)", parts[1], re.I)):
            index = int(m.group(1)) - 1  # L2 is Mid-Layer 1
        is_plane = len(parts) >= 4 and parts[3].lower() == "plane"
        return LayerKind(LayerType.PLANE if is_plane else LayerType.COPPER, side, index)
    by_side = {"soldermask": LayerType.MASK, "paste": LayerType.PASTE, "legend": LayerType.SILK}
    if func in by_side and len(parts) >= 2:
        return LayerKind(by_side[func], side_of(parts[1]))
    if func == "profile":
        return LayerKind(LayerType.OUTLINE)
    if func in ("plated", "nonplated"):
        return DRILL
    if func in ("drillmap",):
        return LayerKind(LayerType.DRILL_DRAWING)
    if func == "keep-out":
        return LayerKind(LayerType.KEEPOUT)
    if func == "other":
        if len(parts) >= 2 and (m := re.match(r"mechanical[_ ]?(\d+)", parts[1], re.I)):
            return LayerKind(LayerType.MECHANICAL, Side.NONE, int(m.group(1)))
        return LayerKind(LayerType.MECHANICAL)
    return None


_X2_COMMENT_RX = re.compile(r"#@!\s*TF\.FileFunction,(.*)$")
_LAYER_COLOR_RX = re.compile(r"Layer_Color=(\d+)")


def file_function_from(file_attrs: dict, comments: list[str]) -> str | tuple | None:
    """Find ``.FileFunction`` in real X2 attributes or in Altium's ``G04 #@! TF.`` comment form."""
    if ".FileFunction" in file_attrs:
        return file_attrs[".FileFunction"]
    for c in comments:
        if m := _X2_COMMENT_RX.search(c):
            return m.group(1)
    return None


def altium_layer_color(comments: list[str]) -> str | None:
    """Altium's ``Layer_Color=<int>`` comment is a Windows COLORREF (0x00BBGGRR). Return ``#RRGGBB``."""
    for c in comments:
        if m := _LAYER_COLOR_RX.search(c):
            v = int(m.group(1))
            r, g, b = v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF
            return f"#{r:02X}{g:02X}{b:02X}"
    return None


# ---------------------------------------------------------------------------------------------
# Colors and draw order

BACKGROUND_COLOR = "#000000"
TOP_MARKER_COLOR = "#00FFFF"
BOTTOM_MARKER_COLOR = "#FF8C00"
DEFAULT_OPACITY = 0.7

MID_LAYER_PALETTE = [
    "#BC8E00", "#70DBFA", "#00CC66", "#9966FF", "#00FFFF", "#800080", "#FF00FF", "#808000",
    "#FFCC99", "#33CCCC", "#99CC00", "#FF6600",
]  # fmt: skip

_FIXED_COLORS = {
    (LayerType.COPPER, Side.TOP): "#FF0000",
    (LayerType.COPPER, Side.BOTTOM): "#0000FF",
    (LayerType.SILK, Side.TOP): "#FFFF00",
    (LayerType.SILK, Side.BOTTOM): "#808000",
    (LayerType.MASK, Side.TOP): "#800080",
    (LayerType.MASK, Side.BOTTOM): "#FF00FF",
    (LayerType.PASTE, Side.TOP): "#808080",
    (LayerType.PASTE, Side.BOTTOM): "#800000",
    (LayerType.PAD_MASTER, Side.TOP): "#808080",
    (LayerType.PAD_MASTER, Side.BOTTOM): "#800000",
}


def default_color(kind: LayerKind) -> str:
    """Approximate Altium default color for a kind (used when the file carries no Layer_Color)."""
    if (kind.type, kind.side) in _FIXED_COLORS:
        return _FIXED_COLORS[(kind.type, kind.side)]
    if kind.type in (LayerType.COPPER, LayerType.PLANE):
        i = (kind.index or 1) - 1
        offset = 0 if kind.type is LayerType.COPPER else 6
        return MID_LAYER_PALETTE[(i + offset) % len(MID_LAYER_PALETTE)]
    if kind.type in (LayerType.KEEPOUT, LayerType.OUTLINE):
        return "#FF00FF"
    if kind.type is LayerType.DRILL:
        return "#C0C0C0"
    if kind.type is LayerType.MISC_DRILL:
        return "#808080"
    if kind.type is LayerType.VCUT:
        return "#E0E0E0"
    if kind.type is LayerType.MECHANICAL:
        return "#FF00FF" if (kind.index or 1) == 1 else "#C0C0C0"
    return "#C0C0C0"


_SIDE_STACK = [LayerType.COPPER, LayerType.MASK, LayerType.PASTE, LayerType.PAD_MASTER, LayerType.SILK]


def z_order(kind: LayerKind, bottom_view: bool = False) -> float:
    """Draw order, bottom to top: far side -> inner -> near side -> drill -> outline/mechanical.

    In top view the near side is Top; bottom view swaps the sides and reverses the inner stack.
    """
    near, far = (Side.BOTTOM, Side.TOP) if bottom_view else (Side.TOP, Side.BOTTOM)
    t = kind.type
    if t is LayerType.DRILL:
        return 400
    if t is LayerType.MISC_DRILL:
        return 410
    if t in (LayerType.OUTLINE, LayerType.KEEPOUT, LayerType.MECHANICAL, LayerType.VCUT):
        return 500 + (kind.index or 0) * 0.01
    if t in (LayerType.DRILL_DRAWING, LayerType.DRILL_GUIDE):
        return 450
    if t is LayerType.UNKNOWN:
        return 350
    if kind.side is far and t in _SIDE_STACK:
        return 100 - _SIDE_STACK.index(t)  # far silk deepest
    if kind.side is Side.INNER:
        idx = kind.index or 0
        # In top view, layers closer to the top (low index) draw later. Planes interleave at +0.5.
        base = idx + (0.5 if t is LayerType.PLANE else 0)
        return 200 + (base if bottom_view else 100 - base) * 0.5
    if kind.side is near and t in _SIDE_STACK:
        return 300 + _SIDE_STACK.index(t)  # near silk on top
    return 350


# ---------------------------------------------------------------------------------------------


@dataclass
class Layer:
    """One input file (Gerber, Excellon, or a report listed greyed out)."""

    name: str  # file name as shown in the tree
    kind: LayerKind
    id_source: str
    color: str
    opacity: float = DEFAULT_OPACITY
    visible: bool = True
    parsed: Any = None  # gerbonara GerberFile / ExcellonFile, or None for reports
    file_color: str | None = None  # from Altium Layer_Color comment
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def is_drill_file(self) -> bool:
        return type(self.parsed).__name__ == "ExcellonFile"

    def reassign(self, kind: LayerKind) -> None:
        """Manual layer-type override. Color resets to the new kind's default."""
        self.kind = kind
        self.id_source = ID_MANUAL
        self.color = default_color(kind)
