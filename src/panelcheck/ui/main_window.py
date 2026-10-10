"""Main window: layer tree dock on the left, canvas in the centre, cursor coordinates in the status bar."""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
)

from panelcheck import __version__
from panelcheck.io.gerber_loader import artwork_layers, artwork_subfolders, folder_has_artwork, load_layers
from panelcheck.io.odb_components import OdbCheck, OdbError, OdbJob, check_parts, find_odb_for, load_odb
from panelcheck.io.pnp_altium import PnPError, looks_like_pnp, parse_pnp
from panelcheck.model.geometry import DisplayUnit, Transform2D, format_length, mm_to
from panelcheck.model.layers import BOTTOM_MARKER_COLOR, TOP_MARKER_COLOR, Layer, LayerKind, LayerType, Side
from panelcheck.model.placement import PnPFile, place
from panelcheck.model.snap import SnapIndex, SnapTarget
from panelcheck.ui.canvas import Canvas
from panelcheck.ui.layer_item import BoardItem, LayerEntry, RenderList
from panelcheck.ui.layer_tree import LayerTree, OdbRows
from panelcheck.ui.overlay_item import PlacementOverlay

APP_NAME = "PanelCheck"
PART_HOVER_PX = 8  # hover radius for showing a part's details in the status bar
_DEFAULT_MARKER_COLORS = {Side.TOP: TOP_MARKER_COLOR, Side.BOTTOM: BOTTOM_MARKER_COLOR}


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 900)
        self.settings = settings if settings is not None else QSettings(APP_NAME, APP_NAME)
        self.unit = DisplayUnit(self.settings.value("unit", DisplayUnit.MM.value))
        self.layers: list[Layer] = []
        self.source_path: Path | None = None
        self.snap_index = SnapIndex([])
        # Home (0,0) in board mm. Phase 3 generalizes this into per-instance origins.
        self.home: QPointF | None = None
        self._last_cursor: tuple[float, float] | None = None
        self.view_side = Side.TOP  # T / B; mirroring (M) is separate, see set_mirrored
        # Pick-and-place file. Its parts are placed relative to Home, so with no Home they wait.
        self.pnp: PnPFile | None = None
        self._pnp_pending = False  # loaded, waiting for the first Home pick; Esc cancels the load
        # ODB++ job found next to the P&P: footprint outlines, pin 1, and a cross-check of the P&P.
        self.odb: OdbJob | None = None
        self.odb_check: OdbCheck | None = None
        self._odb_search_note = ""

        self.canvas = Canvas(self)
        self.board = BoardItem(self.canvas)
        self.canvas.scene().addItem(self.board)
        self.overlay = PlacementOverlay()
        for side, default in _DEFAULT_MARKER_COLORS.items():
            self.overlay.set_color(side, str(self.settings.value(f"marker_color/{side.value}", default)))
        self.overlay.set_pads_shown(self.settings.value("show_footprint_pads", True, type=bool))
        self.canvas.scene().addItem(self.overlay)
        self.setCentralWidget(self.canvas)
        self.tree = LayerTree(self)
        dock = QDockWidget("Layers", self)
        dock.setObjectName("LayersDock")
        dock.setWidget(self.tree)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.resizeDocks([dock], [380], Qt.Orientation.Horizontal)

        self._build_actions()
        self._build_status_bar()

        self.canvas.set_snap_provider(
            lambda x, y, r: self.snap_index.nearest(x, y, r, near_side=self.view_side)
        )
        self.canvas.cursorMoved.connect(self._on_cursor)
        self.canvas.cursorLeft.connect(self._on_cursor_left)
        self.canvas.snapChanged.connect(self._on_snap)
        self.canvas.homePicked.connect(self._on_home_picked)
        self.canvas.hint.connect(lambda msg: self.statusBar().showMessage(msg, 6000))
        self.canvas.viewChanged.connect(self._update_view_status)
        self.tree.visibilityChanged.connect(self._on_visibility)
        self.tree.colorChanged.connect(self._on_color)
        self.tree.opacityChanged.connect(self._on_opacity)
        self.tree.kindChanged.connect(self._on_kind)
        self.tree.placementShownChanged.connect(self._on_marker_shown)
        self.tree.placementColorChanged.connect(self._on_marker_color)
        self.tree.placementCoordSetRequested.connect(
            lambda name: self.open_pnp(self.pnp.path, coord_set=name, prompt=False) if self.pnp else None
        )
        self.tree.placementRemoveRequested.connect(self.remove_pnp)
        self.tree.placementPadsShownChanged.connect(self._on_pads_shown)
        self.tree.placementOdbRequested.connect(self._on_odb_requested)
        self.setAcceptDrops(True)
        self._update_view_status()

    # ---- UI construction ---------------------------------------------------------------------

    def _build_actions(self) -> None:
        m_file = self.menuBar().addMenu("&File")
        self.act_open_dir = QAction("Open Gerber &Folder…", self, shortcut=QKeySequence("Ctrl+O"))
        self.act_open_dir.triggered.connect(self.open_folder_dialog)
        self.act_open_zip = QAction("Open Gerber &Zip…", self, shortcut=QKeySequence("Ctrl+Shift+O"))
        self.act_open_zip.triggered.connect(self.open_zip_dialog)
        self.act_open_pnp = QAction("Open &Pick and Place…", self, shortcut=QKeySequence("Ctrl+P"))
        self.act_open_pnp.triggered.connect(self.open_pnp_dialog)
        act_quit = QAction("E&xit", self, shortcut=QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        m_file.addActions([self.act_open_dir, self.act_open_zip])
        m_file.addSeparator()
        m_file.addAction(self.act_open_pnp)
        m_file.addSeparator()
        m_file.addAction(act_quit)

        m_view = self.menuBar().addMenu("&View")
        self.act_fit = QAction("Zoom to &Fit", self)
        self.act_fit.setShortcuts([QKeySequence("F"), QKeySequence("Home")])
        self.act_fit.triggered.connect(self.canvas.zoom_to_fit)

        side_group = QActionGroup(self)
        self.act_top = QAction("&Top View", self, checkable=True, checked=True, shortcut=QKeySequence("T"))
        self.act_bottom = QAction("&Bottom View", self, checkable=True, shortcut=QKeySequence("B"))
        for a in (self.act_top, self.act_bottom):
            side_group.addAction(a)
        self.act_top.setToolTip("Top view (T): show top layers and parts, hide bottom ones")
        self.act_bottom.setToolTip("Bottom view (B): show bottom layers and parts, hide top ones, mirrored")
        self.act_top.triggered.connect(lambda: self.set_view_side(Side.TOP))
        self.act_bottom.triggered.connect(lambda: self.set_view_side(Side.BOTTOM))
        self.act_mirror = QAction("&Mirror", self, checkable=True, shortcut=QKeySequence("M"))
        self.act_mirror.setToolTip("Mirror (M): flip the display left-right; layers are unchanged")
        self.act_mirror.triggered.connect(lambda checked: self.set_mirrored(checked))

        unit_group = QActionGroup(self)
        self.act_mm = QAction("Units: mm", self, checkable=True)
        self.act_mil = QAction("Units: mil", self, checkable=True)
        for a, u in ((self.act_mm, DisplayUnit.MM), (self.act_mil, DisplayUnit.MIL)):
            unit_group.addAction(a)
            a.setChecked(self.unit is u)
            a.triggered.connect(lambda _=False, u=u: self.set_unit(u))

        m_view.addAction(self.act_fit)
        m_view.addSeparator()
        m_view.addActions([self.act_top, self.act_bottom, self.act_mirror])
        m_view.addSeparator()
        m_view.addActions([self.act_mm, self.act_mil])
        m_view.addSeparator()
        m_view.addAction(self.findChild(QDockWidget).toggleViewAction())

        m_board = self.menuBar().addMenu("&Board")
        act_home_help = QAction("Set Home: hold Shift over a pad or hole, then left-click", self)
        act_home_help.setEnabled(False)
        self.act_clear_home = QAction("&Clear Home", self)
        self.act_clear_home.triggered.connect(lambda: self.set_home(None))
        self.act_clear_home.setEnabled(False)
        self.act_cancel_pnp = QAction("Cancel Pick and Place Load", self, shortcut=QKeySequence("Esc"))
        self.act_cancel_pnp.triggered.connect(self.remove_pnp)
        self.act_cancel_pnp.setEnabled(False)
        m_board.addActions([act_home_help, self.act_clear_home, self.act_cancel_pnp])

        tb = self.addToolBar("Main")
        tb.setObjectName("MainToolbar")
        tb.addActions([self.act_open_dir, self.act_open_zip, self.act_open_pnp])
        tb.addSeparator()
        tb.addAction(self.act_fit)
        tb.addSeparator()
        tb.addActions([self.act_top, self.act_bottom, self.act_mirror])
        tb.addSeparator()
        tb.addActions([self.act_mm, self.act_mil])

        m_help = self.menuBar().addMenu("&Help")
        act_about = QAction("&About", self)
        act_about.triggered.connect(
            lambda: QMessageBox.about(
                self,
                APP_NAME,
                f"{APP_NAME} {__version__}\nGerber + pick-and-place verification.\n\n"
                "Wheel: zoom at cursor   Right/middle-drag or Space+drag: pan\n"
                "F / Home: zoom to fit   T / B: top / bottom view (switches layers)   M: mirror\n"
                "Shift + hover: snap to pad/hole centre   Shift + left-click: set Home (0,0)\n"
                "Ctrl+P: open pick-and-place (placed relative to Home)   hover a + for part details",
            )
        )
        m_help.addAction(act_about)

    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        self.lbl_xy = QLabel()
        self.lbl_xy.setMinimumWidth(260)
        self.lbl_zoom = QLabel()
        self.lbl_side = QLabel()
        self.lbl_info = QLabel()
        self.lbl_snap = QLabel()
        self.lbl_snap.setStyleSheet("color: #00C000;")
        self.lbl_part = QLabel()
        self.lbl_home = QLabel("Home: not set")
        sb.addWidget(self.lbl_info, 1)
        sb.addPermanentWidget(self.lbl_part)
        sb.addPermanentWidget(self.lbl_snap)
        sb.addPermanentWidget(self.lbl_home)
        sb.addPermanentWidget(self.lbl_side)
        sb.addPermanentWidget(self.lbl_zoom)
        sb.addPermanentWidget(self.lbl_xy)

    # ---- loading -----------------------------------------------------------------------------

    def open_folder_dialog(self) -> None:
        start = self.settings.value("last_dir", "")
        d = QFileDialog.getExistingDirectory(self, "Open Gerber folder", start)
        if d:
            self.open_path(d)

    def open_zip_dialog(self) -> None:
        start = self.settings.value("last_dir", "")
        f, _ = QFileDialog.getOpenFileName(
            self, "Open Gerber zip", start, "Zip archives (*.zip);;All files (*)"
        )
        if f:
            self.open_path(f)

    def open_path(self, path: str | Path) -> bool:
        path = Path(path)
        note = ""
        if path.is_dir() and not folder_has_artwork(path):
            subs = artwork_subfolders(path)
            if not subs:
                QMessageBox.warning(self, APP_NAME, f"No Gerber or drill files found in {path}.")
                return False
            if len(subs) == 1:
                chosen = subs[0]
            else:
                names = [str(s.relative_to(path)) for s in subs]
                name, ok = QInputDialog.getItem(
                    self, APP_NAME, f"{path.name} has several Gerber folders. Open which?", names, 0, False
                )
                if not ok:
                    return False
                chosen = subs[names.index(name)]
            note = f"  (opened subfolder {chosen.relative_to(path)})"
            path = chosen
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            layers = load_layers(path)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, APP_NAME, f"Could not open {path}:\n{e}")
            return False
        try:
            self._set_layers(layers)
        finally:
            QApplication.restoreOverrideCursor()
        self.source_path = path
        self.settings.setValue("last_dir", str(path.parent if path.is_file() else path))
        self.setWindowTitle(f"{APP_NAME} — {path.name}")
        art = artwork_layers(layers)
        errors = [la for la in layers if la.error]
        msg = f"{len(art)} artwork layers, {len(layers) - len(art) - len(errors)} other files"
        if errors:
            msg += f", {len(errors)} failed to parse (hover in tree for details)"
        self.lbl_info.setText(msg + note)
        self.canvas.zoom_to_fit()
        return True

    def _set_layers(self, layers: list[Layer]) -> None:
        self.layers = layers
        if self.view_side is Side.BOTTOM:  # layers load top-side visible; match the current view
            for layer in layers:
                if layer.kind.side in (Side.TOP, Side.BOTTOM):
                    layer.visible = layer.kind.side is Side.BOTTOM
        art = artwork_layers(layers)
        render_lists = {id(la): RenderList.from_layer(la) for la in art if not la.kind.is_negative}
        extent = self._board_extent(art, render_lists)
        entries = []
        content = QRectF()
        for layer in art:
            rl = render_lists.get(id(layer)) or RenderList.from_layer(layer, extent)
            if rl.failed_objects:
                layer.warnings.append(f"{rl.failed_objects} objects could not be rendered")
            entries.append(LayerEntry(layer, rl))
            if not rl.bbox.isNull():
                content = content.united(rl.bbox)
        self.board.set_entries(entries)
        # Zoom-to-fit frames the board/panel outline; notes and drawings outside it (e.g. JLC's
        # V-cut dimension drawing) stay reachable by zooming out.
        fit = extent if extent is not None else content
        self.canvas.set_content_rect(fit if fit is not None and not fit.isNull() else QRectF(0, 0, 100, 100))
        self.tree.set_layers(layers)
        self.snap_index = SnapIndex.from_layers(art)
        self.set_home(None)

    @staticmethod
    def _board_extent(art: list[Layer], render_lists: dict[int, RenderList]) -> QRectF | None:
        """Board/panel extent: the outline layer's box (outline, then keep-out, then the lowest-numbered
        mechanical layer), else the union of all positive layers. Also fills negative plane layers."""
        rank = {LayerType.OUTLINE: 0, LayerType.KEEPOUT: 1, LayerType.MECHANICAL: 2}
        best: tuple | None = None
        union = QRectF()
        for layer in art:
            rl = render_lists.get(id(layer))
            if rl is None or rl.bbox.isNull():
                continue
            union = union.united(rl.bbox)
            if layer.kind.type in rank:
                key = (rank[layer.kind.type], layer.kind.index or 0)
                if best is None or key < best[0]:
                    best = (key, rl.bbox)
        if best is not None:
            return best[1]
        return None if union.isNull() else union

    # ---- pick and place ----------------------------------------------------------------------

    def open_pnp_dialog(self) -> None:
        if not self.layers:
            QMessageBox.information(
                self, APP_NAME, "Open the Gerbers first: the parts are placed relative to Home on the board."
            )
            return
        start = self.settings.value("last_pnp_dir", self.settings.value("last_dir", ""))
        f, _ = QFileDialog.getOpenFileName(
            self, "Open pick-and-place file", start, "Pick and place (*.csv *.txt);;All files (*)"
        )
        if f:
            self.open_pnp(f)

    def open_pnp(self, path: str | Path, coord_set: str | None = None, prompt: bool = True) -> bool:
        """Load a P&P file. Parts are placed at Home; if Home isn't set yet, ask the user to set it
        (``prompt``) and keep the parts waiting until they do."""
        path = Path(path)
        try:
            pnp = parse_pnp(path, coord_set)
        except (OSError, PnPError) as e:
            QMessageBox.critical(self, APP_NAME, f"Could not read pick-and-place file {path.name}:\n{e}")
            return False
        if prompt and coord_set is None and len(pnp.coord_sets) > 1:
            names = [f"{s} X/Y" for s in pnp.coord_sets]
            name, ok = QInputDialog.getItem(
                self, APP_NAME, f"{path.name} has several centroid columns. Use which?", names, 0, False
            )
            if not ok:
                return False
            chosen = pnp.coord_sets[names.index(name)]
            if chosen != pnp.coord_set:
                pnp = parse_pnp(path, chosen)
        if self.home is None and prompt and not self._ask_for_home(pnp):
            return False
        reload_same = self.pnp is not None and self.pnp.path == path
        self.pnp = pnp
        self._pnp_pending = self.home is None
        self.settings.setValue("last_pnp_dir", str(path.parent))
        if not reload_same:
            # A new file shows only the parts on the side being viewed, like the layers.
            self.overlay.set_side_shown(Side.TOP, self.view_side is Side.TOP)
            self.overlay.set_side_shown(Side.BOTTOM, self.view_side is Side.BOTTOM)
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self.odb, notes = find_odb_for({p.designator for p in pnp.parts}, path.parent)
            finally:
                QApplication.restoreOverrideCursor()
            self._odb_search_note = notes[0] if notes else ""
        self._check_odb()
        self._update_placement()
        msg = (
            f"Pick and place: {len(pnp.parts)} parts ({pnp.count(Side.TOP)} top, "
            f"{pnp.count(Side.BOTTOM)} bottom) from {path.name}, {pnp.coord_set} X/Y"
        )
        if pnp.warnings:
            msg += f", {len(pnp.warnings)} warnings (hover Pick & Place in the tree)"
        msg += f". {self._odb_summary()}"
        if self._pnp_pending:
            msg += ". Shift+click Home (0,0) to place them; Esc cancels."
        self.statusBar().showMessage(msg, 15000)
        return True

    def _ask_for_home(self, pnp: PnPFile) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"Set Home (0,0) to place the {len(pnp.parts)} parts from {pnp.path.name}.")
        box.setInformativeText(
            "Pick-and-place coordinates are relative to the board origin. Hold Shift over the pad or "
            "hole at the origin, then left-click it. The parts appear as soon as Home is set.\n\n"
            "Esc cancels the load while waiting."
        )
        set_home = box.addButton("Set Home", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(set_home)
        box.exec()
        return box.clickedButton() is set_home

    def remove_pnp(self) -> None:
        if self.pnp is None:
            return
        self.pnp = None
        self.odb = None
        self.odb_check = None
        self._pnp_pending = False
        self._update_placement()
        self.statusBar().showMessage("Pick and place removed", 4000)

    # ---- ODB++ (footprints, pin 1, cross-check) ----------------------------------------------

    def _check_odb(self) -> None:
        self.odb_check = check_parts(self.pnp.parts, self.odb) if self.pnp and self.odb else None

    def _odb_summary(self) -> str:
        if self.odb is None:
            return "No ODB++ found next to the P&P (right-click Pick & Place to load one)"
        ck = self.odb_check
        s = f"ODB++: {self.odb.path.name}, {ck.matched} parts matched"
        if ck.issues:
            s += f", {len(ck.issues)} disagree (red rings)"
        return s

    def _odb_rows(self):
        if self.pnp is None:
            return None
        if self.odb is None:
            return OdbRows("No ODB++ found (right-click to load)", self._odb_search_note)
        ck = self.odb_check
        tip = [str(self.odb.path), f"step {self.odb.step}, {len(self.odb.components)} components",
               f"{ck.matched} of {len(self.pnp.parts)} P&P parts matched"]  # fmt: skip
        tip += ck.notes + [f"warning: {w}" for w in self.odb.warnings]
        return OdbRows(
            f"ODB++: {self.odb.path.name} ({ck.matched} matched)",
            "\n".join(tip),
            pads_shown=self.overlay.pads_shown(),
            issues=ck.issues,
        )

    def _on_odb_requested(self, kind: str) -> None:
        start = str(self.pnp.path.parent) if self.pnp else self.settings.value("last_dir", "")
        if kind == "folder":
            p = QFileDialog.getExistingDirectory(self, "Open ODB++ job folder", start)
        else:
            p, _ = QFileDialog.getOpenFileName(
                self, "Open ODB++ archive", start, "ODB++ archives (*.tgz *.tar.gz *.zip);;All files (*)"
            )
        if p:
            self.open_odb(p)

    def open_odb(self, path: str | Path) -> bool:
        """Use a specific ODB++ job instead of the auto-detected one."""
        try:
            job = load_odb(path)
        except (OSError, OdbError, tarfile.TarError, zipfile.BadZipFile) as e:
            QMessageBox.critical(self, APP_NAME, f"Could not read ODB++ {Path(path).name}:\n{e}")
            return False
        if self.pnp is not None and not {p.designator for p in self.pnp.parts} & set(job.components):
            QMessageBox.warning(
                self, APP_NAME, f"None of the P&P designators are in {Path(path).name}; not using it."
            )
            return False
        self.odb = job
        self._check_odb()
        self._update_placement()
        self.statusBar().showMessage(self._odb_summary(), 10000)
        return True

    def _on_pads_shown(self, shown: bool) -> None:
        self.overlay.set_pads_shown(shown)
        self.settings.setValue("show_footprint_pads", shown)

    def _update_placement(self) -> None:
        """Re-place the P&P parts at Home and refresh the tree group."""
        if self.pnp is None or self.home is None:
            self.overlay.set_placed([])
        else:
            footprints = {}
            if self.odb is not None:
                footprints = {d: c.footprint for d, c in self.odb.components.items() if c.footprint}
            flagged = set(self.odb_check.issues) if self.odb_check else set()
            instances = [Transform2D(self.home.x(), self.home.y())]
            self.overlay.set_placed(place(self.pnp.parts, instances, footprints), flagged)
        self.lbl_part.setText("")
        status = ""
        if self.pnp is not None and self.home is None:
            status = "Set Home to place parts: Shift+click the board origin"
            if self._pnp_pending:
                status += " (Esc cancels)"
        self.act_cancel_pnp.setEnabled(self._pnp_pending)
        colors = {s: self.overlay.color(s) for s in _DEFAULT_MARKER_COLORS}
        shown = {s: self.overlay.side_shown(s) for s in _DEFAULT_MARKER_COLORS}
        self.tree.set_placement(self.pnp, colors, shown, status, self._odb_rows())

    def _on_marker_shown(self, side: Side, shown: bool) -> None:
        self.overlay.set_side_shown(side, shown)
        if self._last_cursor is not None:
            self._on_cursor(*self._last_cursor)

    def _on_marker_color(self, side: Side, color: str) -> None:
        self.overlay.set_color(side, color)
        self.settings.setValue(f"marker_color/{side.value}", color)

    # ---- layer changes from the tree ---------------------------------------------------------

    def _on_visibility(self, layer: Layer, visible: bool) -> None:
        layer.visible = visible
        self.board.invalidate()

    def _on_color(self, layer: Layer, color: str) -> None:
        layer.color = color
        self.board.invalidate()

    def _on_opacity(self, layer: Layer, opacity: float) -> None:
        layer.opacity = opacity
        self.board.invalidate()

    def _on_kind(self, layer: Layer, kind: LayerKind) -> None:
        was_negative = layer.kind.is_negative
        layer.reassign(kind)
        if was_negative != kind.is_negative:
            entries = self.board.entries()
            positive = {id(e.layer): e.render_list for e in entries if not e.layer.kind.is_negative}
            extent = self._board_extent([e.layer for e in entries], positive)
            self.board.replace_render_list(layer, RenderList.from_layer(layer, extent))
        else:
            self.board.resort()
        self.tree.rebuild()

    # ---- view --------------------------------------------------------------------------------

    def set_view_side(self, side: Side) -> None:
        """Top (T) or bottom (B) view: check that side's layers and parts, uncheck the other side's
        (inner, drill and mechanical layers keep their state), set draw order and snapping to
        prefer that side, and mirror the display for the bottom."""
        self.view_side = side
        other = Side.BOTTOM if side is Side.TOP else Side.TOP
        for layer in self.layers:
            if layer.kind.side is side:
                layer.visible = True
            elif layer.kind.side is other:
                layer.visible = False
        self.overlay.set_side_shown(side, True)
        self.overlay.set_side_shown(other, False)
        self.overlay.set_near_side(side)
        self.board.set_bottom_view(side is Side.BOTTOM)  # draw order, and drops the cached tiles
        (self.act_bottom if side is Side.BOTTOM else self.act_top).setChecked(True)
        self.set_mirrored(side is Side.BOTTOM)
        self._update_placement()  # rebuilds the tree, so its checkboxes follow
        if self._last_cursor is not None:
            self._on_cursor(*self._last_cursor)

    def set_mirrored(self, mirrored: bool) -> None:
        """Mirror (M): flip the display left-right without changing which layers are shown."""
        self.canvas.set_mirrored(mirrored)
        self.act_mirror.setChecked(mirrored)
        self._update_view_status()

    def set_unit(self, unit: DisplayUnit) -> None:
        self.unit = unit
        self.settings.setValue("unit", unit.value)
        self._update_view_status()
        self._update_home_label()
        if self._last_cursor is not None:
            self._on_cursor(*self._last_cursor)

    def _xy(self, x: float, y: float) -> str:
        return f"X {format_length(x, self.unit)}  Y {format_length(y, self.unit)}"

    def _on_cursor(self, x: float, y: float) -> None:
        self._last_cursor = (x, y)
        u = self.unit.value
        if self.home is None:
            self.lbl_xy.setText(f"{self._xy(x, y)}  {u}")
        else:
            rel = self._xy(x - self.home.x(), y - self.home.y())
            self.lbl_xy.setText(f"{rel}  {u} from Home   (abs {self._xy(x, y)})")
        hit = self.overlay.nearest(x, y, PART_HOVER_PX / self.canvas.px_per_mm)
        self.overlay.set_highlight(hit)
        if hit is None:
            self.lbl_part.setText("")
        else:
            p = hit.part
            text = f"{p.describe()}  @ {self._xy(p.x, p.y)} {u}"
            problems = self.odb_check.issues.get(p.designator) if self.odb_check else None
            if problems:
                text += "   ⚠ " + "; ".join(problems)
            self.lbl_part.setText(text)
            self.lbl_part.setStyleSheet("color: #FF5050;" if problems else "")
            self.lbl_part.setToolTip(p.description)

    def _on_cursor_left(self) -> None:
        self._last_cursor = None
        self.lbl_xy.setText("")
        self.lbl_part.setText("")
        self.overlay.set_highlight(None)

    def _on_snap(self, target: SnapTarget | None) -> None:
        self.lbl_snap.setText(f"Snap: {target.describe()}" if target is not None else "")

    # ---- home --------------------------------------------------------------------------------

    def _on_home_picked(self, x: float, y: float, target: SnapTarget) -> None:
        self.set_home(QPointF(x, y))
        msg = f"Home (0,0) set at {target.describe()}"
        if self.pnp is not None:
            msg += f"; placed {len(self.pnp.parts)} parts from {self.pnp.path.name}"
        self.statusBar().showMessage(msg, 8000)

    def set_home(self, point: QPointF | None) -> None:
        self.home = QPointF(point) if point is not None else None
        if self.home is not None:
            self._pnp_pending = False
        self.canvas.set_home(self.home)
        self.act_clear_home.setEnabled(self.home is not None)
        self._update_home_label()
        self._update_placement()
        if self._last_cursor is not None:
            self._on_cursor(*self._last_cursor)

    def _update_home_label(self) -> None:
        if self.home is None:
            self.lbl_home.setText("Home: not set")
        else:
            self.lbl_home.setText(f"Home @ {self._xy(self.home.x(), self.home.y())} {self.unit.value}")

    def _update_view_status(self) -> None:
        bottom = self.view_side is Side.BOTTOM
        text = "BOTTOM VIEW" if bottom else "Top view"
        if self.canvas.mirrored != bottom:  # M toggled away from the side's natural orientation
            text += " (mirrored)" if self.canvas.mirrored else " (not mirrored)"
        self.lbl_side.setText(text)
        self.lbl_side.setStyleSheet("color: #FF8C00; font-weight: bold;" if bottom else "")
        px_per_unit = self.canvas.px_per_mm / mm_to(1.0, self.unit)
        self.lbl_zoom.setText(f"{px_per_unit:.3g} px/{self.unit.value}")

    # ---- drag & drop -------------------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if not urls:
            return
        path = Path(urls[0].toLocalFile())
        # Altium drill files are .TXT too, so route by content, not extension.
        if path.is_file() and path.suffix.lower() in (".csv", ".txt") and looks_like_pnp(path):
            if self.layers:
                self.open_pnp(path)
            else:
                QMessageBox.information(self, APP_NAME, "Open the Gerbers first, then the pick-and-place.")
            return
        self.open_path(path)
