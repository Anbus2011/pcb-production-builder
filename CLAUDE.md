# PanelCheck — Gerber + Pick-and-Place Overlay Tool

## What this is

A Windows desktop app for verifying that pick-and-place (P&P) data from Altium lines up with the actual
board-house panel Gerbers before generating machine programs.

The workflow:

1. Open the board-house Gerber set for a **panel** (e.g. 2×2 RoboClaw boards).
2. View the layers, colored like Altium, in a tree + canvas UI.
3. Import the Altium **P&P file** and **BOM** for the *single* board.
4. For each board in the panel, click its home point (0,0), or register it with reference features.
5. The app overlays a `+` at every component centroid, for every board instance, so the user can see whether the data aligns.
6. Later phases add exports for the shop's machines:
   - a Philips/Assembleon pick-and-place program file
   - a Mirtec MV-2HTL AOI program

The user is DK, an experienced PCB and embedded engineer. He doesn't need beginner-level explanations.

## Working rules

- **Build one phase at a time** (see Phases below). Finish a phase, give DK run instructions, and stop so he can test with real files. Don't start the next phase without his go-ahead.
- **Never modify input files.** Gerbers, P&P files, and BOMs are read-only inputs.
- **Ask before overwriting** any existing file outside the generated build folders. **Never delete files** unless DK asks.
- **Never invent machine file formats.** For the Philips/Assembleon and Mirtec exports, ask DK for sample program files and reverse-engineer from those.
- **Verify real behavior against real files.** Where this doc says "verify", check against DK's actual Altium output rather than assuming.
- **Check library APIs against the installed version.** If gerbonara's API differs from what you expect, read its source or docs; don't guess.
- **Keep customer/proprietary data out of git.** `testdata/` is gitignored unless DK says otherwise.

## Current status (as of 2026-10-10)

**Built and tried by DK:**
- **Phase 1 viewer.** Layer tree, tiled renderer, zoom, pan, and top/bottom view.
- **Additions DK asked for after Phase 1:**
  - right-drag pan
  - Shift-hover snapping to pad and hole centers, with a drawn "+" cursor and green full-view crosshair
  - Shift + left-click to set a single Home (0,0); see "Snapping" below
  - opening JLC board-house panel output; see the JLC panel findings below
- **P&P overlay (2026-10-10, DK asked for this ahead of the rest of Phase 2):** see "Pick-and-place overlay (as built)" below. DK tried it and approved it.
- **ODB++ footprints, pin 1 and cross-check (2026-10-10):** see "ODB++ footprints (as built)" below. Built and tested headless; DK hasn't tried it yet.
- **Top-side defaults (2026-10-10, DK):**
  - When Gerbers load, top, drill and mechanical layers start checked. Bottom and inner layers (including planes) start unchecked; see `LayerKind.hidden_by_default`.
  - A newly opened P&P shows top parts only. Reloading the same file keeps the user's checkboxes.
  - Snapping only targets visible layers, so bottom pads don't snap until the bottom layers are checked. Holes always do.
- **View keys (2026-10-10, DK):** the viewed side (`MainWindow.view_side`) and the display mirror (`Canvas.mirrored`) are separate states.
  - **T** checks the top layers and parts, unchecks the bottom ones, and unmirrors. **B** does the reverse and mirrors. Inner, drill and mechanical layers keep their state.
  - The side also sets the draw order (`BoardItem.set_bottom_view`), the near side for snapping, and the overlay's near side (`set_near_side`; footprints are drawn for that side only).
  - **M** toggles only the mirror; the status bar says "(mirrored)" / "(not mirrored)" when it differs from the side's natural orientation.
  - F stays zoom-to-fit; V (the old flip) was removed.
  - New Gerbers or a new P&P opened in bottom view start bottom-side visible.
- 139 tests pass, and `dist\panelcheck\panelcheck.exe` builds and runs.

**Waiting on DK:**
- **GitHub repo:** https://github.com/Anbus2011/pcb-production-builder, branch `main`, first pushed 2026-10-10. **It's public**, so anything committed is published. Keep `testdata/`, `dist/` and `build/` out (they're gitignored), and don't paste real board data into code or docs beyond what's needed.
- **Rest of Phase 2:** BOM import, parts table, and the mismatch report aren't started. They need DK's go-ahead.

**Open questions for DK:**
- Phase 3: import panel instances from the ODB++ step-repeat?
- Should paste come from the ODB++ (the JLC panel has no paste Gerbers)?
- Is `sk` really via plugging?
- Is the drill color OK? Altium's file color is teal #009190.

**Working notes for agents:**
- **Run commands with the venv's executables** (`.venv\Scripts\python …`). DK's PowerShell blocks `Activate.ps1`.
- **Check rendering headless.** Set `QT_QPA_PLATFORM=offscreen` and use `--snapshot` with `--layers`/`--zoom-mm`, then look at the PNG. Offscreen renders show no fonts.
- **Write file contents with the file tools, not bash heredocs.** In this environment, heredocs containing quotes or backslashes have broken or mangled files several times, including a Python-in-heredoc patch script that turned `"\\n"` into a literal newline.
- **Tests must not touch DK's real settings.** `MainWindow(settings)` takes a `QSettings`; tests pass an INI in `tmp_path`. The default is the registry key `HKCU\Software\PanelCheck`.
- **Qt item data turns a `StrEnum` into a plain `str`.** Store `.value` and convert back (see `_pnp_side` in `layer_tree.py`).

## Real test files (already in the project folder)

DK placed these next to this CLAUDE.md:

- `Pick Place for MC60A_HV_V8E.csv` — the Altium pick-and-place export for the MC60A_HV board
- `MC60A_HV.xlsx` — the Altium BOM, in Excel format
- `Gerber_MC60A_HV/` — the Gerber + Excellon output

Before writing any code:

1. Move all three into `testdata/`, and tell DK you moved them. The .gitignore must cover `testdata/`.
2. Inspect each one and **report what you find** before writing the parsers:
   - the P&P preamble, column names, units, and Layer values
   - the BOM sheet name, header row, columns, how designators are grouped, and any DNP marking
   - the Gerber file list and extensions, whether X2 attributes are present, the coordinate format/units, and whether it's a single board or a panel
3. If what you find contradicts the domain notes below, the real files win; update this CLAUDE.md to match.

If the Gerbers are a single board rather than a board-house panel, Phase 3 can still be tested: create one instance at 0,0, and simulate a panel with several instances at offsets and rotations. Ask DK for a real board-house panel when one is available.

### Findings from the MC60A_HV test set (inspected 2026-10-09)

- All files were already in `testdata/`. An identical copy of the P&P CSV also sits inside `Gerber_MC60A_HV/`; the loader must skip non-Gerber files there.
- **Single board, not a panel.** 4-layer (GTL, G1, G2, GBL), no internal planes (.GPx). Outline on GM1 is 85.98 × 99.82 mm, from (−3.683, −3.302) to (82.296, 96.520) mm: the board origin is *inside* the outline, not at a corner. The GM1 rectangle is **not closed**: there's a 0.127 mm (5 mil) gap at the top-left corner, so outline vertex snapping/closing must tolerate gaps.
- NC drill was added later as `MC60A_HV_V8E-RoundHoles.TXT` and `-SlotHoles.TXT` (plus `.DRR`/`.LDP` reports). Both use Excellon `INCH,LZ` 2:5. That means leading zeros are kept and trailing zeros dropped, so `Y0083` is 0.83 in, and gerbonara reports `zeros=trailing`. Plated and non-plated tools are in one file, separated by `;TYPE=PLATED` / `;TYPE=NON_PLATED` comments, and gerbonara sets `tool.plated` from them. There are 732 round holes (5 NPTH at 3.048 mm) and 4 routed slots (`G00…M15 G01…M16`) for the micro-USB shell at CN3, matching the DRR. Every plated hole lands within 0.000 mm of a GTL flash.
- The drill files' `;Layer_Color=9474304` decodes to `#009190` (BGR).
- Gerbers: Altium Designer 22.6.1, `%FSLAX25Y25*%`, `%MOIN*%` (inches, 2.5). X2 attributes appear **only as `G04 #@! TF.…` comments**: `GenerationSoftware`, `SameCoordinates`, `FilePolarity`. **No `TF.FileFunction`**, so identification falls back to the extension. Solder masks (GTS/GBS) carry `FilePolarity,Negative`; render them as openings anyway.
- Clear polarity is real here: `%LPC*%` blocks on GTL, G1, G2, GBL, GTO and GBO (e.g. GTL has 134 clear regions). Aperture macros are only Altium's rounded-rectangle `%AMROUNDEDRECTDnn*%`. No `%SR` step-repeat.
- Each Gerber has a `G04 Layer_Color=<int>*` comment, which is Altium's actual layer color as a **BGR** integer (GTL 255 → red, GBL 16711680 → blue). Use it as the default color when present, and fall back to the table below.
- gerbonara 1.6.3 `LayerStack.open()` identifies all 11 layers correctly from the extensions, in about 0.7 s.

### Findings from Altium's ODB++ export `testdata/odb` (inspected 2026-10-10)

- **Source and layout:** an unpacked folder (not .tgz) written by AD 22.6.1, ODB++ 8.0, `UNITS=MM`, one step `pcb`, datum 0,0. It's the same design as the V8E P&P.
  - `steps/pcb/layers/comp_+_top|comp_+_bot/components` has 96 top and 83 bottom components. The 5 extra bottom ones are net ties N1–N5, which aren't in the P&P. Every P&P designator is present.
  - `.comp_mount_type` is 1 = SMT (166 parts) or 2 = TH (13). The `features` files of the component layers are empty.
- **`CMP <pkg_idx> x y rot mirror designator comment ;attrs`**, then `PRP` lines (BOM fields such as DigiKey) and one `TOP <idx> x y rot mirror net subnet DES-PIN` line per pin, in absolute board mm. The CMP x,y is the **footprint reference point**, not the centroid: it equals the P&P Center on only 77 of 174 parts.
- **ODB rotation vs P&P rotation** (all 174 parts consistent):
  - top: `odb = (−pnp) mod 360`, i.e. ODB is clockwise
  - bottom: `odb = (pnp + 180) mod 360`
  - The mirror flag is `N` on every part, bottom included.
- **Package-to-board transform** (all 179 components, ≤ 0.01 mm against the TOP pin lines):
  - top: `board = ref + R(−rot)·local`
  - bottom: `board = ref + R(+rot)·(−local_x, local_y)`
- **`eda/data` packages.** It holds 34 `PKG name pitch xmin ymin xmax ymax` records, each followed by its outline and then `PIN name type x y …` records carrying the pad contours, in package-local mm.
  - 30 packages give their outline as `RC`, which is just the **pad bounding box**, not the body.
  - 4 give a `CT` contour that is the real body: `44A_N`, `846A-02_N`, `SC70-5` (1.25 × 2.0) and `SOT-23-TT3_N`.
  - Pin names are numeric with a pin "1" everywhere, except the PicoBlade (`1, 2, MP, MP`, which still has a "1").
  - For 11 SOT-669 parts the ODB package is `SOT-669_1` while the P&P says `SOT-669`, so join on designator, not footprint name.
- **The P&P Center equals the pin bounding-box center** on 166 of 174 parts. The 8 exceptions are CN3, CN4, CN7 and Q1B/Q2B/Q3/Q3B/Q4B, where Altium's centroid includes non-pad primitives.

### Findings from the JLC panel `testdata/MC60A_HV_V8D_Y512` (inspected 2026-10-09)

- **Layout:** `ok/` holds JLC's CAM output (jlccam pro v3.2.3), and `YG/` holds the order zip (the V8D Altium single-board Gerbers) plus an order JSON. Opening the top folder auto-opens `ok/`, because the loader descends into the only subfolder with artwork, or asks when there are several.
- **Panel Gerbers have no extensions.** Files are named by ODB++ layer and the header repeats it (`G04 -- layer:tl*`):
  - `tl`, `l2`, `l3`, `bl` are copper (`lN` is stack layer N, so `l2` is Mid-Layer 1).
  - `to`/`bo` are silk and `ts`/`bs` are mask.
  - `drl` is the drill **in Gerber format** (1476 flashes plus 8 slot draws), with no plating info.
  - `ko` is the rout outline and `vcut` holds the V-score lines plus JLC's dimension drawing.
  - `sk` is an ODB "misc" drill layer, probably via plugging. It's shown as Misc Drill, hidden, and isn't a snap target.
  - **There are no paste Gerbers.** The paste layers `gtp`/`gbp` exist only in the ODB++.
- **Format:** `%FSLAX26Y26*%` inches, no X2 attributes, no `%SR` (the panel is flattened), and `%LPC` on copper and silk. Arcs omit zero I/J, which gerbonara correctly reads as 0. Silk files are 4–5 MB, so opening takes ~5 s.
- **The JLC ODB++ carries no component data.** It has no `comp_+_top`/`comp_+_bot` layers and no `eda/data` (JLC rebuilt it from the Gerbers), so footprint outlines and pin 1 can't come from it.
- **`2267606a_y512.tgz` is the ODB++ job.** Its `steps/set/stephdr` has the panel step-repeat: two `EDIT` steps at (0.1968504, 0.1968504) in and (3.5818494, 0.1968504) in, both ANGLE=0, no mirror. The panel profile is 7.1636988 × 4.3236998 in.
- **Board origin:** the `edit` step origin is the board outline's lower-left corner, and Altium's origin sits at +(3.683, 3.302) mm from it. So an Altium coordinate p maps to panel p + (3.683, 3.302) + (5.000, 5.000) for board 1, and + (90.979, 5.000) for board 2. The tests check this against both boards' NPTH holes.
- **Phase 3:** import instance origins and rotations from this ODB++ step-repeat, instead of making DK click each board. Ask DK first.

## Stack

- Python 3.12, `src/` layout, installable with `pip install -e .[dev]`
- **PySide6** for the UI; `QGraphicsView` / `QGraphicsScene` for the canvas
- **gerbonara** for parsing Gerber (RS-274X, X2) and Excellon
- `csv` from the standard library for P&P parsing; **openpyxl** for the BOM (DK's BOMs are .xlsx). Support CSV BOMs too.
- **pytest** for tests; parsers and coordinate math must have unit tests
- **PyInstaller** for the standalone Windows build. Use `--onedir`: it starts faster and trips antivirus less than onefile.
- Configuration lives in `pyproject.toml`. Use ruff for lint/format.

## Repo layout (target)

```
panelcheck/
  CLAUDE.md
  README.md                 # install, run, build-exe instructions
  pyproject.toml
  panelcheck.spec           # PyInstaller spec
  src/panelcheck/
    __main__.py             # python -m panelcheck
    app.py                  # QApplication, main window
    model/
      geometry.py           # Transform2D, unit conversion, registration math
      layers.py             # Layer, LayerType, Side, color table, identification
      snap.py               # snap targets (pad/hole centers) + grid index (built)
      placement.py          # Part, PnPFile, place() (built); BomLine, join + mismatch report
      instance.py           # BoardInstance (origin, rotation, status)
      project.py            # save/load project JSON
    io/
      gerber_loader.py      # folder/zip -> list[Layer] via gerbonara
      pnp_altium.py         # Altium P&P parser (built)
      odb_components.py     # ODB++ components/packages, auto-detect, P&P cross-check (built)
      bom_altium.py         # Altium BOM parser
    ui/
      main_window.py
      layer_tree.py         # left-side tree
      canvas.py             # QGraphicsView: pan/zoom/cursor coords/snapping
      layer_item.py         # RenderList per layer + BoardItem tile compositor (built)
      overlay_item.py       # + markers, labels, hover highlight (built)
      parts_table.py
  tests/
  testdata/                 # gitignored; DK's real files go here
```

## Coordinate conventions (important)

- Internal units are **millimeters**, as floats. Convert on import; display in mm, with mil as an option.
- The coordinate system is **Y-up board/panel coordinates**, the same as Gerber and Altium. Qt's scene is Y-down, so flip Y in exactly one place (the view transform, or a single helper), never ad hoc.
- **Board instance transform:** `panel_xy = R(θ) · board_xy + origin`, where θ is in degrees, counter-clockwise positive.
- **Part rotation on the panel** is `part_rot + θ`, normalized to [0, 360).
- **Bottom view** mirrors the *display* in X. It never changes stored coordinates.
- **Two-point registration.** Given board coordinates a and b of two known features, and clicked panel points A and B:
  - `θ = atan2(B − A) − atan2(b − a)`
  - `origin = A − R(θ)·a`
  - Also compute the scale error `|B − A| / |b − a| − 1` and warn if it exceeds about 0.1%. That usually means a wrong click or a unit mismatch.

## Domain notes

### Gerber layer identification (in priority order)

1. A Gerber X2 `%TF.FileFunction` attribute, if present
2. The Altium file extension
3. The board-house/ODB++ layer name, from the `G04 -- layer:NAME` header and then the file name without extension (`tl`, `bl`, `lN`, `to`, `bo`, `ts`, `bs`, `drl`, `ko`, `vcut`, `sk` …)
4. Manual assignment via right-click in the tree. Board houses often rename files, so this must always be available.

Altium extensions:

| Extension | Layer |
|---|---|
| .GTL / .GBL | top / bottom copper |
| .G1–.G30 | mid layers |
| .GP1–.GPn | internal planes (negative) |
| .GTO / .GBO | top / bottom overlay (silkscreen) |
| .GTS / .GBS | top / bottom solder mask |
| .GTP / .GBP | top / bottom paste |
| .GKO | keep-out |
| .GM1–.GMn | mechanical layers |
| .GD1 / .GG1 | drill drawing / drill guide (hidden by default) |
| .TXT, .DRL, `-RoundHoles.TXT`, `-SlotHoles.TXT` | Excellon drill |
| .DRR, .REP, .RUL, .EXTREP, .APR, .LDP | reports; ignore them but list them greyed out in the tree |

### Layer colors

Approximate Altium defaults. Keep them in one table in `layers.py`.

| Layer | Color |
|---|---|
| Top copper | #FF0000 (red) |
| Bottom copper | #0000FF (blue) |
| Mid layers | distinct colors from a palette |
| Top overlay | #FFFF00 (yellow) |
| Bottom overlay | #808000 (olive/brown) |
| Top solder mask | #800080 (purple) |
| Bottom solder mask | #FF00FF-ish (magenta) |
| Top paste | #808080 (grey) |
| Bottom paste | #800000 (dark red) |
| Keep-out / outline | #FF00FF (magenta) |
| Drill | #C0C0C0 or white |
| Background | black |
| Top-side `+` markers | **cyan** |
| Bottom-side `+` markers | **orange** |

Every color must be user-changeable from the swatch in the tree. Default opacity is about 0.7 per layer, with a slider.

### Rendering requirements

- Gerber layers contain **clear polarity** (LPC) objects, which Altium uses on planes and pours. Draw clear objects with `CompositionMode_Clear` into a per-layer transparent buffer, then composite that buffer with the layer color and opacity. Merging everything into one QPainterPath is wrong.
- Internal planes (.GPx) are negative and need the same treatment.
- Draw order, bottom to top: bottom layers → inner layers → top layers → drill → outline → overlay markers.
- Cache per layer and re-render on zoom changes as needed. Panning must stay smooth on a full panel.

**As built (Phase 1),** in `ui/layer_item.py`:
- Each layer compiles once into a `RenderList`: polarity runs, split into 10 mm spatial chunks. Each chunk holds one winding-fill path and stroke paths keyed by width. Polygons are normalized to positive area so the winding fill is a true union; Qt's `addEllipse`/`addRect` already wind that way.
- A single `BoardItem` composites all layers into 256-px screen tiles keyed in "canvas pixel" space, which is the view's linear transform without translation. Panning renders only newly exposed tiles, and idle time prefetches a one-tile ring. Zooming stretches the old tiles until the wheel settles (160 ms), then renders sharp tiles. Any color, visibility or opacity change drops the tile cache.
- **PySide6 holds the GIL inside QPainter calls**, which I measured: 4 render threads ran no faster than serial, and the main thread stalled ~50 ms. Background-thread rendering doesn't help, so keep rendering on the GUI thread and keep each step small.
- Measured on MC60A_HV at 1920×1080: zoom-to-fit paint ~85 ms; pan at ~200 px/mm 11–16 ms; load and compile ~1 s.

### Snapping (home-point picking)

Snap candidates:
- pad flash centers
- Excellon hit centers
- arc and circle centers
- outline vertices
- (optional) region centroids

Use a snap radius of about 12 screen pixels and show the snap target before the click. Display cursor X/Y live, and allow typing an exact origin as an alternative to clicking.

**As built (requested by DK after Phase 1),** in `model/snap.py` and `ui/canvas.py`:
- **Shift + hover** snaps to the nearest pad flash center (copper, paste, mask) or drill/slot center on visible layers. Coincident targets prefer holes, then copper.
- The **near side wins**: in top view, top pads and holes beat a closer bottom or inner pad, and bottom view does the reverse. The other side is used only when nothing on the near side is in range.
- **Shift + left-click** sets the single **Home (0,0)** point, which the P&P/BOM work will use. Once Home is set, the status bar shows cursor X/Y relative to Home plus absolute. Board > Clear Home removes it, and loading new Gerbers resets it. Phase 3 generalizes Home into per-instance origins.
- The cursor is drawn by the canvas, not the OS: a white "+" with opaque green (#00FF00) 1-px lines running from its arm ends to the viewport edges. When snapped, the drawn cursor jumps to the target, and a green ring marks it. The OS pointer is hidden over the canvas and never warped.
- Not yet snap targets: arc/circle centers, outline vertices, region centroids. Typed origin entry isn't built yet either.

### Altium pick-and-place file

- The file can be .csv or .txt. It has a preamble (the title line, date, "Units used: mm" or mil, etc.) before the column header row. Find the header by scanning for the row containing `Designator`.
- Typical columns: `Designator, Comment, Layer, Footprint, Center-X(mm), Center-Y(mm), Rotation, Description`.
- Older exports use `Mid X / Mid Y`, `Ref X / Ref Y`, or `Pad X / Pad Y`. Prefer Center or Mid, and let the user choose.
- Units come from the column suffix `(mm)` or `(mil)`, or from the "Units used" line. Values may carry a unit suffix like `12.5mm`.
- `Layer` is `TopLayer` or `BottomLayer`.
- **Verified (MC60A_HV):** bottom-side X is reported as seen from the top. Bottom parts' centroids sit between their unmirrored GBP pads.
- DK's file: 12-line preamble, header on line 13, every field double-quoted, CRLF, `Units used: mm`, Center-X/Y columns, rotations only 0/90/180/270, 174 parts (96 top, 78 bottom). Description can be empty (CN3). The second preamble line is the output path (`Z:\…`), not a `Key: value` line.
- **The Altium origin is a mounting hole** (3.175 mm PTH on MC60A_HV; on the JLC panel, 3.3 mm `drl` holes at (8.683, 8.302) for board 1 and (94.662, 8.302) for board 2), so Shift+click snaps Home exactly onto it. With Home there, the 0603/0805/1206 centroids land within 1.6 mm of a paste pad on their own side (tested).

**Pick-and-place overlay (as built)** in `io/pnp_altium.py`, `model/placement.py`, `ui/overlay_item.py`:
- **Parser.** It finds the header by scanning for a `Designator` field and detects the delimiter (comma, tab, semicolon, or quoted whitespace) on that line. Centroid column sets are Center > Mid > Ref > Pad (> bare X/Y). Units come from the value suffix, then the column suffix, then `Units used:`; if none is found it assumes mm and warns. Bad rows are skipped with warnings; duplicate designators produce a warning. Only DK's CSV is verified, and the .txt variants are untested against real Altium output.
- **Loading.** File > Open Pick and Place (Ctrl+P), or drop the file. A dropped .csv/.txt is routed by content (`looks_like_pnp`), because Altium drill files are .TXT too.
  - If Home isn't set, a dialog asks for it. The parts wait (the tree shows a note, Esc cancels) and are placed when Home is picked.
  - Clearing Home or loading new Gerbers hides the markers but keeps the file.
- **Placement.** `place(parts, [Transform2D(home)])`, with θ = 0. Phase 3 replaces the single Home with one `Transform2D` per board instance. Bottom parts aren't mirrored; their X is already as seen from the top.
- **Markers.** Fixed 6 px-arm `+`, a 1 px side-colored line over a 3 px dark halo, in viewport pixels. The near side is drawn last. Designators show at ≥25 px/mm. Hovering within 8 px highlights the part and shows its details in the status bar.
- **Tree.** The Pick & Place group sits at the top, with Top parts / Bottom parts rows (checkbox plus color swatch; colors persist in settings). Right-click offers the coordinate column set and Remove.

**ODB++ footprints (as built)** in `io/odb_components.py` (DK's choices: P&P for placement, ODB++ for outline, pin 1 and checks; auto-detect; pads on by default with a toggle):
- **Auto-detect.** On P&P load, `find_odb_for` searches the P&P folder (depth 3), then its parent (depth 2), for job folders (`matrix/matrix`) and .tgz/.tar.gz/.zip archives.
  - Jobs without component layers are skipped, such as JLC's flattened job.
  - The job sharing the most designators wins; ties go to the newest.
  - Right-click Pick & Place > Load ODB++ Folder/Archive overrides it. Reloading the same P&P (e.g. a coordinate-set change) keeps the current job.
- **Model.** `Footprint` (outline, pads, pin1, `outline_is_body`) is built in board mm at load and carried through `place()` by `Transform2D`, so it follows Home and will follow Phase 3 instances. Pin 1 comes from the component's own TOP pin record when present, else from the package pin "1".
- **Cross-check** (`check_parts`): it flags side mismatches, rotation mismatches (after the convention conversion), centroids more than 0.05 mm outside their pads' box, and parts missing from the ODB++.
  - Flagged parts get a red ring at every zoom, a "⚠ N parts disagree" tree row (tooltip lists them), and the problem text on hover.
  - ODB-only parts and footprint-name differences are notes in the ODB++ row's tooltip.
  - DK's files: 174 matched, no issues.
- **Drawing** (≥ 6 px/mm, **near side only**; the far side's pads showing through were confusing):
  - pad outlines (1 px, side color, toggle "Footprint pads (ODB++)", persisted as `show_footprint_pads`), or, when pads are off, the dashed pads' box
  - real body contours, solid
  - a 3 px pin-1 dot
  - all lines un-antialiased, for crisp 1-px edges
  
  The dashed box is hidden while pads show because on H1 (heatsink) and the connectors it spans the board.

### Altium BOM

- The BOM is usually .xlsx. Altium's Excel template can put title and logo rows above the header, so find the header row by scanning for a `Designator` cell rather than assuming row 1.
- **openpyxl can't open Altium's .xlsx directly.** `xl/styles.xml` has a non-standard `xxid` attribute on cell styles, which raises `TypeError: CellStyle.__init__() got an unexpected keyword argument 'xxid'`. Workaround: copy the zip into memory, strip `xxid="…"` from `styles.xml`, and load from the `BytesIO`. Never write the cleaned copy back to disk.
- DK's file (`MC60A_HV.xlsx`): sheet `MC60A_HV` (plus empty `Sheet2`, `Sheet3`). Header is in row 1, with no title rows. Columns are `Designator, Comment, Description, Quantity, DigiKey, Layer`. There's **no Footprint column**. `Layer` holds `Top`, `Bottom`, or `Bottom, Top` for mixed groups.
- **DNP is marked by `Description = "No Populate"`** (CN2, M1–M4). There's no dedicated DNP column. Treat "No Populate", "Not Fitted", and "DNP" in any column as DNP.
- Grouped lines concatenate differing Descriptions and DigiKey PNs with `", "`, which collides with commas inside descriptions. Don't try to split those back per designator.
- Comment case can differ between BOM and P&P (C3: `0.1uf` vs `0.1uF`). Compare case-insensitively; flag it as info, not a mismatch.
- Designators are grouped, e.g. `C1, C2, C5-C8`. Split on commas and expand ranges. DK's file has no ranges, but keep supporting them. Check that the expanded count equals `Quantity`.
- Join to P&P rows by designator. Report:
  - parts in the P&P but not in the BOM
  - parts in the BOM but not in the P&P
  - DNP / "Not Fitted" lines (with a toggle to hide them)
  - duplicate designators

## Phases

### Phase 1 — Viewer
- Open a folder or .zip of Gerber + Excellon files.
- Left tree grouped as Top / Bottom / Inner / Drill / Mechanical / Other, with a visibility checkbox and color swatch on each layer.
- Right canvas with layers rendered in color:
  - zoom on the mouse wheel, centered at the cursor
  - pan with right-drag, middle-drag or space+drag
  - zoom-to-fit
- Status bar with cursor X/Y and units.
- Top/bottom view toggle.
- **Done when:** `testdata/Gerber_MC60A_HV` (including its NC drill files) renders correctly against DK's reference viewer, including clear-polarity areas, planes, and drill hits. Later, a real board-house panel must render the same way.

### Phase 2 — Import
- Parse the P&P and BOM, join them, and show a parts table below the tree or in a dock.
- Show the mismatch report.
- Unit tests for both parsers, using DK's real files and synthetic edge cases.

### Phase 3 — Board instances and overlay
- "Add board instance", then set its origin by snapped click or typed coordinates, plus a rotation of 0/90/180/270 or free.
- Reference-feature mode: pick 1 or 2 designators (fiducials or mounting holes) from the P&P and click them on the panel. The app computes origin and rotation and reports the scale error.
- Draw a `+` at every part centroid for every instance, in the side color. Show designator labels when zoomed in past a threshold.
- The tree shows each instance (Board 1…N) with its origin, rotation, and set/unset status.

### Phase 4 — Verification aids
- Click a `+` to highlight its row in the table, show its BOM line, and highlight the same part on all instances (and the reverse, from the table).
- Auto-check: flag any centroid that doesn't land on a copper or paste feature on its own side.
- Filters: show top only, bottom only, or flagged only.

### Phase 5 — Project file
- Save and load a JSON project: file paths (relative to the project file when possible), layer type overrides, colors and visibility, instances, and the chosen P&P column set.

### Phase 6 — Exports (formats TBD)
- Philips/Assembleon P&P program and Mirtec MV-2HTL AOI program.
- **Ask DK for sample files first.** Both exports consume the same transformed panel list:

  `designator, instance, panel_x, panel_y, panel_rot, side, footprint, value/comment, BOM fields`

## Commands

```
python -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]
.venv\Scripts\python -m panelcheck            # run
.venv\Scripts\python -m panelcheck DIR --snapshot out.png [--bottom] [--layers .GTL,.GTO] [--zoom-mm X,Y,W,H] [--home X,Y --pnp FILE]
                                # headless render to PNG (set QT_QPA_PLATFORM=offscreen in CI/agents)
.venv\Scripts\python -m pytest                          # tests
.venv\Scripts\ruff check . ; .venv\Scripts\ruff format .
.venv\Scripts\pyinstaller panelcheck.spec     # standalone build -> dist\panelcheck\panelcheck.exe
```

Keep the README's install, run, and build steps in sync with these commands.
