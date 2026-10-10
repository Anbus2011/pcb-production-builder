# PanelCheck

PanelCheck is a Windows desktop tool for PCB assembly prep. It checks that the pick-and-place
(P&P) data from Altium Designer lines up with the Gerbers you actually get back from the board
house, before you build programs for the pick-and-place machine and the AOI.

Board houses often panelize, rename layers, and move the origin. PanelCheck lets you see the
problem instead of finding it on the line:

1. **Open the Gerbers:** Altium output, or a board house's panel (for example JLCPCB's CAM output).
   They render in Altium-like colors, with a layer tree on the left.
2. **Set Home:** Shift+click the board origin, usually a mounting hole. The cursor snaps to pad and
   hole centers.
3. **Open the Altium pick-and-place file.** A `+` appears at every part's centroid, cyan for top
   and orange for bottom.
4. **Zoom in to check alignment.** If an Altium ODB++ export sits next to the P&P file, each part
   also shows its footprint pads and a dot on **pin 1**. The P&P is cross-checked against the
   ODB++, and parts whose side, rotation or position disagree are ringed in red.

**Status:** the viewer, the P&P overlay, ODB++ footprints / pin 1, and the cross-check work. Still
to come: BOM import and parts table, multiple board instances on a panel, and exports for a
Philips/Assembleon pick-and-place machine and a Mirtec MV-2HTL AOI.

## Install

PanelCheck runs on 64-bit Windows 10 or 11. You can run the standalone build, or run it from
source.

### Standalone (no Python needed)

Copy the whole `panelcheck` folder (`panelcheck.exe` plus its `_internal` folder) anywhere, such
as a local folder, a USB stick or a network share, and run `panelcheck.exe`. Nothing is installed.
The .exe won't start without the `_internal` folder beside it. The build isn't code-signed, so
Windows SmartScreen may ask the first time ("More info" > "Run anyway").

To produce that folder yourself, follow *From source* below, then:

```
.venv\Scripts\pyinstaller panelcheck.spec
```

The result is `dist\panelcheck\`, about 110 MB. Copy the whole folder.

### From source

Requires [Python 3.12](https://www.python.org/downloads/) and git.

```
git clone https://github.com/Anbus2011/pcb-production-builder
cd pcb-production-builder
python -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]
```

The commands call the virtual environment's executables directly, so there's no need to activate
it. PowerShell blocks `Activate.ps1` under its default execution policy anyway.

## Run

```
.venv\Scripts\python -m panelcheck                         # then File > Open Gerber Folder / Zip
.venv\Scripts\python -m panelcheck path\to\gerber_folder   # open a folder (or .zip) directly
```

From the standalone build, double-click `panelcheck.exe`, or run `panelcheck.exe path\to\gerbers`.

## Quick start

1. **File > Open Gerber Folder** (Ctrl+O), or **Open Gerber Zip**, or drag the folder or .zip onto
   the window. The board opens showing the top side.
2. **Find the board origin,** the point Altium's coordinates start from (often a mounting hole).
   Hold **Shift** and hover it until the green ring appears, then **left-click** to set Home (0,0).
3. **File > Open Pick and Place** (Ctrl+P) and pick the Altium P&P .csv. If Home isn't set yet,
   PanelCheck asks you to set it first, and the parts appear as soon as you do.
4. **Zoom in** with the wheel. Each `+` should sit in the middle of its part's pads. Hover a `+` to
   see the part in the status bar.
5. Press **B** to check the bottom side and **T** to go back to the top.

For a panel, set Home on the origin of the board you want to check. Moving Home moves all the
markers.

## Controls

| Action | Control |
|---|---|
| Zoom at cursor | mouse wheel |
| Pan | right-drag, middle-drag, or hold Space and left-drag |
| Zoom to fit | F or Home |
| Top view | T: checks top layers and parts, unchecks bottom ones |
| Bottom view | B: checks bottom layers and parts, unchecks top ones, mirrored as seen from below |
| Mirror | M: flips the display left-right, layers unchanged |
| Units | View menu or toolbar (mm / mil) |
| Snap to pad / hole center | hold Shift and hover (green ring marks the target) |
| Set Home (0,0) | while snapped, left-click; Board > Clear Home removes it |
| Open pick-and-place | File > Open Pick and Place (Ctrl+P), or drop the .csv/.txt on the window |
| Cancel a P&P load waiting for Home | Esc |
| Part details | hover a `+` marker (shown in the status bar) |
| Layer visibility | checkbox in the tree (a group's checkbox toggles the whole group) |
| Layer color / opacity | click the swatch / drag the slider beside each layer |
| Override layer type | right-click a layer > Layer type |
| Show or recolor top / bottom markers | checkbox / swatch on the Pick & Place rows in the tree |
| Footprint pads on / off | *Footprint pads (ODB++)* checkbox in the tree |

## What it reads

- **Gerbers (RS-274X, X2) and Excellon drill files**, from a folder or a .zip:
  - Altium output, identified by extension (.GTL, .GBL, .G1, .GTO, .GTS, .GTP, .GM1, .TXT, …)
  - board-house panel output with extensionless ODB-style names (JLCPCB's `tl`, `bl`, `l2`, `drl`, `ko`, `vcut`, …)
  
  If the folder you open has no Gerbers but one subfolder does (a JLC delivery keeps them in
  `ok/`), that subfolder is opened. Clear-polarity areas, planes and slots render correctly.
  Hover a layer in the tree to see how its type was identified; right-click to override it.
- **Altium pick-and-place** (.csv or .txt): the column header row is found automatically below
  the preamble. Units come from the file (mm or mil). If the file has several centroid column
  sets (Center, Mid, Ref, Pad), you're asked which to use.
- **Altium ODB++ export** (job folder, .tgz or .zip), optional. It's found automatically in or
  below the P&P file's folder, or next to it. It supplies footprint pads, pin 1 and the
  cross-check. To use a different one, right-click the Pick & Place group > Load ODB++.

PanelCheck only reads these files; it never modifies them.

## Settings

Units, marker colors, the footprint-pads toggle and last-used folders are remembered per Windows
user, in the registry under `HKCU\Software\PanelCheck`.

## Development

### Headless snapshot

This renders the view to a PNG and exits, which is useful for checking a file set without the
GUI:

```
.venv\Scripts\python -m panelcheck path\to\gerbers --snapshot out.png [--bottom] [--layers .GTL,.GTO] [--size 1600x1200] [--zoom-mm X,Y,W,H] [--home X,Y --pnp FILE]
```

`--home X,Y` sets Home in Gerber mm, and `--pnp` overlays a pick-and-place file there.

### Test and lint

```
.venv\Scripts\python -m pytest
.venv\Scripts\ruff check . ; .venv\Scripts\ruff format .
```

Tests that need real board files skip themselves when `testdata/` is absent. `testdata/` holds
customer data and is gitignored, so it is never committed.

`CLAUDE.md` holds the design notes, file-format findings and coordinate conventions.
