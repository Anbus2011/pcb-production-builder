"""Load a folder or .zip of Gerber + Excellon files into a list of :class:`Layer`.

Input files are only ever read. Identification priority: X2 ``.FileFunction`` -> Altium extension
-> file content (Excellon is always drill; other Gerbers become UNKNOWN for manual assignment).
"""

from __future__ import annotations

import re
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path

from gerbonara import ExcellonFile, GerberFile

from panelcheck.model.layers import (
    DRILL,
    ID_CONTENT,
    ID_EXTENSION,
    ID_LAYER_NAME,
    ID_X2,
    REPORT,
    UNKNOWN,
    Layer,
    LayerType,
    altium_layer_color,
    default_color,
    file_function_from,
    is_drill_extension,
    is_report_extension,
    kind_from_extension,
    kind_from_file_function,
    kind_from_layer_name,
    layer_name_from_comments,
)

MAX_ARTWORK_BYTES = 200 * 1024 * 1024

# Parser notes that are legal per the Gerber spec and only clutter the tooltip.
_BENIGN_WARNING_RX = re.compile(r'"G70"|"G71"|"G90"|"G91"|Arc is missing [IJ] value')


@dataclass
class SourceFile:
    name: str  # display name (zip member path or file name)
    data: bytes


def read_sources(path: str | Path) -> list[SourceFile]:
    """Read every file in a folder (non-recursive) or a .zip (all members)."""
    p = Path(path)
    if p.is_dir():
        files = sorted((f for f in p.iterdir() if f.is_file()), key=lambda f: f.name.lower())
        return [SourceFile(f.name, f.read_bytes()) for f in files]
    if zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as z:
            members = sorted((i for i in z.infolist() if not i.is_dir()), key=lambda i: i.filename.lower())
            return [SourceFile(i.filename, z.read(i)) for i in members]
    raise ValueError(f"Not a folder or .zip file: {p}")


_GERBER_RX = re.compile(rb"%FS[LT]?[AI]?X\d\dY\d\d|%MO(MM|IN)\*%|^G04", re.M)
_EXCELLON_RX = re.compile(rb"^M48\s*$|^(INCH|METRIC)\b|^T\d+.*C\d*\.\d+", re.M)


def sniff(data: bytes) -> str | None:
    """'gerber', 'excellon', or None. Only looks at the first 16 KiB."""
    head = data[:16384]
    if b"\x00" in head:
        return None
    if _EXCELLON_RX.search(head) and not re.search(rb"%FS", head):
        return "excellon"
    if _GERBER_RX.search(head) and re.search(rb"[XY]-?\d+.*D0?[123]\*|%AD", data):
        return "gerber"
    return None


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def load_layer(src: SourceFile) -> Layer:
    """Identify and parse one file. Never raises: parse failures are reported on the Layer."""
    if is_report_extension(src.name) or len(src.data) > MAX_ARTWORK_BYTES:
        return Layer(src.name, REPORT, ID_EXTENSION, default_color(REPORT), visible=False)

    content = sniff(src.data)
    if content is None:
        return Layer(src.name, REPORT, ID_CONTENT, default_color(REPORT), visible=False)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            text = _decode(src.data)
            if content == "excellon":
                parsed = ExcellonFile.from_string(text, filename=src.name)
            else:
                parsed = GerberFile.from_string(text, filename=src.name)
        except Exception as e:  # gerbonara raises a variety of exceptions on bad input
            kind = DRILL if content == "excellon" else (kind_from_extension(src.name) or UNKNOWN)
            layer = Layer(src.name, kind, ID_EXTENSION, default_color(kind), visible=False)
            layer.error = f"{type(e).__name__}: {e}"
            return layer
    msgs = [str(w.message) for w in caught if not _BENIGN_WARNING_RX.search(str(w.message))]

    if content == "excellon":
        kind, id_source = DRILL, ID_EXTENSION if is_drill_extension(src.name) else ID_CONTENT
    else:
        kind, id_source = None, ID_X2
        if ff := file_function_from(parsed.file_attrs, parsed.comments):
            kind = kind_from_file_function(ff)
        if kind is None:
            kind, id_source = kind_from_extension(src.name), ID_EXTENSION
        if kind is None:
            # Board-house CAM output: the layer name in the header, else the file name without extension.
            names = [layer_name_from_comments(parsed.comments), _stem(src.name)]
            kind = next((k for n in names if n and (k := kind_from_layer_name(n))), None)
            id_source = ID_LAYER_NAME
        if kind is None:
            kind, id_source = UNKNOWN, ID_CONTENT

    file_color = altium_layer_color(parsed.comments)
    layer = Layer(
        name=src.name,
        kind=kind,
        id_source=id_source,
        color=file_color or default_color(kind),
        parsed=parsed,
        file_color=file_color,
        warnings=msgs,
    )
    layer.visible = not kind.hidden_by_default
    return layer


def load_layers(path: str | Path) -> list[Layer]:
    return [load_layer(s) for s in read_sources(path)]


def _stem(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0] if "." in base else base


def folder_has_artwork(path: Path) -> bool:
    """True if any file directly in ``path`` looks like Gerber or Excellon (cheap header sniff)."""
    for f in path.iterdir():
        if f.is_file() and not is_report_extension(f.name):
            try:
                with f.open("rb") as fh:
                    head = fh.read(65536)
            except OSError:
                continue
            if sniff(head):
                return True
    return False


def artwork_subfolders(path: Path, max_depth: int = 2) -> list[Path]:
    """Subfolders (up to ``max_depth`` deep) that hold Gerber/Excellon files, for when the chosen
    folder itself has none, e.g. a board-house delivery with the panel Gerbers in ``ok/``."""
    found: list[Path] = []
    level = [path]
    for _ in range(max_depth):
        nxt = []
        for d in level:
            for sub in sorted((s for s in d.iterdir() if s.is_dir()), key=lambda s: s.name.lower()):
                (found if folder_has_artwork(sub) else nxt).append(sub)
        if found:
            break
        level = nxt
    return found


def artwork_layers(layers: list[Layer]) -> list[Layer]:
    return [la for la in layers if la.parsed is not None and la.kind.type is not LayerType.REPORT]
