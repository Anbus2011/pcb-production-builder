"""Application entry point.

python -m panelcheck [PATH]                       open a Gerber folder or .zip
python -m panelcheck PATH --snapshot out.png      render the view to PNG and exit
    [--bottom] [--layers GTL,GBL] [--size 1600x1200] [--zoom-mm X,Y,W,H]
    [--home X,Y] [--pnp FILE]                     place pick-and-place "+" markers at Home
"""

from __future__ import annotations

import argparse
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtWidgets import QApplication

from panelcheck import __version__
from panelcheck.model.layers import Side
from panelcheck.ui.main_window import APP_NAME, MainWindow


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="panelcheck", description="Gerber + pick-and-place verification")
    ap.add_argument("path", nargs="?", help="Gerber folder or .zip to open")
    ap.add_argument("--snapshot", metavar="PNG", help="render the canvas to a PNG and exit")
    ap.add_argument("--bottom", action="store_true", help="bottom view: bottom layers/parts, mirrored")
    ap.add_argument("--layers", help="comma-separated file-name suffixes to show (others hidden)")
    ap.add_argument("--size", default="1600x1200", help="snapshot size WxH")
    ap.add_argument("--zoom-mm", help="snapshot region X,Y,W,H in board mm instead of zoom-to-fit")
    ap.add_argument("--home", help="set Home (0,0) at X,Y board/panel mm")
    ap.add_argument("--pnp", metavar="FILE", help="load a pick-and-place file (placed at --home)")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)

    win = MainWindow()
    if args.snapshot:
        w, h = (int(v) for v in args.size.lower().split("x"))
        win.canvas.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        win.canvas.setParent(None)
        win.canvas.resize(w, h)
        win.canvas.show()
    else:
        win.show()
    app.processEvents()

    if args.path and not win.open_path(args.path):
        return 1
    if args.bottom:
        win.set_view_side(Side.BOTTOM)
    if args.layers:
        wanted = [s.strip().lower() for s in args.layers.split(",") if s.strip()]
        for layer in win.layers:
            layer.visible = any(layer.name.lower().endswith(s) for s in wanted)
            win.tree.refresh_layer(layer)
        win.board.invalidate()
    if args.home:
        hx, hy = (float(v) for v in args.home.split(","))
        win.set_home(QPointF(hx, hy))
    if args.pnp and not win.open_pnp(args.pnp, prompt=not args.snapshot):
        return 1

    if args.snapshot:
        if args.zoom_mm:
            x, y, zw, zh = (float(v) for v in args.zoom_mm.split(","))
            win.canvas.set_content_rect(QRectF(x, y, zw, zh))
        win.canvas.zoom_to_fit()
        app.processEvents()
        ok = win.canvas.grab().save(args.snapshot)
        print(f"snapshot {'saved to' if ok else 'FAILED:'} {args.snapshot}")
        return 0 if ok else 1
    return app.exec()
