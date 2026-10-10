"""ODB++ component reader: components, pins and package geometry from an Altium ODB++ export.

Reads only what the overlay needs: ``steps/<step>/layers/comp_+_top|comp_+_bot/components`` and the
packages in ``steps/<step>/eda/data``. The job can be a folder or a .tgz/.tar.gz/.zip archive.

Conventions, verified on DK's AD 22.6.1 export (all 179 components fit to 0.01 mm):
- ODB rotation is clockwise. P&P (CCW) rotation = ``-rot`` on top, ``rot + 180`` on the bottom.
- Package-local -> board: top ``ref + R(-rot)·p``; bottom ``ref + R(+rot)·(-px, py)``.
- The CMP x,y is the footprint reference point, not the centroid.
"""

from __future__ import annotations

import math
import os
import tarfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from panelcheck.model.geometry import MM_PER_INCH, arc_points, normalize_angle
from panelcheck.model.layers import Side
from panelcheck.model.placement import Footprint, Part, Point, Polygon

ARCHIVE_SUFFIXES = (".tgz", ".tar.gz", ".zip")
_COMP_LAYERS = {"comp_+_top": Side.TOP, "comp_+_bot": Side.BOTTOM}
_SAME_MM = 0.01


class OdbError(ValueError):
    pass


@dataclass
class Package:
    name: str
    outline: list[Polygon]  # package-local mm
    outline_is_body: bool  # False when it's just the pads' bounding box (Altium's usual RC)
    pins: dict[str, tuple[Point, list[Polygon]]]  # pin name -> (center, pad contours)


@dataclass
class OdbComponent:
    designator: str
    comment: str
    side: Side
    x: float  # footprint reference point, board mm
    y: float
    rotation: float  # ODB rotation (clockwise degrees), as in the file
    package: str
    mount_type: str  # "smt", "th", or "" if not given
    pins: dict[str, Point]  # pin name -> board mm (from the component's TOP records)
    props: dict[str, str] = field(default_factory=dict)  # PRP lines (DigiKey, ...)
    footprint: Footprint | None = None

    @property
    def pnp_rotation(self) -> float:
        """The rotation the Altium P&P file reports for this part (CCW degrees)."""
        r = -self.rotation if self.side is Side.TOP else self.rotation + 180
        return normalize_angle(r)


@dataclass
class OdbJob:
    path: Path
    step: str
    components: dict[str, OdbComponent]
    packages: list[Package]
    warnings: list[str] = field(default_factory=list)


# ---- job access (folder or archive) ------------------------------------------------------------


class _Job:
    """Files of one ODB++ job, keyed by lower-case path relative to the job root."""

    def __init__(self, files: dict[str, Callable[[], bytes]]):
        self.files = files

    def text(self, rel: str) -> str:
        return self.files[rel.lower()]().decode("utf-8", errors="replace")

    def has(self, rel: str) -> bool:
        return rel.lower() in self.files


def _open_job(path: Path) -> _Job:
    if path.is_dir():
        root = path
        if not (root / "matrix" / "matrix").is_file():
            subs = [d for d in path.iterdir() if (d / "matrix" / "matrix").is_file()]
            if len(subs) != 1:
                raise OdbError(f"{path} is not an ODB++ job (no matrix/matrix).")
            root = subs[0]
        files = {}
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                p = Path(dirpath) / n
                files[p.relative_to(root).as_posix().lower()] = p.read_bytes
        return _Job(files)
    name = path.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            data = {i.filename: z.read(i) for i in z.infolist() if _wanted(i.filename)}
    elif name.endswith((".tgz", ".tar.gz")):
        data = {}
        with tarfile.open(path, "r:*") as t:
            for m in t:
                if m.isfile() and _wanted(m.name):
                    data[m.name] = t.extractfile(m).read()
    else:
        raise OdbError(f"{path.name}: not a folder, .tgz or .zip.")
    roots = [k[: -len("matrix/matrix")] for k in data if k.lower().endswith("matrix/matrix")]
    if not roots:
        raise OdbError(f"{path.name} has no ODB++ job (no matrix/matrix).")
    root = min(roots, key=len)
    return _Job({k[len(root) :].lower(): (lambda v=v: v) for k, v in data.items() if k.startswith(root)})


def _wanted(name: str) -> bool:
    n = name.lower()
    return n.endswith(("matrix/matrix", "/components", "/eda/data", "misc/info"))


# ---- parsing ---------------------------------------------------------------------------------


def _scale(text: str) -> float:
    for line in text.splitlines()[:40]:
        s = line.strip().upper().replace(" ", "")
        if s.startswith("UNITS="):
            return MM_PER_INCH if s[6:] == "INCH" else 1.0
        if s == "U INCH" or s == "UINCH":
            return MM_PER_INCH
    return 1.0


def _parse_shape(t: list[str], lines: list[list[str]], i: int, s: float) -> tuple[list[Polygon], int]:
    """Parse one RC/CR/SQ/CT shape starting at ``lines[i]``. Returns polygons and the next index."""
    kind = t[0]
    if kind == "RC":
        x, y, w, h = (float(v) * s for v in t[1:5])
        return [((x, y), (x + w, y), (x + w, y + h), (x, y + h))], i + 1
    if kind == "CR":
        cx, cy, r = (float(v) * s for v in t[1:4])
        return [_circle(cx, cy, r)], i + 1
    if kind == "SQ":
        cx, cy, h = (float(v) * s for v in t[1:4])
        return [((cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h))], i + 1
    if kind == "CT":
        polys: list[Polygon] = []
        pts: list[Point] = []
        i += 1
        while i < len(lines) and lines[i][0] != "CE":
            r = lines[i]
            if r[0] == "OB":
                pts = [(float(r[1]) * s, float(r[2]) * s)]
            elif r[0] == "OS":
                pts.append((float(r[1]) * s, float(r[2]) * s))
            elif r[0] == "OC":
                xe, ye, xc, yc = (float(v) * s for v in r[1:5])
                x0, y0 = pts[-1]
                pts.extend(list(arc_points(xc, yc, x0, y0, xe, ye, clockwise=r[5].upper() == "Y"))[1:])
            elif r[0] == "OE" and len(pts) > 2:
                polys.append(tuple(pts))
            i += 1
        return polys, i + 1
    return [], i + 1


def _circle(cx: float, cy: float, r: float, n: int = 24) -> Polygon:
    angles = (2 * math.pi * k / n for k in range(n))
    return tuple((cx + r * math.cos(a), cy + r * math.sin(a)) for a in angles)


def _bbox(polys: list[Polygon]) -> tuple[float, float, float, float] | None:
    pts = [p for poly in polys for p in poly]
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def parse_packages(text: str) -> list[Package]:
    s = _scale(text)
    lines = [ln.split() for ln in text.splitlines()]
    lines = [t for t in lines if t and not t[0].startswith("#")]
    packages: list[Package] = []
    i = 0
    while i < len(lines):
        t = lines[i]
        if t[0] != "PKG":
            i += 1
            continue
        name = t[1]
        box = tuple(float(v) * s for v in t[3:7])
        outline: list[Polygon] = []
        pins: dict[str, tuple[Point, list[Polygon]]] = {}
        i += 1
        current: str | None = None
        while i < len(lines) and lines[i][0] != "PKG":
            r = lines[i]
            if r[0] == "PIN":
                current = r[1]
                pins.setdefault(current, ((float(r[3]) * s, float(r[4]) * s), []))
                i += 1
            elif r[0] in ("RC", "CR", "SQ", "CT"):
                polys, i = _parse_shape(r, lines, i, s)
                (pins[current][1] if current is not None else outline).extend(polys)
            else:
                i += 1  # attributes and other records
        if not outline and len(box) == 4:
            x0, y0, x1, y1 = box
            outline = [((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        pad_box = _bbox([p for _, pads in pins.values() for p in pads])
        out_box = _bbox(outline)
        is_pad_box = (
            len(outline) == 1
            and len(outline[0]) == 4
            and pad_box is not None
            and all(abs(a - b) <= _SAME_MM for a, b in zip(out_box, pad_box, strict=True))
        )
        packages.append(Package(name, outline, not is_pad_box, pins))
    return packages


def _parse_components(text: str, side: Side) -> list[tuple[int, OdbComponent]]:
    """Components of one comp layer, each with its package index into eda/data."""
    s = _scale(text)
    attr_names: dict[str, str] = {}
    out: list[tuple[int, OdbComponent]] = []
    c: OdbComponent | None = None
    for line in text.splitlines():
        t = line.split()
        if not t:
            continue
        if t[0].startswith("@"):
            attr_names[t[0][1:]] = t[1]
        elif t[0] == "CMP":
            head, _, attrs = line.partition(";")
            h = head.split()
            values = dict(kv.split("=", 1) for kv in attrs.split(",") if "=" in kv)
            named = {attr_names.get(k, k): v for k, v in values.items()}
            mount = {"1": "smt", "2": "th"}.get(named.get(".comp_mount_type", ""), "")
            c = OdbComponent(
                designator=h[6],
                comment=" ".join(h[7:]),
                side=side,
                x=float(h[2]) * s,
                y=float(h[3]) * s,
                rotation=float(h[4]),
                package="",
                mount_type=mount,
                pins={},
            )
            out.append((int(h[1]), c))
        elif t[0] == "PRP" and c is not None and len(t) >= 2:
            c.props[t[1]] = line.split(None, 2)[2].strip().strip("'") if len(t) > 2 else ""
        elif t[0] == "TOP" and c is not None:
            name = t[-1].split("-", 1)[1] if t[-1].startswith(c.designator + "-") else t[1]
            c.pins.setdefault(name, (float(t[2]) * s, float(t[3]) * s))
    return out


def _to_board(c: OdbComponent) -> Callable[[Point], Point]:
    a = math.radians(-c.rotation if c.side is Side.TOP else c.rotation)
    ca, sa = math.cos(a), math.sin(a)
    mirror = -1.0 if c.side is Side.BOTTOM else 1.0

    def f(p: Point) -> Point:
        x, y = p[0] * mirror, p[1]
        return c.x + ca * x - sa * y, c.y + sa * x + ca * y

    return f


def _footprint(c: OdbComponent, pkg: Package) -> Footprint:
    f = _to_board(c)
    pads = [tuple(f(p) for p in poly) for _, polys in pkg.pins.values() for poly in polys]
    if "1" in c.pins:
        pin1 = c.pins["1"]
    elif "1" in pkg.pins:
        pin1 = f(pkg.pins["1"][0])
    else:
        pin1 = None
    return Footprint(
        package=pkg.name,
        side=c.side,
        outline=tuple(tuple(f(p) for p in poly) for poly in pkg.outline),
        outline_is_body=pkg.outline_is_body,
        pads=tuple(pads),
        pin1=pin1,
    )


def load_odb(path: str | Path) -> OdbJob:
    """Load components and package geometry from an ODB++ job folder or archive."""
    path = Path(path)
    job = _open_job(path)
    steps = sorted({k.split("/")[1] for k in job.files if k.startswith("steps/") and k.count("/") >= 2})
    with_comps = [
        st for st in steps if any(job.has(f"steps/{st}/layers/{lay}/components") for lay in _COMP_LAYERS)
    ]
    if not with_comps:
        raise OdbError(f"{path.name} has no component layers (comp_+_top / comp_+_bot).")
    step = "pcb" if "pcb" in with_comps else with_comps[0]
    warnings: list[str] = []
    if len(with_comps) > 1:
        warnings.append(f"Several steps have components ({', '.join(with_comps)}); using {step}.")
    if not job.has(f"steps/{step}/eda/data"):
        raise OdbError(f"{path.name}: step {step} has no eda/data (package definitions).")
    packages = parse_packages(job.text(f"steps/{step}/eda/data"))
    components: dict[str, OdbComponent] = {}
    for layer, side in _COMP_LAYERS.items():
        rel = f"steps/{step}/layers/{layer}/components"
        if not job.has(rel):
            continue
        for idx, c in _parse_components(job.text(rel), side):
            if not 0 <= idx < len(packages):
                warnings.append(f"{c.designator}: package index {idx} out of range")
                c.package = "?"
            else:
                c.package = packages[idx].name
                c.footprint = _footprint(c, packages[idx])
            if c.designator in components:
                warnings.append(f"Duplicate designator in ODB++: {c.designator}")
            components[c.designator] = c
    return OdbJob(path, step, components, packages, warnings)


# ---- cross-check against the P&P ---------------------------------------------------------------

CENTROID_TOLERANCE_MM = 0.05  # how far outside its pads' bounding box a P&P centroid may sit
ROTATION_TOLERANCE_DEG = 0.05


@dataclass
class OdbCheck:
    matched: int
    issues: dict[str, list[str]]  # designator -> problems
    notes: list[str]  # informational, file level


def check_parts(parts: list[Part], job: OdbJob) -> OdbCheck:
    """Compare P&P parts with the ODB++ components: side, rotation, and that each centroid lies on
    its footprint's pads. Footprint name differences and ODB-only parts are notes, not issues."""
    issues: dict[str, list[str]] = {}
    renamed: dict[tuple[str, str], int] = {}
    matched = 0
    for p in parts:
        c = job.components.get(p.designator)
        if c is None:
            issues.setdefault(p.designator, []).append("not in the ODB++")
            continue
        matched += 1
        problems = []
        if c.side is not p.side:
            problems.append(f"P&P says {p.side.value} side, ODB++ says {c.side.value}")
        else:
            d = abs(p.rotation - c.pnp_rotation) % 360
            if min(d, 360 - d) > ROTATION_TOLERANCE_DEG:
                problems.append(f"rotation {p.rotation:g}° in P&P, {c.pnp_rotation:g}° in ODB++")
            box = _bbox(list(c.footprint.pads)) if c.footprint is not None else None
            if box is not None:
                dx = max(box[0] - p.x, 0.0, p.x - box[2])
                dy = max(box[1] - p.y, 0.0, p.y - box[3])
                off = math.hypot(dx, dy)
                if off > CENTROID_TOLERANCE_MM:
                    problems.append(f"centroid is {off:.2f} mm off its pads")
        if problems:
            issues[p.designator] = problems
        if p.footprint and c.package and p.footprint != c.package:
            renamed[(p.footprint, c.package)] = renamed.get((p.footprint, c.package), 0) + 1
    notes = []
    in_pnp = {p.designator for p in parts}
    extra = sorted(d for d in job.components if d not in in_pnp)
    if extra:
        notes.append(f"In ODB++ but not in the P&P: {', '.join(extra)}")
    for (a, b), n in sorted(renamed.items()):
        notes.append(f"Footprint name differs: P&P {a}, ODB++ {b} ({n} parts)")
    return OdbCheck(matched, issues, notes)


# ---- auto-detection --------------------------------------------------------------------------


def find_odb_jobs(start: str | Path, max_depth: int = 3, max_dirs: int = 2000) -> list[Path]:
    """ODB++ job folders and archives at or below ``start`` (archives aren't opened here)."""
    start = Path(start)
    found: list[Path] = []
    visited = 0
    stack = [(start, 0)]
    while stack and visited < max_dirs:
        d, depth = stack.pop()
        visited += 1
        if (d / "matrix" / "matrix").is_file():
            found.append(d)
            continue  # don't descend into a job
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if e.is_dir(follow_symlinks=False):
                if depth < max_depth:
                    stack.append((Path(e.path), depth + 1))
            elif e.name.lower().endswith(ARCHIVE_SUFFIXES):
                found.append(Path(e.path))
    return sorted(found)


def find_odb_for(designators: set[str], near: str | Path) -> tuple[OdbJob | None, list[str]]:
    """Find the ODB++ job near ``near`` (a P&P file's folder) that best matches ``designators``.

    Searches the folder and its subfolders, then the parent folder. The job sharing the most
    designators wins; ties go to the most recently modified. Returns (job or None, notes).
    """
    near = Path(near)
    notes: list[str] = []
    for root, depth in ((near, 3), (near.parent, 2)):
        best: tuple | None = None
        for cand in find_odb_jobs(root, depth):
            try:
                job = load_odb(cand)
            except (OSError, OdbError, ValueError, tarfile.TarError, zipfile.BadZipFile):
                continue  # not an ODB++ job with components (e.g. a board house's flattened job)
            overlap = len(designators & set(job.components))
            if overlap == 0:
                continue
            key = (overlap, cand.stat().st_mtime)
            if best is None or key > best[0]:
                best = (key, job)
        if best is not None:
            return best[1], notes
    notes.append(f"No ODB++ job with matching components found in or below {near}.")
    return None, notes
