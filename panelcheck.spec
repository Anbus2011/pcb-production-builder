# PyInstaller spec for PanelCheck. Build with:  pyinstaller panelcheck.spec
# Output: dist\panelcheck\panelcheck.exe  (--onedir: faster start, fewer antivirus false positives)
# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import copy_metadata

# gerbonara/__init__.py calls importlib.metadata.version("gerbonara"), so its dist-info must ship.
datas = copy_metadata("gerbonara")

# gerbonara's CLI and web preview server pull these in; the app never imports them.
excludes = [
    "quart", "click", "rtree", "hypercorn", "werkzeug", "flask", "jinja2",
    "gerbonara.cli", "gerbonara.__main__", "gerbonara.cad.protoserve",
    "tkinter", "matplotlib", "numpy", "PIL", "IPython", "pytest",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtNetwork", "PySide6.QtPdf",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
    "PySide6.QtWebSockets", "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
]  # fmt: skip

a = Analysis(
    ["src/panelcheck/__main__.py"],
    pathex=["src"],
    datas=datas,
    hiddenimports=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="panelcheck",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="panelcheck", upx=False)
