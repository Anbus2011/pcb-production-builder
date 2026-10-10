import pytest
from PySide6.QtCore import QPointF, QRectF, QSettings, Qt
from PySide6.QtGui import QColor

from conftest import MC60A, needs_testdata, write_odb
from panelcheck.model.layers import TOP_MARKER_COLOR, Side
from panelcheck.ui.layer_tree import ROLE_LAYER, ROLE_PNP
from panelcheck.ui.main_window import MainWindow

PNP = (
    "Units used: mm\n"
    "Designator,Comment,Layer,Footprint,Center-X(mm),Center-Y(mm),Rotation\n"
    "C1,0.1uF,TopLayer,0603,10,20,0\n"
    "R1,10K,BottomLayer,0603,30,5,90\n"
)
# ODB++ matching PNP: C1 top at rot 0 (pin 1 at -0.6985 X from the centroid); R1 bottom, P&P 90°
# is ODB 270, pins along +Y after the bottom mirror.
GOOD_ODB = [("top", "C1", 0, 9.3015, 20, 0), ("bot", "R1", 0, 30, 4.3015, 270)]


@pytest.fixture
def pnp_path(tmp_path):
    # Nested two deep so the ODB++ search (P&P folder, then its parent) stays inside tmp_path.
    d = tmp_path / "proj" / "out"
    d.mkdir(parents=True)
    (d / "pnp.csv").write_text(PNP)
    return d / "pnp.csv"


@pytest.fixture
def win(qapp, tmp_path):
    # A throwaway INI keeps tests away from the user's real settings (and theirs out of the tests).
    w = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    w.resize(1000, 800)
    w.show()
    qapp.processEvents()
    w.canvas.set_content_rect(QRectF(0, 0, 100, 60))
    w.canvas.zoom_to_fit()
    yield w
    w.close()


def _pnp_rows(win):
    g = win.tree.topLevelItem(0)
    if g is None or g.data(0, ROLE_PNP) != "group":
        return None
    return [g.child(i) for i in range(g.childCount())]


def _row(win, start):
    return next((r for r in _pnp_rows(win) if r.text(0).startswith(start)), None)


def test_parts_wait_for_home_then_place(win, pnp_path):
    assert win.open_pnp(pnp_path, prompt=False)
    assert win.overlay.placed() == []
    rows = _pnp_rows(win)
    assert [r.text(0) for r in rows[:2]] == ["Top parts (1)", "Bottom parts (1)"]
    assert _row(win, "Set Home") is not None
    assert _row(win, "No ODB++ found") is not None
    assert win.act_cancel_pnp.isEnabled()

    win.set_home(QPointF(5, 7))
    placed = {p.part.designator: (p.x, p.y) for p in win.overlay.placed()}
    assert placed == {"C1": (15, 27), "R1": (35, 12)}
    assert _row(win, "Set Home") is None  # status note gone
    assert not win.act_cancel_pnp.isEnabled()

    win.set_home(None)  # clearing Home hides the markers but keeps the file
    assert win.overlay.placed() == [] and win.pnp is not None


def test_new_pnp_shows_top_parts_only(win, pnp_path):
    win.set_home(QPointF(0, 0))
    win.open_pnp(pnp_path, prompt=False)
    top, bottom = _pnp_rows(win)[:2]
    assert top.checkState(0) == Qt.CheckState.Checked and win.overlay.side_shown(Side.TOP)
    assert bottom.checkState(0) == Qt.CheckState.Unchecked and not win.overlay.side_shown(Side.BOTTOM)
    assert win.overlay.nearest(30, 5, 1.0) is None  # R1 (bottom) isn't hoverable while hidden

    bottom.setCheckState(0, Qt.CheckState.Checked)
    win.open_pnp(pnp_path, coord_set="Center", prompt=False)  # same file again keeps the choice
    assert win.overlay.side_shown(Side.BOTTOM)
    other = pnp_path.with_name("other.csv")
    other.write_text(pnp_path.read_text())
    win.open_pnp(other, prompt=False)  # a new file resets to top only
    assert not win.overlay.side_shown(Side.BOTTOM)


def test_bottom_view_shows_bottom_parts_and_new_file_follows_view(win, pnp_path):
    win.set_home(QPointF(0, 0))
    win.open_pnp(pnp_path, prompt=False)
    win.act_bottom.trigger()
    assert win.overlay.side_shown(Side.BOTTOM) and not win.overlay.side_shown(Side.TOP)
    top, bottom = _pnp_rows(win)[:2]
    assert top.checkState(0) == Qt.CheckState.Unchecked and bottom.checkState(0) == Qt.CheckState.Checked
    assert win.overlay.nearest(30, 5, 1.0).part.designator == "R1"
    other = pnp_path.with_name("other.csv")
    other.write_text(pnp_path.read_text())
    win.open_pnp(other, prompt=False)  # opened in bottom view: bottom parts only
    assert win.overlay.side_shown(Side.BOTTOM) and not win.overlay.side_shown(Side.TOP)


@needs_testdata
def test_top_bottom_mirror_keys(win):
    def shown():
        return {la.kind.side for la in win.layers if la.visible and la.kind.is_artwork}

    def tree_checked(name):
        for i in range(win.tree.topLevelItemCount()):
            g = win.tree.topLevelItem(i)
            for j in range(g.childCount()):
                la = g.child(j).data(0, ROLE_LAYER)
                if la is not None and la.name.endswith(name):
                    return g.child(j).checkState(0) == Qt.CheckState.Checked
        raise KeyError(name)

    win.open_path(MC60A)
    assert shown() == {Side.TOP, Side.NONE} and not win.canvas.mirrored

    win.act_bottom.trigger()
    assert win.view_side is Side.BOTTOM and win.canvas.mirrored and win.act_mirror.isChecked()
    assert shown() == {Side.BOTTOM, Side.NONE}  # inner layers stay as they were (unchecked)
    assert tree_checked(".GBL") and not tree_checked(".GTL") and tree_checked("RoundHoles.TXT")
    assert win.lbl_side.text() == "BOTTOM VIEW"

    win.act_mirror.trigger()  # M: unmirror, layers unchanged
    assert not win.canvas.mirrored and shown() == {Side.BOTTOM, Side.NONE}
    assert win.lbl_side.text() == "BOTTOM VIEW (not mirrored)"

    g1 = next(la for la in win.layers if la.name.endswith(".G1"))
    g1.visible = True  # the user turns an inner layer on; T/B leave it alone
    win.act_top.trigger()
    assert win.view_side is Side.TOP and not win.canvas.mirrored
    assert shown() == {Side.TOP, Side.INNER, Side.NONE}
    assert win.lbl_side.text() == "Top view"
    win.act_mirror.trigger()
    assert win.lbl_side.text() == "Top view (mirrored)"

    win.act_bottom.trigger()
    win.open_path(MC60A)  # new Gerbers in bottom view open bottom-side visible
    assert shown() == {Side.BOTTOM, Side.NONE}


def test_prompt_cancel_does_not_load(win, pnp_path, monkeypatch):
    monkeypatch.setattr(win, "_ask_for_home", lambda pnp: False)
    assert not win.open_pnp(pnp_path)
    assert win.pnp is None and _pnp_rows(win) is None


def test_no_prompt_when_home_set(win, pnp_path, monkeypatch):
    monkeypatch.setattr(win, "_ask_for_home", lambda pnp: pytest.fail("should not prompt"))
    win.set_home(QPointF(0, 0))
    assert win.open_pnp(pnp_path)
    assert len(win.overlay.placed()) == 2


def test_cancel_while_waiting_removes(win, pnp_path):
    win.open_pnp(pnp_path, prompt=False)
    win.act_cancel_pnp.trigger()
    assert win.pnp is None and _pnp_rows(win) is None


def test_marker_drawn_hover_and_side_toggle(win, pnp_path, qapp):
    win.set_home(QPointF(0, 0))
    win.open_pnp(pnp_path, prompt=False)
    qapp.processEvents()

    def cyan_near(x, y):
        img = win.canvas.grab().toImage()
        c = win.canvas.mapFromScene(QPointF(x, y))
        return any(
            img.pixelColor(c.x() + dx, c.y() + dy) == QColor(TOP_MARKER_COLOR)
            for dx in range(-3, 4)
            for dy in range(-3, 4)
        )

    assert cyan_near(10, 20)
    win._on_cursor(10.1, 20.1)
    assert win.lbl_part.text().startswith("C1")
    win._on_cursor(50, 50)
    assert win.lbl_part.text() == ""

    _pnp_rows(win)[0].setCheckState(0, Qt.CheckState.Unchecked)  # hide top parts
    assert not win.overlay.side_shown(Side.TOP)
    assert not cyan_near(10, 20)
    assert win.overlay.nearest(10, 20, 1.0) is None


# ---- ODB++ next to the P&P -----------------------------------------------------------------------


def test_odb_auto_detected_in_sibling_folder(win, pnp_path):
    write_odb(pnp_path.parent.parent / "odb", GOOD_ODB)  # proj/odb, beside proj/out/pnp.csv
    win.set_home(QPointF(0, 0))
    assert win.open_pnp(pnp_path, prompt=False)
    assert win.odb is not None and win.odb.path.name == "odb"
    assert win.odb_check.matched == 2 and win.odb_check.issues == {}
    by = {p.part.designator: p for p in win.overlay.placed()}
    assert by["C1"].footprint.pin1 == pytest.approx((9.3015, 20))
    assert by["R1"].footprint.pin1 == pytest.approx((30, 4.3015))
    assert _row(win, "ODB++: odb (2 matched)") is not None

    pads = _row(win, "Footprint pads")
    assert pads.checkState(0) == Qt.CheckState.Checked and win.overlay.pads_shown()
    pads.setCheckState(0, Qt.CheckState.Unchecked)
    assert not win.overlay.pads_shown()
    assert win.settings.value("show_footprint_pads", type=bool) is False


def test_odb_follows_home(win, pnp_path):
    write_odb(pnp_path.parent / "odb", GOOD_ODB)
    win.open_pnp(pnp_path, prompt=False)
    win.set_home(QPointF(100, 50))
    c1 = next(p for p in win.overlay.placed() if p.part.designator == "C1")
    assert c1.footprint.pin1 == pytest.approx((109.3015, 70))


def test_odb_rotation_mismatch_is_flagged(win, pnp_path):
    bad = [("top", "C1", 0, 9.3015, 20, 90), GOOD_ODB[1]]  # ODB says C1 is at P&P 270°
    write_odb(pnp_path.parent / "odb", bad)
    win.set_home(QPointF(0, 0))
    win.open_pnp(pnp_path, prompt=False)
    assert list(win.odb_check.issues) == ["C1"]
    assert "rotation 0° in P&P, 270° in ODB++" in win.odb_check.issues["C1"][0]
    assert _row(win, "⚠ 1 part disagree") is not None
    win._on_cursor(10, 20)
    assert "⚠ rotation" in win.lbl_part.text()


def test_footprint_outline_drawn_when_zoomed(win, pnp_path, qapp):
    write_odb(pnp_path.parent / "odb", GOOD_ODB)
    win.set_home(QPointF(0, 0))
    win.open_pnp(pnp_path, prompt=False)
    win.canvas.set_content_rect(QRectF(8, 18, 4, 4))  # ~200 px/mm around C1
    win.canvas.zoom_to_fit()
    qapp.processEvents()
    img = win.canvas.grab().toImage()

    def cyanish(x, y):  # pads are drawn semi-transparent and antialiased: close to cyan, not equal
        c = win.canvas.mapFromScene(QPointF(x, y))
        px = [img.pixelColor(c.x() + dx, c.y()) for dx in range(-2, 3)]
        return any(p.red() < 80 and p.green() > 100 and p.blue() > 100 for p in px)

    # Pin 2's pad outer edge is at x = 9.3015 + 1.778, the inner edge at 9.3015 + 1.016.
    assert cyanish(9.3015 + 1.016, 20.2)
    assert not cyanish(9.3015 + 1.4, 20.2)  # inside the pad: nothing drawn
    win.overlay.set_pads_shown(False)
    qapp.processEvents()
    img = win.canvas.grab().toImage()
    assert not cyanish(9.3015 + 1.016, 20.2)
