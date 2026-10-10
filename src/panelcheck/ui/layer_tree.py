"""Left-side layer tree: a Pick & Place group (once a P&P file is loaded), then the Top / Bottom /
Inner / Drill / Mechanical / Other layer groups.

Each layer row has a visibility checkbox, a color swatch (click to change) and an opacity slider.
Right-click a layer to override its type; reports are listed greyed out. The Pick & Place group has
a Top parts and a Bottom parts row, each with a checkbox and a marker color swatch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QHeaderView,
    QMenu,
    QSlider,
    QTreeWidget,
    QTreeWidgetItem,
)

from panelcheck.model.layers import (
    GROUP_ORDER,
    Layer,
    LayerGroup,
    LayerKind,
    LayerType,
    Side,
    assignable_kinds,
    z_order,
)
from panelcheck.model.placement import PnPFile

COL_NAME, COL_COLOR, COL_OPACITY = 0, 1, 2


@dataclass
class OdbRows:
    """What the Pick & Place group shows about the ODB++ job."""

    label: str  # e.g. "ODB++: odb (174 matched)" or "No ODB++ found (right-click to load)"
    tooltip: str = ""
    pads_shown: bool | None = None  # None: no footprints, so no pads toggle
    issues: dict[str, list[str]] = field(default_factory=dict)  # designator -> problems


ROLE_LAYER = Qt.ItemDataRole.UserRole + 1
ROLE_PNP = Qt.ItemDataRole.UserRole + 2  # "top"/"bottom" on P&P part rows, "group" on the P&P group


def swatch(color: str, size: int = 14) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setPen(QColor("#606060"))
    p.setBrush(QColor(color))
    p.drawRect(0, 0, size - 1, size - 1)
    p.end()
    return QIcon(pm)


class LayerTree(QTreeWidget):
    visibilityChanged = Signal(object, bool)  # Layer, visible
    colorChanged = Signal(object, str)  # Layer, "#RRGGBB"
    opacityChanged = Signal(object, float)  # Layer, 0..1
    kindChanged = Signal(object, object)  # Layer, LayerKind
    placementShownChanged = Signal(object, bool)  # Side, shown
    placementColorChanged = Signal(object, str)  # Side, "#RRGGBB"
    placementCoordSetRequested = Signal(str)  # "Center", "Mid", ...
    placementRemoveRequested = Signal()
    placementPadsShownChanged = Signal(bool)
    placementOdbRequested = Signal(str)  # "folder" or "archive": the user wants to pick an ODB++ job

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHeaderLabels(["Layer", "", "Opacity"])
        self.header().setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(COL_COLOR, QHeaderView.ResizeMode.Fixed)
        self.header().setSectionResizeMode(COL_OPACITY, QHeaderView.ResizeMode.Fixed)
        self.header().setStretchLastSection(False)
        self.setColumnWidth(COL_COLOR, 26)
        self.setColumnWidth(COL_OPACITY, 80)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.itemChanged.connect(self._on_item_changed)
        self.itemClicked.connect(self._on_item_clicked)
        self._layers: list[Layer] = []
        self._pnp: PnPFile | None = None
        self._pnp_colors: dict[Side, str] = {}
        self._pnp_shown: dict[Side, bool] = {}
        self._pnp_status = ""
        self._odb: OdbRows | None = None
        self._updating = False

    def set_layers(self, layers: list[Layer]) -> None:
        self._layers = list(layers)
        self.rebuild()

    def set_placement(
        self,
        pnp: PnPFile | None,
        colors: dict[Side, str],
        shown: dict[Side, bool],
        status: str = "",
        odb: OdbRows | None = None,
    ) -> None:
        """Show (or with ``pnp=None`` remove) the Pick & Place group. ``status`` is a note shown
        under it, e.g. that Home must be set before the parts can be placed. ``odb`` describes the
        ODB++ rows (source, pads toggle, cross-check issues)."""
        self._pnp = pnp
        self._pnp_colors = dict(colors)
        self._pnp_shown = dict(shown)
        self._pnp_status = status
        self._odb = odb
        self.rebuild()

    def rebuild(self) -> None:
        self._updating = True
        self.clear()
        if self._pnp is not None:
            self._add_placement_group()
        groups: dict[LayerGroup, QTreeWidgetItem] = {}
        for g in GROUP_ORDER:
            members = [la for la in self._layers if la.kind.group is g]
            if not members:
                continue
            gi = QTreeWidgetItem(self, [g.value])
            gi.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate
            )
            font = gi.font(COL_NAME)
            font.setBold(True)
            gi.setFont(COL_NAME, font)
            groups[g] = gi
            for layer in sorted(members, key=_sort_key):
                self._add_layer_item(gi, layer)
            gi.setExpanded(g is not LayerGroup.OTHER)
        self._updating = False

    def _add_placement_group(self) -> None:
        pnp = self._pnp
        gi = QTreeWidgetItem(self, [f"Pick & Place  —  {pnp.path.name}"])
        gi.setData(COL_NAME, ROLE_PNP, "group")
        gi.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate
        )
        font = gi.font(COL_NAME)
        font.setBold(True)
        gi.setFont(COL_NAME, font)
        tip = [str(pnp.path), f"{len(pnp.parts)} parts, coordinates from {pnp.coord_set} X/Y "
               f"({pnp.source_unit}), header on line {pnp.header_line}"]  # fmt: skip
        tip += [f"{k}: {v}" for k, v in pnp.info.items()]
        tip += [f"warning: {w}" for w in pnp.warnings[:10]]
        if len(pnp.warnings) > 10:
            tip.append(f"... and {len(pnp.warnings) - 10} more warnings")
        tip.append("Right-click for options")
        gi.setToolTip(COL_NAME, "\n".join(tip))
        for side, label in ((Side.TOP, "Top parts"), (Side.BOTTOM, "Bottom parts")):
            item = QTreeWidgetItem(gi, [f"{label} ({pnp.count(side)})"])
            item.setData(COL_NAME, ROLE_PNP, side.value)  # Qt data turns a StrEnum into a plain str
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            shown = self._pnp_shown.get(side, True)
            item.setCheckState(COL_NAME, Qt.CheckState.Checked if shown else Qt.CheckState.Unchecked)
            item.setIcon(COL_COLOR, swatch(self._pnp_colors.get(side, "#FFFFFF")))
            item.setToolTip(COL_COLOR, "Click to change marker color")
        odb = self._odb
        if odb is not None and odb.pads_shown is not None:
            item = QTreeWidgetItem(gi, ["Footprint pads (ODB++)"])
            item.setData(COL_NAME, ROLE_PNP, "pads")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(COL_NAME, Qt.CheckState.Checked if odb.pads_shown else Qt.CheckState.Unchecked)
            item.setToolTip(COL_NAME, "Outline each part's pads from the ODB++ (shown when zoomed in)")
        if odb is not None:
            self._note(gi, odb.label, "#A0A0A0", odb.tooltip)
            if odb.issues:
                lines = [f"{d}: {'; '.join(msgs)}" for d, msgs in sorted(odb.issues.items())]
                tip = "\n".join(lines[:40]) + (f"\n... and {len(lines) - 40} more" if len(lines) > 40 else "")
                n = len(odb.issues)
                self._note(gi, f"⚠ {n} part{'s' if n != 1 else ''} disagree with ODB++", "#FF5050", tip)
        if self._pnp_status:
            self._note(gi, self._pnp_status, "#E0A000")
        gi.setExpanded(True)

    @staticmethod
    def _note(parent: QTreeWidgetItem, text: str, color: str, tip: str = "") -> None:
        note = QTreeWidgetItem(parent, [text])
        note.setData(COL_NAME, ROLE_PNP, "note")
        note.setFlags(Qt.ItemFlag.ItemIsEnabled)
        note.setForeground(COL_NAME, QBrush(QColor(color)))
        if tip:
            note.setToolTip(COL_NAME, tip)

    def _add_layer_item(self, parent: QTreeWidgetItem, layer: Layer) -> None:
        item = QTreeWidgetItem(parent, [f"{layer.kind.label()}  —  {_short(layer.name)}"])
        item.setData(COL_NAME, ROLE_LAYER, layer)
        tip = [layer.name, f"Type: {layer.kind.label()} (from {layer.id_source})"]
        if layer.file_color:
            tip.append(f"Color from file: {layer.file_color}")
        if layer.error:
            tip.append(f"PARSE ERROR: {layer.error}")
        tip += [f"warning: {w}" for w in layer.warnings[:10]]
        item.setToolTip(COL_NAME, "\n".join(tip))

        if layer.parsed is None:
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setForeground(COL_NAME, QBrush(QColor("#808080")))
            if layer.error:
                item.setForeground(COL_NAME, QBrush(QColor("#E05050")))
            return

        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
        )
        item.setCheckState(COL_NAME, Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked)
        item.setIcon(COL_COLOR, swatch(layer.color))
        item.setToolTip(COL_COLOR, "Click to change color")

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(5, 100)
        slider.setValue(round(layer.opacity * 100))
        slider.setToolTip("Opacity")
        slider.setFixedHeight(16)
        slider.valueChanged.connect(lambda v, la=layer: self.opacityChanged.emit(la, v / 100))
        self.setItemWidget(item, COL_OPACITY, slider)

    # ---- interaction -------------------------------------------------------------------------

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != COL_NAME:
            return
        if item.data(COL_NAME, ROLE_PNP) == "pads":
            shown = item.checkState(COL_NAME) == Qt.CheckState.Checked
            self._odb.pads_shown = shown
            self.placementPadsShownChanged.emit(shown)
            return
        side = _pnp_side(item)
        if isinstance(side, Side):
            shown = item.checkState(COL_NAME) == Qt.CheckState.Checked
            self._pnp_shown[side] = shown
            self.placementShownChanged.emit(side, shown)
            return
        layer = item.data(COL_NAME, ROLE_LAYER)
        if layer is None:
            return  # group checkboxes propagate to children via auto-tristate
        visible = item.checkState(COL_NAME) == Qt.CheckState.Checked
        if visible != layer.visible:
            self.visibilityChanged.emit(layer, visible)

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        side = _pnp_side(item)
        if isinstance(side, Side) and column == COL_COLOR:
            c = QColorDialog.getColor(QColor(self._pnp_colors[side]), self, f"Marker color — {side.value}")
            if c.isValid():
                hexc = c.name().upper()
                self._pnp_colors[side] = hexc
                item.setIcon(COL_COLOR, swatch(hexc))
                self.placementColorChanged.emit(side, hexc)
            return
        layer = item.data(COL_NAME, ROLE_LAYER)
        if column != COL_COLOR or layer is None or layer.parsed is None:
            return
        c = QColorDialog.getColor(QColor(layer.color), self, f"Color — {layer.kind.label()}")
        if c.isValid():
            hexc = c.name().upper()
            item.setIcon(COL_COLOR, swatch(hexc))
            self.colorChanged.emit(layer, hexc)

    def refresh_layer(self, layer: Layer) -> None:
        """Re-sync swatch and checkbox for one layer without rebuilding."""
        self._updating = True
        it = self._find(layer)
        if it is not None:
            it.setIcon(COL_COLOR, swatch(layer.color))
            it.setCheckState(COL_NAME, Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked)
        self._updating = False

    def _find(self, layer: Layer) -> QTreeWidgetItem | None:
        for i in range(self.topLevelItemCount()):
            g = self.topLevelItem(i)
            for j in range(g.childCount()):
                if g.child(j).data(COL_NAME, ROLE_LAYER) is layer:
                    return g.child(j)
        return None

    def _context_menu(self, pos: QPoint) -> None:
        item = self.itemAt(pos)
        if item is not None and item.data(COL_NAME, ROLE_PNP) is not None:
            self._placement_menu(pos)
            return
        layer = item.data(COL_NAME, ROLE_LAYER) if item else None
        if layer is None or layer.parsed is None:
            return
        menu = QMenu(self)
        type_menu = menu.addMenu("Layer type")
        sub = {
            "Top": type_menu.addMenu("Top"),
            "Bottom": type_menu.addMenu("Bottom"),
            "Mid layers": type_menu.addMenu("Mid layers"),
            "Planes": type_menu.addMenu("Internal planes"),
            "Mechanical": type_menu.addMenu("Mechanical"),
        }
        for kind in assignable_kinds():
            if kind.side is Side.TOP:
                target = sub["Top"]
            elif kind.side is Side.BOTTOM:
                target = sub["Bottom"]
            elif kind.type is LayerType.COPPER:
                target = sub["Mid layers"]
            elif kind.type is LayerType.PLANE:
                target = sub["Planes"]
            elif kind.type is LayerType.MECHANICAL:
                target = sub["Mechanical"]
            else:
                target = type_menu
            act = QAction(kind.label(), target)
            act.setCheckable(True)
            act.setChecked(kind == layer.kind)
            act.triggered.connect(lambda _=False, k=kind, la=layer: self._reassign(la, k))
            target.addAction(act)
        menu.exec(self.viewport().mapToGlobal(pos))

    def _placement_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        sets = menu.addMenu("Coordinate columns")
        for name in self._pnp.coord_sets:
            act = QAction(f"{name} X/Y", sets)
            act.setCheckable(True)
            act.setChecked(name == self._pnp.coord_set)
            act.triggered.connect(lambda _=False, n=name: self.placementCoordSetRequested.emit(n))
            sets.addAction(act)
        sets.setEnabled(len(self._pnp.coord_sets) > 1)
        menu.addSeparator()
        menu.addAction("Load ODB++ Folder…", lambda: self.placementOdbRequested.emit("folder"))
        menu.addAction("Load ODB++ Archive (.tgz/.zip)…", lambda: self.placementOdbRequested.emit("archive"))
        menu.addSeparator()
        menu.addAction("Remove Pick && Place", self.placementRemoveRequested.emit)
        menu.exec(self.viewport().mapToGlobal(pos))

    def _reassign(self, layer: Layer, kind: LayerKind) -> None:
        if kind != layer.kind:
            self.kindChanged.emit(layer, kind)


def _pnp_side(item: QTreeWidgetItem) -> Side | None:
    v = item.data(COL_NAME, ROLE_PNP)
    return Side(v) if v in (Side.TOP.value, Side.BOTTOM.value) else None


def _sort_key(layer: Layer):
    return (-z_order(layer.kind), layer.name.lower())


def _short(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]
