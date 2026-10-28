#!/usr/bin/env python3
"""Builds, and reads back, one CalculiX structural/thermal case on an uploaded solid.

Called by run_fea.sh. Usage:
  fea_case.py mesh <job_dir>              repair job_dir/geometry.stl, mesh it, write mesh.inp
  fea_case.py deck <job_dir>              write model.inp (and modal.inp) from case.json + mesh.inp
  fea_case.py results <job_dir>           read model.dat (and modal.dat) into status.json and
                                          view.json (the surface with the solution, for the browser)
  fea_case.py status <job_dir> <state> [message]
  fea_case.py info <job_dir>              static facts about the job (for the monitor)
  fea_case.py progress <job_dir> <log_offset> <coeff_offset> [final]
  fea_case.py summary <jobs_dir> <job_id>...

Units inside the solver: mm, N, tonne, s, degC (so MPa for stress, tonne/mm3 for density).
`mesh` needs numpy, pyvista and pytetwild (setup.sh puts them in ~/icarus/venv); the other
commands need numpy only (and `info`, `progress`, `summary`, `status` nothing).
"""

import json
import math
import os
import re
import struct
import sys
import time
from pathlib import Path

# --- inputs ---------------------------------------------------------------------------------

UNIT_TO_MM = {"mm": 1.0, "m": 1000.0, "in": 25.4}
# fTetWild edge length (as a fraction of the model's diagonal) and surface tolerance.
MESH_LEVELS = {
    "coarse": (0.07, 0.008),
    "medium": (0.05, 0.003),
    "fine": (0.035, 0.002),
}
# CalculiX's direct solver needs memory in proportion to the mesh; this machine class tops out here.
MAX_ELEMENTS = int(os.environ.get("ICARUS_FEA_MAX_ELEMENTS", "70000"))
MIN_ELEMENTS = 1500
STAGES = ["starting", "mesh", "deck", "solve", "modal", "results"]
AXES = {"x": 0, "y": 1, "z": 2}

# Typical handbook values (about +/- 10%): temperature (degC) tables. E in GPa, alpha is the
# mean expansion from 20 degC in 1e-6/K, k in W/mK, cp in J/kgK, yield is the 0.2% proof stress.
MATERIALS = {
    "inconel718": {
        "name": "Inconel 718 (age hardened)",
        "density": 8190,
        "nu": 0.29,
        "T": [20, 100, 200, 300, 400, 500, 600, 650, 700, 760, 850, 900],
        "E": [200, 196, 190, 184, 179, 172, 166, 163, 159, 154, 146, 141],
        "alpha": [13.0, 13.0, 13.1, 13.3, 13.7, 14.1, 14.5, 14.8, 15.1, 15.4, 16.0, 16.3],
        "k": [11.4, 12.5, 14.0, 15.5, 17.0, 18.7, 20.3, 21.2, 22.0, 23.0, 24.7, 25.5],
        "cp": [435, 445, 455, 470, 490, 510, 525, 538, 550, 570, 600, 620],
        "yield": [1100, 1070, 1040, 1020, 1010, 990, 970, 960, 930, 850, 520, 300],
        "service_limit": 700,
    },
    "ti6al4v": {
        "name": "Ti-6Al-4V (annealed)",
        "density": 4430,
        "nu": 0.342,
        "T": [20, 100, 200, 300, 400, 500, 600],
        "E": [114, 110, 105, 100, 95, 90, 84],
        "alpha": [8.6, 8.8, 9.0, 9.2, 9.4, 9.6, 9.8],
        "k": [6.7, 7.3, 8.7, 10.3, 11.8, 13.3, 15.0],
        "cp": [526, 546, 584, 620, 650, 680, 700],
        "yield": [880, 800, 700, 620, 560, 500, 430],
        "service_limit": 400,
    },
    "aluminium7075": {
        "name": "Aluminium 7075-T6",
        "density": 2810,
        "nu": 0.33,
        "T": [20, 100, 150, 200, 250],
        "E": [71.7, 69.5, 67.5, 65.0, 62.0],
        "alpha": [23.4, 23.9, 24.3, 24.7, 25.1],
        "k": [130, 135, 140, 145, 150],
        "cp": [960, 990, 1010, 1040, 1060],
        "yield": [503, 460, 380, 220, 100],
        "service_limit": 120,
    },
    "steel4340": {
        "name": "Steel 4340 (quenched and tempered)",
        "density": 7850,
        "nu": 0.29,
        "T": [20, 100, 200, 300, 400, 500, 600],
        "E": [205, 202, 198, 193, 187, 180, 170],
        "alpha": [12.3, 12.6, 12.9, 13.3, 13.7, 14.1, 14.5],
        "k": [44.5, 44.0, 42.5, 40.5, 38.0, 35.5, 32.0],
        "cp": [475, 490, 510, 530, 560, 590, 640],
        "yield": [1150, 1100, 1050, 1000, 900, 750, 520],
        "service_limit": 450,
    },
}


def read_case(job_dir: Path) -> dict:
    return json.loads((job_dir / "case.json").read_text())


def yield_strength(material: dict, temperature):
    import numpy as np

    return np.interp(temperature, material["T"], material["yield"])


# --- status ---------------------------------------------------------------------------------

def write_status(job_dir: Path, state: str, message: str | None, result: dict | None = None) -> None:
    payload = {"status": state}
    if message:
        payload["error"] = message
    if result is not None:
        payload["result"] = result
    tmp = job_dir / "status.json.tmp"
    tmp.write_text(json.dumps(payload))
    tmp.replace(job_dir / "status.json")


# --- STL ------------------------------------------------------------------------------------

def read_stl(path: Path):
    """The STL's triangles as an (n, 3, 3) float array (binary or ASCII)."""
    import numpy as np

    data = path.read_bytes()
    if len(data) >= 84:
        count = struct.unpack("<I", data[80:84])[0]
        if 84 + 50 * count == len(data):
            record = np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")])
            return np.frombuffer(data, record, count=count, offset=84)["v"].reshape(-1, 3, 3).astype(float)
    found = re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data)
    if not found or len(found) % 3:
        raise SystemExit("geometry.stl isn't a valid STL file")
    return np.array(found, dtype=float).reshape(-1, 3, 3)


def clean_triangles(triangles):
    """Drops zero-area triangles and triangles stored twice (a common defect that makes the
    surface non-manifold). Returns the triangles and a list of what was done."""
    import numpy as np

    notes = []
    area = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1)
    keep = area > 1e-12
    if (~keep).any():
        notes.append(f"dropped {int((~keep).sum())} zero-area triangles")
    triangles = triangles[keep]
    scale = max(float(np.ptp(triangles.reshape(-1, 3), axis=0).max()), 1e-9) * 1e-6
    snapped = np.round(triangles.reshape(-1, 3) / scale).astype(np.int64)
    _, vertex = np.unique(snapped, axis=0, return_inverse=True)
    # A triangle is the same triangle however it's wound: compare its sorted vertex ids.
    rows = np.sort(vertex.reshape(-1, 3), axis=1)
    _, inverse, counts = np.unique(rows, axis=0, return_inverse=True, return_counts=True)
    doubled = counts[inverse.ravel()] > 1
    if doubled.any():
        notes.append(f"dropped {int(doubled.sum())} triangles that were stored twice")
        triangles = triangles[~doubled]
    return triangles, notes


def surface_problems(triangles) -> list[str]:
    """Open edges and edges shared by more than two triangles: what a mesher stumbles on."""
    import numpy as np

    scale = max(float(np.ptp(triangles.reshape(-1, 3), axis=0).max()), 1e-9) * 1e-6
    snapped = np.round(triangles.reshape(-1, 3) / scale).astype(np.int64)
    _, inverse = np.unique(snapped, axis=0, return_inverse=True)
    faces = inverse.reshape(-1, 3)
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    _, uses = np.unique(edges, axis=0, return_counts=True)
    problems = []
    if (uses == 1).any():
        problems.append(f"the surface has {int((uses == 1).sum())} open edges")
    if (uses > 2).any():
        problems.append(f"{int((uses > 2).sum())} edges are shared by more than two triangles")
    return problems


# --- meshing --------------------------------------------------------------------------------

def tet_mesh(triangles_mm, level: str):
    """Tetrahedra for a closed surface with fTetWild; coarsens until the mesh fits the solver."""
    import numpy as np
    import pytetwild
    import pyvista as pv

    points = triangles_mm.reshape(-1, 3)
    unique, inverse = np.unique(np.round(points, 6), axis=0, return_inverse=True)
    faces = np.hstack([np.full((len(inverse) // 3, 1), 3), inverse.reshape(-1, 3)]).ravel()
    surface = pv.PolyData(unique, faces)
    edge, epsilon = MESH_LEVELS[level]
    notes = []
    for attempt in range(4):
        grid = pytetwild.tetrahedralize_pv(surface, edge_length_fac=edge, epsilon=epsilon, optimize=True, quiet=True)
        tets = grid.cells_dict[10]
        if len(tets) <= MAX_ELEMENTS:
            break
        notes.append(f"{len(tets):,} elements is more than this machine's solver handles; remeshed coarser")
        edge, epsilon = edge * 1.5, epsilon * 2
    else:
        raise SystemExit(f"the mesh stayed above {MAX_ELEMENTS:,} elements; use mesh 'coarse' or a simpler model")
    if len(tets) < MIN_ELEMENTS:
        notes.append(f"only {len(tets):,} elements: the mesh is coarse and peak stresses are unreliable")
    # The mesher may move the surface by up to its tolerance (epsilon, a fraction of the diagonal).
    diagonal = float(np.linalg.norm(np.ptp(points, axis=0)))
    return np.asarray(grid.points), tets, notes, epsilon * diagonal


def write_second_order(nodes, tets, path: Path) -> int:
    """Writes the mesh as CalculiX C3D10 elements (mid-edge nodes added); returns the node count."""
    import numpy as np

    a, b, c, d = (nodes[tets[:, i]] for i in range(4))
    left_handed = np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a) < 0
    tets = tets.copy()
    tets[left_handed] = tets[left_handed][:, [0, 2, 1, 3]]
    pairs = [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]
    edges = np.sort(np.concatenate([tets[:, [i, j]] for i, j in pairs]), axis=1)
    unique, inverse = np.unique(edges, axis=0, return_inverse=True)
    mid_nodes = (nodes[unique[:, 0]] + nodes[unique[:, 1]]) / 2
    mid_ids = len(nodes) + inverse.reshape(len(pairs), len(tets)).T
    elements = np.hstack([tets, mid_ids])
    points = np.vstack([nodes, mid_nodes])
    with path.open("w") as f:
        f.write("*NODE, NSET=NALL\n")
        for i, p in enumerate(points, 1):
            f.write(f"{i}, {p[0]:.6f}, {p[1]:.6f}, {p[2]:.6f}\n")
        f.write("*ELEMENT, TYPE=C3D10, ELSET=EALL\n")
        for i, row in enumerate(elements + 1, 1):
            f.write(f"{i}, " + ", ".join(map(str, row[:8])) + ",\n   " + ", ".join(map(str, row[8:])) + "\n")
    return len(points)


def do_mesh(job_dir: Path) -> None:
    import numpy as np

    case = read_case(job_dir)
    triangles = read_stl(job_dir / "geometry.stl") * UNIT_TO_MM[case["units"]]
    if len(triangles) == 0:
        raise SystemExit("geometry.stl has no triangles")
    triangles, notes = clean_triangles(triangles)
    problems = surface_problems(triangles)
    if problems:
        notes.append("the surface isn't a clean closed solid (" + "; ".join(problems) + "); the mesher repaired it")
    nodes, tets, mesh_notes, tolerance = tet_mesh(triangles, case["mesh"])
    notes += mesh_notes
    count = write_second_order(nodes, tets, job_dir / "mesh.inp")
    a, b, c, d = (nodes[tets[:, i]] for i in range(4))
    volume = float(np.abs(np.einsum("ij,ij->i", b - a, np.cross(c - a, d - a))).sum() / 6)
    low, high = nodes.min(axis=0), nodes.max(axis=0)
    (job_dir / "mesh.json").write_text(
        json.dumps({
            "elements": int(len(tets)), "nodes": count, "volume_mm3": volume,
            "bbox_min": low.tolist(), "bbox_max": high.tolist(), "notes": notes,
            # The solid's own extent, and how far the mesher may have moved its surface.
            "stl_bbox_min": triangles.reshape(-1, 3).min(axis=0).tolist(),
            "stl_bbox_max": triangles.reshape(-1, 3).max(axis=0).tolist(),
            "surface_tolerance_mm": tolerance,
        })
    )
    print(f"{len(tets):,} tetrahedra, {count:,} nodes, volume {volume:.1f} mm3")
    for note in notes:
        print("note:", note)


# --- deck -----------------------------------------------------------------------------------

FACES = [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)]  # C3D10 faces F1..F4 by corner position


def read_mesh(path: Path):
    import numpy as np

    nodes, elements, mode, pending = [], [], None, []
    for line in path.open():
        line = line.strip()
        if line.startswith("*"):
            mode = "node" if line.upper().startswith("*NODE") else "element" if line.upper().startswith("*ELEMENT") else None
            continue
        if mode == "node":
            _, x, y, z = line.split(",")
            nodes.append((float(x), float(y), float(z)))
        elif mode == "element":
            pending += [int(v) for v in line.strip(",").split(",") if v.strip()]
            if len(pending) == 11:
                elements.append([v - 1 for v in pending[1:]])
                pending = []
    return np.array(nodes), np.array(elements)


def _free_faces(nodes, elements):
    """The faces on the mesh's outside: (owner element and face index, corner node ids, outward
    unit normal, area). The corner ids are wound so the right-hand normal points outward."""
    import numpy as np

    corner = elements[:, :4]
    keys, owners = [], []
    for f, idx in enumerate(FACES):
        keys.append(np.sort(corner[:, list(idx)], axis=1))
        owners.append(np.column_stack([np.arange(len(corner)), np.full(len(corner), f)]))
    keys, owners = np.vstack(keys), np.vstack(owners)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    owner = owners[counts[inverse.ravel()] == 1]
    tri = np.array([corner[e][list(FACES[f])] for e, f in owner])
    p = nodes[tri]
    cross = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    area = np.linalg.norm(cross, axis=1) / 2
    normal = cross / (2 * area[:, None])
    opposite = np.array([nodes[corner[e][[i for i in range(4) if i not in FACES[f]][0]]] for e, f in owner])
    inward = np.einsum("ij,ij->i", normal, opposite - p.mean(axis=1)) > 0
    normal[inward] *= -1
    wound = tri.copy()
    wound[inward] = tri[inward][:, [0, 2, 1]]
    return owner, wound, normal, area


def surface_faces(nodes, elements):
    """Free faces of the mesh: (element number, face number, outward unit normal, area)."""
    owner, _, normal, area = _free_faces(nodes, elements)
    return owner[:, 0] + 1, owner[:, 1] + 1, normal, area


def surface_triangles(nodes, elements):
    """The outside of the mesh as triangles of corner node ids, wound outward."""
    return _free_faces(nodes, elements)[1]


def material_block(material: dict, temperature_dependent: bool) -> str:
    out = [f"*MATERIAL, NAME=MAT", "*DENSITY", f"{material['density'] * 1e-12:.6e}"]
    if temperature_dependent:
        out += ["*ELASTIC"] + [f"{e * 1e3:.0f}, {material['nu']}, {t}" for e, t in zip(material["E"], material["T"])]
        out += ["*EXPANSION, ZERO=20"] + [f"{a * 1e-6:.4e}, {t}" for a, t in zip(material["alpha"], material["T"])]
        out += ["*CONDUCTIVITY"] + [f"{k}, {t}" for k, t in zip(material["k"], material["T"])]
        out += ["*SPECIFIC HEAT"] + [f"{c * 1e6:.4e}, {t}" for c, t in zip(material["cp"], material["T"])]
    else:
        out += ["*ELASTIC", f"{material['E'][0] * 1e3:.0f}, {material['nu']}", "*EXPANSION, ZERO=20", f"{material['alpha'][0] * 1e-6:.4e}"]
    return "\n".join(out)


def nset(name: str, ids) -> str:
    lines = [f"*NSET, NSET={name}"]
    ids = list(ids)
    for i in range(0, len(ids), 12):
        lines.append(", ".join(map(str, ids[i:i + 12])))
    return "\n".join(lines)


def do_deck(job_dir: Path) -> None:
    import numpy as np

    case = read_case(job_dir)
    material = MATERIALS[case["material"]]
    nodes, elements = read_mesh(job_dir / "mesh.inp")
    mesh_info = json.loads((job_dir / "mesh.json").read_text())
    el, fc, normal, area = surface_faces(nodes, elements)
    axis = AXES[case["support_axis"]]
    # The support plane is the solid's own extreme (the mesher may have moved the surface a little,
    # and a few stray nodes beyond it must not decide where the plane is); nodes within the
    # mesher's tolerance of it are held.
    extreme = mesh_info["stl_bbox_min" if case["support_side"] == "min" else "stl_bbox_max"]
    plane = extreme[axis]
    tol = max(1.5 * mesh_info["surface_tolerance_mm"], 0.02)
    held = np.abs(nodes[:, axis] - plane) <= tol
    support = np.nonzero(held)[0] + 1
    corner_in_support = held[surface_triangles(nodes, elements)].all(axis=1)
    support_area = float(_free_faces(nodes, elements)[3][corner_in_support].sum())
    if len(support) < 3 or support_area < 1e-3 * float(area.sum()):
        raise SystemExit(
            f"no flat face of the solid lies on the {case['support_side']} end along "
            f"{case['support_axis']}; the model can't be held there (try the other end or axis)"
        )

    thermal = case.get("gas_temperature_c") is not None
    deck = ["*INCLUDE, INPUT=mesh.inp", nset("NSUPPORT", support), "*ELSET, ELSET=EALL, GENERATE", f"1, {len(elements)}",
            material_block(material, temperature_dependent=thermal), "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT"]
    if thermal:
        deck += ["*INITIAL CONDITIONS, TYPE=TEMPERATURE", f"NALL, {case['base_temperature_c']}"]
    deck += ["*BOUNDARY", "NSUPPORT, 1, 3"]
    if thermal:
        deck += ["*BOUNDARY", f"NSUPPORT, 11, 11, {case['base_temperature_c']}"]
        deck += ["*STEP, INC=100", "*UNCOUPLED TEMPERATURE-DISPLACEMENT, STEADY STATE"]
    else:
        deck += ["*STEP", "*STATIC"]
    loads = []
    omega2 = (2 * math.pi * case["spin_rpm"] / 60) ** 2
    scale = UNIT_TO_MM[case["units"]]
    if case["spin_rpm"]:
        o = [v * scale for v in case["spin_axis_origin"]]
        d = case["spin_axis_direction"]
        loads.append(f"EALL, CENTRIF, {omega2:.6e}, {o[0]:.4f}, {o[1]:.4f}, {o[2]:.4f}, {d[0]}, {d[1]}, {d[2]}")
    if case["pressure_mpa"]:
        loads += [f"{e}, P{f}, {case['pressure_mpa']:.6e}" for e, f in zip(el, fc)]
    if loads:
        deck += ["*DLOAD"] + loads
    if thermal:
        h = case["htc_w_m2k"] * 1e-3  # W/m2K -> N/(mm s K)
        deck += ["*FILM"] + [f"{e}, F{f}, {case['gas_temperature_c']}, {h:.6e}" for e, f in zip(el, fc)]
    deck += ["*NODE PRINT, NSET=NALL, FREQUENCY=1", "U", "*EL PRINT, ELSET=EALL, FREQUENCY=1", "S"]
    if thermal:
        deck += ["*NODE PRINT, NSET=NALL, FREQUENCY=1", "NT"]
    deck += ["*END STEP"]
    (job_dir / "model.inp").write_text("\n".join(deck) + "\n")

    if case["modal"]:
        mean_t = (case["base_temperature_c"] + case["gas_temperature_c"]) / 2 if thermal else 20.0
        modal = ["*INCLUDE, INPUT=mesh.inp", nset("NSUPPORT", support), "*ELSET, ELSET=EALL, GENERATE", f"1, {len(elements)}",
                 material_block(material, temperature_dependent=True), "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT",
                 "*INITIAL CONDITIONS, TYPE=TEMPERATURE", f"NALL, {mean_t}", "*BOUNDARY", "NSUPPORT, 1, 3",
                 "*STEP, NLGEOM", "*STATIC", "*TEMPERATURE", f"NALL, {mean_t}"]
        if case["spin_rpm"]:
            modal += ["*DLOAD", loads[0]]
        modal += ["*END STEP", "*STEP", "*FREQUENCY, STORAGE=YES", "8", "*END STEP"]
        (job_dir / "modal.inp").write_text("\n".join(modal) + "\n")
    (job_dir / "deck.json").write_text(json.dumps({
        "support_nodes": int(len(support)), "support_plane": float(plane), "support_axis": axis,
        "support_area_mm2": support_area, "surface_faces": int(len(el)),
    }))
    print(f"deck written: {len(support)} support nodes, {len(el)} surface faces, thermal={thermal}")


# --- results --------------------------------------------------------------------------------

def read_blocks(path: Path) -> dict:
    import numpy as np

    blocks, name = {}, None
    for line in path.open():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("displacements"):
            name = "u"
        elif low.startswith("stresses"):
            name = "s"
        elif low.startswith("temperatures"):
            name = "t"
        elif low.startswith(("total", "forces")):
            name = None
        elif name:
            try:
                blocks.setdefault(name, []).append([float(v) for v in s.split()])
            except ValueError:
                name = None
    return {k: np.array(v) for k, v in blocks.items()}


def read_frequencies(path: Path) -> list[float]:
    """Natural frequencies in Hz from a CalculiX frequency step's .dat."""
    freqs, reading = [], False
    for line in path.open():
        if "E I G E N V A L U E" in line:
            reading = True
            continue
        if reading:
            parts = line.split()
            if len(parts) >= 4 and parts[0].isdigit():
                freqs.append(float(parts[3]))
            elif freqs and not parts:
                break
    return freqs


def build_results(job_dir: Path, blocks: dict | None = None) -> dict:
    import numpy as np

    case = read_case(job_dir)
    material = MATERIALS[case["material"]]
    mesh_info = json.loads((job_dir / "mesh.json").read_text())
    deck_info = json.loads((job_dir / "deck.json").read_text())
    nodes, elements = read_mesh(job_dir / "mesh.inp")
    blocks = blocks if blocks is not None else read_blocks(job_dir / "model.dat")
    if "s" not in blocks or "u" not in blocks:
        raise SystemExit("model.dat has no stresses or displacements (did the solve finish?)")
    u, s = blocks["u"], blocks["s"]
    s11, s22, s33, s12, s13, s23 = (s[:, 2 + i] for i in range(6))
    vm = np.sqrt(0.5 * ((s11 - s22) ** 2 + (s22 - s33) ** 2 + (s33 - s11) ** 2) + 3 * (s12**2 + s13**2 + s23**2))
    element_vm = np.zeros(len(elements))
    np.maximum.at(element_vm, s[:, 0].astype(int) - 1, vm)
    centroid = nodes[elements[:, :4]].mean(axis=1)
    thermal = "t" in blocks
    if thermal:
        temp = np.zeros(len(nodes))
        temp[blocks["t"][:, 0].astype(int) - 1] = blocks["t"][:, 1]
        element_t = temp[elements[:, :4]].mean(axis=1)
    else:
        element_t = np.full(len(elements), 20.0)
    safety = yield_strength(material, element_t) / np.maximum(element_vm, 1e-9)

    axis = deck_info["support_axis"]
    extent = float(nodes[:, axis].max() - nodes[:, axis].min())
    away = np.abs(centroid[:, axis] - deck_info["support_plane"]) > 0.1 * extent  # not near the clamp
    peak = int(np.argmax(element_vm))
    peak_away = int(np.argmax(np.where(away, element_vm, -1)))
    displacement = np.linalg.norm(u[:, 1:4], axis=1)
    top = int(np.argmax(displacement))
    result = {
        "material": material["name"],
        "elements": mesh_info["elements"],
        "nodes": mesh_info["nodes"],
        "volume_mm3": round(mesh_info["volume_mm3"], 2),
        "mass_kg": round(mesh_info["volume_mm3"] * material["density"] * 1e-9, 6),
        "bounding_box_mm": [[round(v, 3) for v in mesh_info["bbox_min"]], [round(v, 3) for v in mesh_info["bbox_max"]]],
        "max_displacement_mm": round(float(displacement[top]), 5),
        "max_displacement_at_mm": [round(float(v), 2) for v in nodes[int(u[top, 0]) - 1]],
        "max_von_mises_mpa": round(float(element_vm[peak]), 1),
        "max_von_mises_at_mm": [round(float(v), 2) for v in centroid[peak]],
        "max_von_mises_away_from_support_mpa": round(float(element_vm[peak_away]), 1),
        "max_von_mises_away_at_mm": [round(float(v), 2) for v in centroid[peak_away]],
        "typical_von_mises_mpa": round(float(np.percentile(element_vm[away], 95)), 1),
        "min_safety_factor_yield_away_from_support": round(float(safety[away].min()), 2),
        "min_safety_factor_yield_anywhere": round(float(safety.min()), 2),
        "peak_stress_note": "Stress at the clamped support is a constraint artefact; judge the part by the values 'away from support'.",
    }
    warnings = list(mesh_info["notes"])
    if thermal:
        result["temperature_c"] = [round(float(blocks["t"][:, 1].min()), 1), round(float(blocks["t"][:, 1].max()), 1)]
        if blocks["t"][:, 1].max() > material["service_limit"]:
            warnings.append(
                f"the metal reaches {blocks['t'][:, 1].max():.0f} C, above the usual service limit of "
                f"{material['name']} (about {material['service_limit']} C): creep and rupture limit the life, "
                "which this analysis does not model"
            )
    size = float(np.ptp(nodes, axis=0).max())
    if result["max_displacement_mm"] > 0.25 * size:
        warnings.append(
            f"the displacement ({result['max_displacement_mm']:.3g} mm) is over a quarter of the part's "
            "size, which is not a believable small-deflection result: check the support, the loads "
            "and the units before trusting any number here"
        )
    if result["min_safety_factor_yield_away_from_support"] < 1:
        warnings.append("the stress exceeds yield away from the support: the part yields")
    elif result["min_safety_factor_yield_away_from_support"] < 1.5:
        warnings.append("safety factor against yield is below 1.5 away from the support")
    modal_dat = job_dir / "modal.dat"
    if modal_dat.exists():
        freqs = read_frequencies(modal_dat)
        result["natural_frequencies_hz"] = [round(f, 1) for f in freqs[:6]]
        if case["spin_rpm"]:
            run_hz = case["spin_rpm"] / 60
            result["engine_order_hz"] = {f"{k}E": round(run_hz * k, 1) for k in (1, 2, 3, 4)}
            close = [f"{f:.0f} Hz is within 10% of {k}E ({run_hz * k:.0f} Hz)" for f in freqs[:4] for k in (1, 2, 3, 4) if abs(f - run_hz * k) < 0.1 * run_hz * k]
            if close:
                warnings.append("possible resonance: " + "; ".join(close))
    if warnings:
        result["warnings"] = warnings
    return result


def _encode(array, dtype) -> str:
    import base64

    import numpy as np

    return base64.b64encode(np.ascontiguousarray(array, dtype=dtype).tobytes()).decode()


def build_view(job_dir: Path, blocks: dict | None = None) -> dict:
    """The part's outside with the solution on it, for the browser to draw: positions (mm),
    displacements (mm), von Mises stress (MPa) and temperature (degC) at the surface's nodes,
    and its triangles. Arrays are base64 little-endian float32 (index: uint32)."""
    import numpy as np

    nodes, elements = read_mesh(job_dir / "mesh.inp")
    blocks = blocks if blocks is not None else read_blocks(job_dir / "model.dat")
    triangles = surface_triangles(nodes, elements)
    used, index = np.unique(triangles, return_inverse=True)

    s = blocks["s"]
    s11, s22, s33, s12, s13, s23 = (s[:, 2 + i] for i in range(6))
    vm = np.sqrt(0.5 * ((s11 - s22) ** 2 + (s22 - s33) ** 2 + (s33 - s11) ** 2) + 3 * (s12**2 + s13**2 + s23**2))
    element_vm = np.zeros(len(elements))
    np.maximum.at(element_vm, s[:, 0].astype(int) - 1, vm)
    # Stress is known per element; give each node the average of the elements around it.
    total, count = np.zeros(len(nodes)), np.zeros(len(nodes))
    corner = elements[:, :4]
    np.add.at(total, corner.ravel(), np.repeat(element_vm, 4))
    np.add.at(count, corner.ravel(), 1)
    node_vm = total / np.maximum(count, 1)

    displacement = np.zeros((len(nodes), 3))
    displacement[blocks["u"][:, 0].astype(int) - 1] = blocks["u"][:, 1:4]
    fields = {
        "von_mises": node_vm[used],
        "displacement": np.linalg.norm(displacement[used], axis=1),
    }
    if "t" in blocks:
        temperature = np.zeros(len(nodes))
        temperature[blocks["t"][:, 0].astype(int) - 1] = blocks["t"][:, 1]
        fields["temperature"] = temperature[used]
    view = {
        "vertices": int(len(used)),
        "triangles": int(len(triangles)),
        "position": _encode(nodes[used], "<f4"),
        "displacement_vector": _encode(displacement[used], "<f4"),
        "index": _encode(index.reshape(-1, 3), "<u4"),
        "fields": {
            name: {
                "values": _encode(values, "<f4"),
                "min": float(values.min()),
                "max": float(values.max()),
                # Where most of the part lies: a clamp's stress spike shouldn't set the colours.
                "p98": float(np.percentile(values, 98)),
                "unit": {"von_mises": "MPa", "displacement": "mm", "temperature": "°C"}[name],
            }
            for name, values in fields.items()
        },
        "bbox_min": nodes[used].min(axis=0).tolist(),
        "bbox_max": nodes[used].max(axis=0).tolist(),
    }
    return view


def describe_results(result: dict) -> str:
    return (
        f"max von Mises {result['max_von_mises_mpa']} MPa ({result['max_von_mises_away_from_support_mpa']} MPa away from the "
        f"support), max displacement {result['max_displacement_mm']} mm, safety factor "
        f"{result['min_safety_factor_yield_away_from_support']}"
    )


# --- monitor (what the backend asks while a job runs) ----------------------------------------

def present_stages(job_dir: Path) -> list[str]:
    return [stage for stage in STAGES if stage != "starting" and (job_dir / f"log.{stage}").exists()]


def current_stage(job_dir: Path, status: str) -> str:
    stages = present_stages(job_dir)
    if status == "completed":
        return "results"
    return stages[-1] if stages else "starting"


def read_status(job_dir: Path) -> dict:
    try:
        return json.loads((job_dir / "status.json").read_text())
    except (OSError, ValueError):
        return {"status": "running"}


def build_info(job_dir: Path) -> dict:
    case = read_case(job_dir)
    info = {"case": case, "stages_expected": [s for s in STAGES if s != "starting" and (s != "modal" or case["modal"])]}
    mesh = job_dir / "mesh.json"
    if mesh.exists():
        meta = json.loads(mesh.read_text())
        info["mesh"] = {"elements": meta["elements"], "nodes": meta["nodes"], "notes": meta["notes"]}
    return info


def build_progress(job_dir: Path, log_offset: int, final: bool) -> dict:
    status = read_status(job_dir)["status"]
    stage = current_stage(job_dir, status)
    log = job_dir / f"log.{stage}"
    lines: list[str] = []
    if log.exists():
        text = log.read_text(errors="replace").splitlines()
        lines = text[-12:]
    expected = [s for s in STAGES if s != "starting"]
    index = expected.index(stage) if stage in expected else 0
    done = status == "completed"
    return {
        "stage": stage,
        "fraction": 1.0 if done else round(min(index / len(expected), 0.95), 3),
        "eta_s": None,
        "log_offset": log_offset,
        "coeff_offset": 0,
        "lines": lines,
    }


def build_summary(jobs_dir: Path, job_ids: list[str]) -> dict:
    out = {}
    for job_id in job_ids:
        job_dir = jobs_dir / job_id
        if not job_dir.is_dir():
            continue
        status = read_status(job_dir)["status"]
        progress = build_progress(job_dir, 0, False)
        out[job_id] = {"status": status, "stage": progress["stage"], "fraction": progress["fraction"], "eta_s": None}
    return out


def main(argv: list[str]) -> None:
    if len(argv) < 3:
        raise SystemExit(__doc__)
    command, job_dir = argv[1], Path(argv[2])
    if command == "mesh":
        do_mesh(job_dir)
    elif command == "deck":
        do_deck(job_dir)
    elif command == "results":
        blocks = read_blocks(job_dir / "model.dat")
        result = build_results(job_dir, blocks)
        try:  # the 3D view is a bonus: the results stand without it. It's written first so the
            # browser finds it as soon as the job shows as completed.
            (job_dir / "view.json").write_text(json.dumps(build_view(job_dir, blocks)))
        except Exception as error:  # noqa: BLE001
            print(f"couldn't write the 3D view: {error}")
        write_status(job_dir, "completed", None, result)
        print(describe_results(result))
    elif command == "info":
        print(json.dumps(build_info(job_dir)))
    elif command == "summary":
        print(json.dumps(build_summary(job_dir, argv[3:])))
    elif command == "progress":
        print(json.dumps(build_progress(job_dir, int(argv[3]), len(argv) > 5 and argv[5] == "final")))
    elif command == "status":
        write_status(job_dir, argv[3], argv[4] if len(argv) > 4 else None)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
