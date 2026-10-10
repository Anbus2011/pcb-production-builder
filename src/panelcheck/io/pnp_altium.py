"""Altium pick-and-place parser (.csv, or .txt with delimited or quoted whitespace-separated fields).

The header is the first row with a ``Designator`` field; everything above it is preamble. Units come
from a value suffix (``12.5mm``), else the column suffix (``Center-X(mm)``), else the ``Units used:``
line. Verified against DK's Altium 22 CSV export (comma-separated, every field quoted, CRLF).
"""

from __future__ import annotations

import csv
import re
import shlex
from pathlib import Path

from panelcheck.model.geometry import MM_PER_INCH, MM_PER_MIL, normalize_angle
from panelcheck.model.layers import Side
from panelcheck.model.placement import Part, PnPFile

# Column-set name -> normalized prefix, in order of preference (Center or Mid preferred).
COORD_SETS = {"Center": "center", "Mid": "mid", "Ref": "ref", "Pad": "pad", "X/Y": ""}

_UNIT_MM = {"mm": 1.0, "mil": MM_PER_MIL, "mils": MM_PER_MIL, "in": MM_PER_INCH, "inch": MM_PER_INCH}
_COL_UNIT_RX = re.compile(r"\(\s*(mm|mils?|in|inch)\s*\)\s*$", re.IGNORECASE)
_UNITS_LINE_RX = re.compile(r"units\s+used\s*:\s*(mm|mils?|in|inch)\b", re.IGNORECASE)
_VALUE_RX = re.compile(
    r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(mm|mils?|in|inch)?\s*$", re.IGNORECASE
)
_INFO_RX = re.compile(r"^\s*([A-Za-z][A-Za-z ]{1,30}?)\s*:\s*(.+?)\s*$")  # 2+ chars: not "Z:\path"
_DELIMITERS = (",", "\t", ";")
_HEADER_SCAN_LINES = 200

_TOP = {"toplayer", "top", "t", "topside"}
_BOTTOM = {"bottomlayer", "bottom", "b", "bot", "bottomside"}


class PnPError(ValueError):
    pass


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _COL_UNIT_RX.sub("", name).lower())


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _split(line: str, delimiter: str | None) -> list[str]:
    if delimiter is None:
        try:
            return shlex.split(line, posix=True)
        except ValueError:
            return line.split()
    return [f.strip() for f in next(csv.reader([line], delimiter=delimiter))]


def _find_header(lines: list[str]) -> tuple[int, str | None]:
    """Index of the header row and its delimiter (None = whitespace)."""
    for i, line in enumerate(lines[:_HEADER_SCAN_LINES]):
        if "designator" not in line.lower():
            continue
        counts = {d: line.count(d) for d in _DELIMITERS}
        best = max(counts, key=counts.get)
        delimiter = best if counts[best] else None
        if "designator" in (_norm(f) for f in _split(line, delimiter)):
            return i, delimiter
    raise PnPError("No column header row with a 'Designator' column found.")


def looks_like_pnp(path: str | Path) -> bool:
    """Cheap check used to route dropped .csv/.txt files (Altium drill files are .TXT too)."""
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            head = [f.readline() for _ in range(_HEADER_SCAN_LINES)]
        _find_header(head)
        return True
    except (OSError, PnPError):
        return False


def parse_pnp(path: str | Path, coord_set: str | None = None) -> PnPFile:
    """Parse an Altium P&P file. ``coord_set`` picks the X/Y columns ("Center", "Mid", "Ref", "Pad");
    by default Center, then Mid, then whatever the file has."""
    path = Path(path)
    lines = _read_text(path).splitlines()
    hi, delimiter = _find_header(lines)
    header = _split(lines[hi], delimiter)
    norm = [_norm(h) for h in header]
    col = {n: i for i, n in reversed(list(enumerate(norm))) if n}  # first occurrence wins

    info: dict[str, str] = {}
    for line in lines[:hi]:
        if (m := _INFO_RX.match(line)) and not line.strip().startswith("="):
            info.setdefault(m.group(1).strip(), m.group(2))
    m = _UNITS_LINE_RX.search("\n".join(lines[:hi]))
    file_unit = m.group(1).lower() if m else None

    available = [name for name, pre in COORD_SETS.items() if f"{pre}x" in col and f"{pre}y" in col]
    if not available:
        raise PnPError(f"No X/Y coordinate columns found in header: {', '.join(header)}")
    if coord_set is None:
        coord_set = available[0]
    elif coord_set not in available:
        raise PnPError(f"Column set '{coord_set}' not in file (has {', '.join(available)}).")
    pre = COORD_SETS[coord_set]
    ix, iy = col[f"{pre}x"], col[f"{pre}y"]
    col_unit = {}
    for i in (ix, iy):
        cm = _COL_UNIT_RX.search(header[i])
        col_unit[i] = cm.group(1).lower() if cm else file_unit

    i_des = col["designator"]
    i_layer = next((col[k] for k in ("layer", "tb", "side") if k in col), None)
    i_rot = next((col[k] for k in ("rotation", "rot", "angle") if k in col), None)

    def text(row: list[str], key: str) -> str:
        i = col.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    warnings: list[str] = []
    if i_layer is None:
        warnings.append("No Layer column; all parts treated as top side.")
    parts: list[Part] = []
    units_used: set[str] = set()
    unknown_unit = False

    def coord(row: list[str], i: int, what: str, line_no: int) -> float | None:
        nonlocal unknown_unit
        vm = _VALUE_RX.match(row[i]) if i < len(row) else None
        if vm is None:
            warnings.append(f"Line {line_no}: bad {what} value {row[i] if i < len(row) else '(missing)'!r}")
            return None
        unit = (vm.group(2) or col_unit[i] or "").lower()
        if not unit:
            unknown_unit = True
            unit = "mm"
        units_used.add("mil" if unit == "mils" else "in" if unit == "inch" else unit)
        return float(vm.group(1)) * _UNIT_MM[unit]

    rows = (
        csv.reader(lines[hi + 1 :], delimiter=delimiter)
        if delimiter
        else (_split(ln, None) for ln in lines[hi + 1 :])
    )
    for n, row in enumerate(rows):
        line_no = hi + 2 + n
        if not any(f.strip() for f in row):
            continue
        des = row[i_des].strip() if i_des < len(row) else ""
        if not des:
            warnings.append(f"Line {line_no}: no designator, skipped")
            continue
        side = Side.TOP
        if i_layer is not None:
            lv = _norm(row[i_layer]) if i_layer < len(row) else ""
            if lv in _TOP:
                side = Side.TOP
            elif lv in _BOTTOM:
                side = Side.BOTTOM
            else:
                warnings.append(f"Line {line_no}: {des} has unknown layer {lv!r}, skipped")
                continue
        x, y = coord(row, ix, "X", line_no), coord(row, iy, "Y", line_no)
        if x is None or y is None:
            continue
        rot = 0.0
        if i_rot is not None and i_rot < len(row) and row[i_rot].strip():
            try:
                rot = normalize_angle(float(row[i_rot]))
            except ValueError:
                warnings.append(f"Line {line_no}: {des} has bad rotation {row[i_rot]!r}; using 0")
        parts.append(
            Part(
                designator=des,
                comment=text(row, "comment"),
                side=side,
                footprint=text(row, "footprint"),
                x=x,
                y=y,
                rotation=rot,
                description=text(row, "description"),
            )
        )

    if not parts:
        raise PnPError("The file has a header row but no parts.")
    if unknown_unit:
        warnings.append("Units not stated in the columns, values or 'Units used' line; assumed mm.")
    seen: dict[str, int] = {}
    for p in parts:
        seen[p.designator] = seen.get(p.designator, 0) + 1
    dups = sorted(d for d, c in seen.items() if c > 1)
    if dups:
        warnings.append(f"Duplicate designators: {', '.join(dups)}")

    return PnPFile(
        path=path,
        parts=parts,
        coord_set=coord_set,
        coord_sets=available,
        source_unit=units_used.pop() if len(units_used) == 1 else "mixed",
        header_line=hi + 1,
        info=info,
        warnings=warnings,
    )
