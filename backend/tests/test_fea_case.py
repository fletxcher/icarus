"""The work station's FEA case builder (workstation/fea_case.py), checked without CalculiX."""

import importlib.util
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pytest

_path = Path(__file__).resolve().parents[2] / "workstation" / "fea_case.py"
_spec = importlib.util.spec_from_file_location("fea_case", _path)
fea_case = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fea_case)

CASE = {
    "kind": "fea",
    "geometry_file_id": "f1",
    "geometry_name": "bar.stl",
    "units": "mm",
    "mesh": "medium",
    "material": "inconel718",
    "support_axis": "z",
    "support_side": "min",
    "spin_rpm": 30000,
    "spin_axis_origin": [0, 0, -50],
    "spin_axis_direction": [0, 1, 0],
    "pressure_mpa": 0.1,
    "gas_temperature_c": 800,
    "htc_w_m2k": 1000,
    "base_temperature_c": 600,
    "modal": True,
}


def cube_mesh(job_dir: Path, size: float = 10.0) -> None:
    """A cube of six tetrahedra written as C3D10 elements (the format `mesh` writes)."""
    corners = np.array([[x, y, z] for z in (0, 1) for y in (0, 1) for x in (0, 1)], float) * size
    tets = []
    for order in itertools.permutations(range(3)):
        node, path = 0, [0]
        for axis in order:
            node |= 1 << axis
            path.append(node)
        tets.append(path)
    fea_case.write_second_order(corners, np.array(tets), job_dir / "mesh.inp")
    (job_dir / "mesh.json").write_text(
        json.dumps(
            {
                "elements": 6,
                "nodes": 8,
                "volume_mm3": size**3,
                "bbox_min": [0, 0, 0],
                "bbox_max": [size] * 3,
                "notes": ["a note from meshing"],
                "stl_bbox_min": [0, 0, 0],
                "stl_bbox_max": [size] * 3,
                "surface_tolerance_mm": 0.05,
            }
        )
    )


def test_a_triangle_stored_twice_is_dropped():
    tri = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
    other = np.array([[0, 0, 1], [1, 0, 1], [0, 1, 1]], float)
    triangles = np.array([tri, other, tri[::-1]])

    cleaned, notes = fea_case.clean_triangles(triangles)

    assert len(cleaned) == 1 and np.allclose(cleaned[0], other)
    assert "stored twice" in notes[0]


def test_an_open_surface_is_reported():
    tri = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0]]], float)

    assert "3 open edges" in fea_case.surface_problems(tri)[0]


def test_the_second_order_mesh_has_midside_nodes_and_positive_volumes(tmp_path):
    cube_mesh(tmp_path)

    nodes, elements = fea_case.read_mesh(tmp_path / "mesh.inp")

    assert elements.shape == (6, 10) and len(nodes) == 8 + 19  # 8 corners + the shared edges
    a, b, c, d = (nodes[elements[:, i]] for i in range(4))
    assert (np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a) > 0).all()
    # A mid-edge node sits halfway along its edge (C3D10: node 5 between nodes 1 and 2).
    assert np.allclose(nodes[elements[:, 4]], (a + b) / 2)


def test_the_surface_faces_are_the_cubes_six_sides(tmp_path):
    cube_mesh(tmp_path)
    nodes, elements = fea_case.read_mesh(tmp_path / "mesh.inp")

    element, face, normal, area = fea_case.surface_faces(nodes, elements)

    assert area.sum() == pytest.approx(6 * 100)
    assert np.allclose(np.abs(normal).sum(axis=1), 1)  # every outward normal is along an axis
    assert normal.sum(axis=0) == pytest.approx(0, abs=1e-9)  # as many point out each way


def test_the_deck_has_the_loads_the_case_asks_for(tmp_path):
    cube_mesh(tmp_path)
    (tmp_path / "case.json").write_text(json.dumps(CASE))

    fea_case.do_deck(tmp_path)

    deck = (tmp_path / "model.inp").read_text()
    omega2 = (2 * math.pi * 30000 / 60) ** 2
    assert f"EALL, CENTRIF, {omega2:.6e}, 0.0000, 0.0000, -50.0000, 0, 1, 0" in deck
    assert "*UNCOUPLED TEMPERATURE-DISPLACEMENT, STEADY STATE" in deck
    assert (
        deck.count(", F") >= 12 and ", 800, 1.000000e+00" in deck
    )  # film: 1000 W/m2K -> 1 N/mm/s/K
    assert deck.count("1.000000e-01") == deck.count(", P") >= 12  # 0.1 MPa on every surface face
    assert "*BOUNDARY\nNSUPPORT, 11, 11, 600" in deck
    assert (tmp_path / "modal.inp").exists() and "*FREQUENCY" in (
        tmp_path / "modal.inp"
    ).read_text()
    assert json.loads((tmp_path / "deck.json").read_text())["support_nodes"] == 4 + 5


def test_without_a_gas_temperature_the_deck_is_a_plain_static_step(tmp_path):
    cube_mesh(tmp_path)
    case = {**CASE, "gas_temperature_c": None, "modal": False, "spin_rpm": 0, "pressure_mpa": 0}
    (tmp_path / "case.json").write_text(json.dumps(case))

    fea_case.do_deck(tmp_path)

    deck = (tmp_path / "model.inp").read_text()
    assert "*STATIC" in deck and "*UNCOUPLED" not in deck and "*FILM" not in deck
    assert "CENTRIF" not in deck and "*DLOAD" not in deck
    assert not (tmp_path / "modal.inp").exists()


def test_units_scale_the_spin_axis(tmp_path):
    cube_mesh(tmp_path)
    (tmp_path / "case.json").write_text(
        json.dumps({**CASE, "units": "m", "spin_axis_origin": [0, 0, -0.05]})
    )

    fea_case.do_deck(tmp_path)

    assert ", 0.0000, 0.0000, -50.0000," in (tmp_path / "model.inp").read_text()


DAT = """
 displacements (vx,vy,vz) for set NALL and time  0.1000000E+01

         1  0.000000E+00  0.000000E+00  0.000000E+00
         2  3.000000E-02  4.000000E-02  0.000000E+00

 stresses (elem, integ.pnt.,sxx,syy,szz,sxy,sxz,syz) for set EALL and time  0.1000000E+01

         1   1  1.000000E+02  0.000000E+00  0.000000E+00  0.000000E+00  0.000000E+00  0.000000E+00
         2   1  0.000000E+00  0.000000E+00  0.000000E+00  5.000000E+01  0.000000E+00  0.000000E+00
         3   1  2.000000E+02  0.000000E+00  0.000000E+00  0.000000E+00  0.000000E+00  0.000000E+00

 temperatures for set NALL and time  0.1000000E+01

         1  6.000000E+02
         2  8.000000E+02
"""

MODAL = """
     E I G E N V A L U E   O U T P U T

 MODE NO    EIGENVALUE                       FREQUENCY
                                     REAL PART            IMAGINARY PART
                           (RAD/TIME)      (CYCLES/TIME     (RAD/TIME)

      1   0.3947842E+08   0.6283185E+04   0.1000000E+04   0.0000000E+00
      2   0.1000000E+10   0.3162278E+05   0.5033000E+04   0.0000000E+00

 P A R T I C I P A T I O N   F A C T O R S
"""


def test_results_are_read_from_the_dat_files(tmp_path):
    cube_mesh(tmp_path)
    (tmp_path / "case.json").write_text(json.dumps(CASE))
    fea_case.do_deck(tmp_path)
    (tmp_path / "model.dat").write_text(DAT)
    (tmp_path / "modal.dat").write_text(MODAL)

    result = fea_case.build_results(tmp_path)

    assert result["max_von_mises_mpa"] == pytest.approx(200.0)  # uniaxial 200 MPa
    assert result["max_displacement_mm"] == pytest.approx(0.05)  # 3-4-5 triangle
    assert result["temperature_c"] == [600.0, 800.0]
    assert result["mass_kg"] == pytest.approx(1000 * 8190e-9)  # 1000 mm3 of Inconel 718
    assert result["natural_frequencies_hz"] == [1000.0, 5033.0]
    assert result["engine_order_hz"]["2E"] == 1000.0
    text = " ".join(result["warnings"])
    assert "possible resonance" in text and "1000 Hz is within 10% of 2E" in text
    assert "above the usual service limit" in text  # the metal reaches 800 C


def test_the_view_has_the_surface_with_the_solution_on_it(tmp_path):
    import base64

    cube_mesh(tmp_path)
    (tmp_path / "case.json").write_text(json.dumps(CASE))
    blocks = fea_case.read_blocks(_write(tmp_path / "model.dat", DAT))

    view = fea_case.build_view(tmp_path, blocks)

    def floats(text, width):
        return np.frombuffer(base64.b64decode(text), "<f4").reshape(-1, width)

    position = floats(view["position"], 3)
    index = np.frombuffer(base64.b64decode(view["index"]), "<u4").reshape(-1, 3)
    assert (view["vertices"], view["triangles"]) == (8, 12)  # the cube's corners, two per side
    assert position.max() == pytest.approx(10) and position.min() == 0
    # Every triangle is wound so its normal points away from the cube's centre.
    a, b, c = (position[index[:, i]] for i in range(3))
    outward = np.einsum("ij,ij->i", np.cross(b - a, c - a), (a + b + c) / 3 - 5)
    assert (outward > 0).all()
    assert set(view["fields"]) == {"von_mises", "displacement", "temperature"}
    assert view["fields"]["temperature"]["unit"] == "°C"
    assert view["fields"]["displacement"]["max"] == pytest.approx(0.05)  # node 2 of DAT
    assert view["fields"]["von_mises"]["max"] > 0
    assert floats(view["fields"]["von_mises"]["values"], 1).shape == (8, 1)
    assert floats(view["displacement_vector"], 3).shape == (8, 3)


def _write(path, text):
    path.write_text(text)
    return path


def test_a_stray_node_beyond_the_base_doesnt_move_the_support(tmp_path):
    """The mesher can push a few surface nodes past the solid's real extent; the clamp must still
    take the whole base (found when a job had too few nodes held and came out wildly wrong)."""
    cube_mesh(tmp_path)
    mesh = (tmp_path / "mesh.inp").read_text()
    # Move a corner node on the base 0.2 mm below it, beyond the 1.5 x 0.05 mm tolerance.
    mesh = mesh.replace("2, 10.000000, 0.000000, 0.000000", "2, 10.000000, 0.000000, -0.200000")
    (tmp_path / "mesh.inp").write_text(mesh)
    (tmp_path / "case.json").write_text(json.dumps(CASE))

    fea_case.do_deck(tmp_path)

    held = json.loads((tmp_path / "deck.json").read_text())
    assert held["support_plane"] == 0  # the solid's own base, not the stray node's
    # The rest of the base is still held (before, the stray node made it the plane: one node).
    assert 4 <= held["support_nodes"] < 9 and held["support_area_mm2"] >= 40


def test_a_support_with_no_flat_face_is_refused(tmp_path):
    cube_mesh(tmp_path)
    info = json.loads((tmp_path / "mesh.json").read_text())
    info["stl_bbox_min"] = [0, 0, -5]  # nothing of the solid lies on that plane
    (tmp_path / "mesh.json").write_text(json.dumps(info))
    (tmp_path / "case.json").write_text(json.dumps(CASE))

    with pytest.raises(SystemExit, match="no flat face"):
        fea_case.do_deck(tmp_path)


def test_an_unbelievable_displacement_is_flagged(tmp_path):
    cube_mesh(tmp_path)
    (tmp_path / "case.json").write_text(json.dumps(CASE))
    fea_case.do_deck(tmp_path)
    (tmp_path / "model.dat").write_text(
        DAT.replace("3.000000E-02  4.000000E-02", "3.000000E+00  4.000000E+00")
    )

    result = fea_case.build_results(tmp_path)

    assert any("not a believable small-deflection result" in w for w in result["warnings"])
