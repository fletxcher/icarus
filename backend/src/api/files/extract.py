"""Turns an uploaded file into the text the model reads.

Text files go in as they are. The rest can't be pasted into a prompt, so the model gets a
summary instead: a mesh's size and shape (STL, OBJ), a CAD file's contents (STEP), or a
PDF's extracted text. The original bytes of those are kept with the file.
"""

import csv
import io
import re
import struct
from collections import Counter
from dataclasses import dataclass
from typing import Literal

import numpy as np
from pypdf import PdfReader
from pypdf.errors import PyPdfError

Kind = Literal["text", "csv", "stl", "obj", "step", "pdf"]

# Extensions that are always treated as these kinds; any other file is accepted as "text" if
# it decodes as UTF-8.
EXTENSIONS: dict[str, Kind] = {
    ".stl": "stl",
    ".obj": "obj",
    ".step": "step",
    ".stp": "step",
    ".pdf": "pdf",
    ".csv": "csv",
}
# Kinds whose original bytes are stored (the text is only a summary of them).
BINARY_KINDS: set[Kind] = {"stl", "obj", "step", "pdf"}
SUPPORTED = "text files (.dat, .csv, .txt, .json, ...), .stl, .obj, .step/.stp and .pdf"

MAX_TRIANGLES = 3_000_000
PDF_MAX_PAGES = 60
PDF_MAX_CHARS = 200_000
STEP_POINT_LIMIT = 500_000


class Unreadable(Exception):
    """The file isn't what its name says, or can't be read; the message is for the user."""


@dataclass(frozen=True)
class Extracted:
    kind: Kind
    text: str


def kind_of(name: str) -> Kind:
    for extension, kind in EXTENSIONS.items():
        if name.lower().endswith(extension):
            return kind
    return "text"


def decode_text(data: bytes) -> str | None:
    """The file as text, or None for binary files."""
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def extract(name: str, data: bytes) -> Extracted:
    """Raises Unreadable."""
    kind = kind_of(name)
    if kind in ("text", "csv"):
        text = decode_text(data)
        if text is None:
            raise Unreadable(f"{name} isn't a file I can read; supported: {SUPPORTED}")
        return Extracted(kind, _csv_summary(text) + text if kind == "csv" else text)
    summarize = {"stl": _stl, "obj": _obj, "step": _step, "pdf": _pdf}[kind]
    return Extracted(kind, summarize(name, data))


def _fmt(value: float) -> str:
    return f"{value:.6g}"


def _box(points: np.ndarray) -> str:
    low, high = points.min(axis=0), points.max(axis=0)
    size = high - low
    ranges = ", ".join(
        f"{axis} {_fmt(a)}..{_fmt(b)}" for axis, a, b in zip("xyz", low, high, strict=True)
    )
    extents = " x ".join(_fmt(v) for v in size)
    return f"bounding box {ranges} (extents {extents})"


def _csv_summary(text: str) -> str:
    try:
        rows = list(csv.reader(io.StringIO(text), dialect=csv.Sniffer().sniff(text[:4096])))
    except csv.Error:
        rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return ""
    return (
        f"[CSV: {len(rows)} rows, {len(rows[0])} columns; first row: {', '.join(rows[0][:12])}]\n"
    )


# --- STL ----------------------------------------------------------------------------------


def _stl_triangles(name: str, data: bytes) -> tuple[np.ndarray, str]:
    if len(data) >= 84:
        count = struct.unpack("<I", data[80:84])[0]
        if 84 + 50 * count == len(data):
            record = np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")])
            return np.frombuffer(data, record, count=count, offset=84)["v"].reshape(
                -1, 3, 3
            ).astype(float), "binary"
    if data.lstrip()[:5].lower() == b"solid":
        found = re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data)
        if found and len(found) % 3 == 0:
            try:
                return np.array(found, dtype=float).reshape(-1, 3, 3), "ASCII"
            except ValueError:
                pass
    raise Unreadable(f"{name} isn't a valid STL file (neither binary nor ASCII STL)")


def _stl(name: str, data: bytes) -> str:
    triangles, flavour = _stl_triangles(name, data)
    count = len(triangles)
    if count == 0:
        raise Unreadable(f"{name} has no triangles")
    if count > MAX_TRIANGLES:
        raise Unreadable(f"{name} has {count:,} triangles; the limit is {MAX_TRIANGLES:,}")
    points = triangles.reshape(-1, 3)
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    area = float(np.linalg.norm(cross, axis=1).sum() / 2)
    volume = float(
        np.einsum("ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6
    )
    size = points.max(axis=0) - points.min(axis=0)
    # Vertices closer than a millionth of the model's size are the same vertex.
    snapped = np.round(points / max(float(size.max()) * 1e-6, 1e-12)).astype(np.int64)
    unique, inverse = np.unique(snapped, axis=0, return_inverse=True)
    faces = inverse.reshape(-1, 3)
    degenerate = int(
        (
            (faces[:, 0] == faces[:, 1])
            | (faces[:, 1] == faces[:, 2])
            | (faces[:, 2] == faces[:, 0])
        ).sum()
    )
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    _, uses = np.unique(edges, axis=0, return_counts=True)
    open_edges, shared = int((uses == 1).sum()), int((uses > 2).sum())
    closed = open_edges == 0 and shared == 0
    if closed:
        volume_text = f"enclosed volume {_fmt(abs(volume))}"
    elif open_edges == 0:
        volume_text = f"volume about {_fmt(abs(volume))} (some edges have over two triangles)"
    else:
        volume_text = "no enclosed volume (the surface isn't closed)"
    lines = [
        f"[STL file summary ({flavour}). The geometry itself isn't included; it was measured.]",
        f"{count:,} triangles, {len(unique):,} distinct vertices",
        _box(points) + ". Units aren't stored in an STL (millimetres are typical).",
        f"surface area {_fmt(area)}, {volume_text}",
        "watertight: yes (a closed solid)"
        if closed
        else f"watertight: no ({open_edges} open edges, {shared} edges with over two triangles)",
    ]
    if degenerate:
        lines.append(f"{degenerate} degenerate (zero-area) triangles")
    return "\n".join(lines)


# --- OBJ ----------------------------------------------------------------------------------


def _obj(name: str, data: bytes) -> str:
    text = decode_text(data)
    if text is None:
        raise Unreadable(f"{name} isn't a text OBJ file")
    vertices: list[tuple[float, float, float]] = []
    faces = triangles = normals = uvs = 0
    objects: list[str] = []
    materials: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        tag = parts[0]
        if tag == "v" and len(parts) >= 4:
            try:
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                continue
        elif tag == "f":
            faces += 1
            triangles += max(len(parts) - 3, 0)
        elif tag == "vn":
            normals += 1
        elif tag == "vt":
            uvs += 1
        elif tag in ("o", "g") and len(parts) > 1 and parts[1] not in objects:
            objects.append(parts[1])
        elif tag in ("mtllib", "usemtl") and len(parts) > 1 and parts[1] not in materials:
            materials.append(parts[1])
    if not vertices or not faces:
        raise Unreadable(f"{name} has no vertices and faces; is it an OBJ file?")
    lines = [
        "[OBJ file summary. The geometry itself isn't included; this was measured from it.]",
        f"{len(vertices):,} vertices, {faces:,} faces ({triangles:,} triangles once split), "
        f"{normals:,} normals, {uvs:,} texture coordinates",
        _box(np.array(vertices)) + ". Units aren't stored in an OBJ.",
    ]
    if objects:
        lines.append(
            f"objects/groups ({len(objects)}): "
            + ", ".join(objects[:20])
            + (" ..." if len(objects) > 20 else "")
        )
    if materials:
        lines.append("materials: " + ", ".join(materials[:10]))
    return "\n".join(lines)


# --- STEP ---------------------------------------------------------------------------------


def _step(name: str, data: bytes) -> str:
    text = data.decode("latin-1")
    if "ISO-10303-21" not in text[:200]:
        raise Unreadable(f"{name} isn't a STEP file (no ISO-10303-21 header)")
    lines = [
        "[STEP file summary. The geometry itself isn't included; this was read from the file.]"
    ]
    if match := re.search(r"FILE_NAME\s*\(\s*'([^']*)'", text):
        lines.append(f"name: {match.group(1)}")
    if match := re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']*)'", text):
        lines.append(f"schema: {match.group(1)}")
    if match := re.search(
        r"FILE_NAME\s*\([^;]*?'[^']*'\s*,\s*'[^']*'\s*,\s*\([^)]*\)\s*,\s*\([^)]*\)\s*,\s*'([^']*)'\s*,\s*'([^']*)'",
        text,
        re.S,
    ):
        origin = ", ".join(part for part in match.groups() if part)
        if origin:
            lines.append(f"made with: {origin}")
    products = list(dict.fromkeys(re.findall(r"PRODUCT\s*\(\s*'([^']*)'", text)))
    if products:
        lines.append(
            f"products ({len(products)}): "
            + ", ".join(products[:15])
            + (" ..." if len(products) > 15 else "")
        )
    entities = Counter(re.findall(r"^#\d+\s*=\s*([A-Z0-9_]+)\s*\(", text, re.M))
    interesting = {
        k: entities[k]
        for k in (
            "MANIFOLD_SOLID_BREP",
            "ADVANCED_FACE",
            "CLOSED_SHELL",
            "SHELL_BASED_SURFACE_MODEL",
            "CYLINDRICAL_SURFACE",
            "B_SPLINE_SURFACE_WITH_KNOTS",
        )
        if entities[k]
    }
    if interesting:
        lines.append(
            "geometry: "
            + ", ".join(f"{count:,} {key.lower()}" for key, count in interesting.items())
        )
    lines.append(f"{sum(entities.values()):,} entities in all")
    if re.search(r"SI_UNIT\s*\(\s*\.MILLI\.\s*,\s*\.METRE\.", text):
        lines.append("length unit: millimetres")
    elif re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'INCH'", text):
        lines.append("length unit: inches")
    elif re.search(r"SI_UNIT\s*\(\s*\$\s*,\s*\.METRE\.", text):
        lines.append("length unit: metres")
    coordinates = re.findall(
        r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(\s*([-+0-9.Ee]+)\s*,\s*([-+0-9.Ee]+)\s*,\s*([-+0-9.Ee]+)\s*\)",
        text,
    )
    if coordinates:
        points = np.array(coordinates[:STEP_POINT_LIMIT], dtype=float)
        lines.append(
            "approximate "
            + _box(points)
            + " (from the file's points, which include construction points)"
        )
    return "\n".join(lines)


# --- PDF ----------------------------------------------------------------------------------


def _pdf(name: str, data: bytes) -> str:
    if b"%PDF-" not in data[:1024]:
        raise Unreadable(f"{name} isn't a PDF file")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise Unreadable(f"{name} is password-protected")
        pages = len(reader.pages)
        chunks: list[str] = []
        total = 0
        for index in range(min(pages, PDF_MAX_PAGES)):
            page_text = (reader.pages[index].extract_text() or "").strip()
            if page_text:
                chunks.append(f"--- page {index + 1} ---\n{page_text}")
                total += len(page_text)
            if total > PDF_MAX_CHARS:
                break
        title = (reader.metadata.title if reader.metadata else None) or ""
    except Unreadable:
        raise
    except (PyPdfError, ValueError, KeyError, RecursionError, OSError) as error:
        raise Unreadable(f"couldn't read {name} as a PDF ({type(error).__name__})") from error
    head = f"[PDF: {pages} pages" + (f", titled {title!r}" if title else "") + "]"
    if not chunks:
        return head + "\nNo text could be extracted (it may be scanned images)."
    note = (
        f"\n[Text of the first {min(pages, PDF_MAX_PAGES)} pages only.]"
        if pages > PDF_MAX_PAGES
        else ""
    )
    return head + "\n" + "\n\n".join(chunks)[:PDF_MAX_CHARS] + note
