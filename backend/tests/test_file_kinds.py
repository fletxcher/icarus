"""Uploads other than plain text: meshes, CAD files and PDFs reach the model as summaries."""

import struct

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pymongo import MongoClient

from api.catalog import FILES
from tests.conftest import MONGODB_URI, sign_up_and_in, use_scripted_agent
from tests.test_chats_api import create_thread, read_events
from tests.test_files import upload

CUBE_POINTS = [(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)]
# Twelve triangles, outward facing, of the unit cube (vertex indexes into CUBE_POINTS).
CUBE_FACES = [
    (0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4),
    (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5),
]  # fmt: skip


def cube_triangles(skip: int | None = None):
    return [[CUBE_POINTS[i] for i in face] for n, face in enumerate(CUBE_FACES) if n != skip]


def binary_stl(triangles) -> bytes:
    out = b"cube".ljust(80, b" ") + struct.pack("<I", len(triangles))
    for tri in triangles:
        out += struct.pack("<12fH", 0, 0, 0, *(c for v in tri for c in v), 0)
    return out


def ascii_stl(triangles) -> bytes:
    lines = ["solid cube"]
    for tri in triangles:
        lines += ["facet normal 0 0 0", "outer loop"]
        lines += [f"vertex {x} {y} {z}" for x, y, z in tri]
        lines += ["endloop", "endfacet"]
    return ("\n".join([*lines, "endsolid cube"]) + "\n").encode()


def text_of(client: TestClient, attachment: dict) -> str:
    source = MongoClient(MONGODB_URI)
    try:
        return source[client.app.state.db.name][FILES].find_one({"_id": attachment["id"]})["text"]
    finally:
        source.close()


def stored(client: TestClient, attachment: dict) -> dict:
    source = MongoClient(MONGODB_URI)
    try:
        return source[client.app.state.db.name][FILES].find_one({"_id": attachment["id"]})
    finally:
        source.close()


def test_a_binary_stl_is_measured_not_pasted(client: TestClient):
    response = upload(client, "cube.stl", binary_stl(cube_triangles()), "model/stl")

    assert response.status_code == 201
    text = text_of(client, response.json())
    assert "STL file summary (binary)" in text
    assert "12 triangles, 8 distinct vertices" in text
    assert "extents 1 x 1 x 1" in text
    assert "enclosed volume 1" in text and "surface area 6" in text
    assert "watertight: yes" in text


def test_an_ascii_stl_with_a_missing_triangle_is_reported_open(client: TestClient):
    text = text_of(client, upload(client, "open.stl", ascii_stl(cube_triangles(skip=3))).json())

    assert "(ASCII)" in text
    assert "11 triangles" in text
    assert "watertight: no" in text and "open edges" in text
    assert "no enclosed volume" in text


def test_the_original_bytes_of_summarized_files_are_kept(client: TestClient):
    data = binary_stl(cube_triangles())
    doc = stored(client, upload(client, "cube.stl", data).json())
    assert bytes(doc["data"]) == data and doc["kind"] == "stl"

    plain = stored(client, upload(client, "naca.dat", b"1 2\n3 4\n").json())
    assert "data" not in plain and plain["kind"] == "text"


def test_something_that_isnt_an_stl_is_refused(client: TestClient):
    response = upload(client, "model.stl", b"this is just words")

    assert response.status_code == 415
    assert "isn't a valid STL" in response.json()["detail"]


def test_an_obj_is_summarized(client: TestClient):
    obj = ["# cube", "o Cube", "mtllib cube.mtl"]
    obj += [f"v {x} {y} {z}" for x, y, z in CUBE_POINTS]
    obj += ["f 1 2 4 3", "f 5 6 8 7", "f 1 2 6 5", "f 3 4 8 7", "f 1 3 7 5", "f 2 4 8 6"]

    text = text_of(client, upload(client, "cube.obj", "\n".join(obj).encode()).json())

    assert "OBJ file summary" in text
    assert "8 vertices, 6 faces (12 triangles once split)" in text
    assert "objects/groups (1): Cube" in text and "cube.mtl" in text
    assert "extents 1 x 1 x 1" in text
    assert upload(client, "empty.obj", b"nothing here").status_code == 415


STEP = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('test'),'2;1');
FILE_NAME('bracket.step','2026-01-01',('A. Person'),('Org'),'preproc','SolidWorks 2025','');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));
ENDSEC;
DATA;
#1=PRODUCT('Bracket','Bracket','',(#9));
#2=CARTESIAN_POINT('',(0.,0.,0.));
#3=CARTESIAN_POINT('',(40.,20.,5.));
#4=ADVANCED_FACE('',(#5),#6,.T.);
#7=ADVANCED_FACE('',(#5),#6,.T.);
#8=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.));
ENDSEC;
END-ISO-10303-21;
"""


def test_a_step_file_is_summarized(client: TestClient):
    text = text_of(client, upload(client, "bracket.step", STEP.encode()).json())

    assert "STEP file summary" in text
    assert "name: bracket.step" in text and "AUTOMOTIVE_DESIGN" in text
    assert "made with: preproc, SolidWorks 2025" in text
    assert "products (1): Bracket" in text
    assert "2 advanced_face" in text and "length unit: millimetres" in text
    assert "extents 40 x 20 x 5" in text
    assert upload(client, "fake.stp", b"not a step file").status_code == 415


def minimal_pdf(text: str) -> bytes:
    stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )


def test_a_pdfs_text_is_extracted(client: TestClient):
    response = upload(client, "datasheet.pdf", minimal_pdf("Inconel 718 datasheet"))

    assert response.status_code == 201
    text = text_of(client, response.json())
    assert "[PDF: 1 pages]" in text
    assert "--- page 1 ---" in text and "Inconel 718 datasheet" in text


def test_a_broken_pdf_is_refused_with_a_reason(client: TestClient):
    assert "isn't a PDF" in upload(client, "a.pdf", b"hello").json()["detail"]
    broken = upload(client, "b.pdf", b"%PDF-1.4\nnothing sensible follows")
    assert broken.status_code == 415 and "couldn't read" in broken.json()["detail"]


def test_a_csv_gets_a_one_line_summary_and_keeps_its_content(client: TestClient):
    text = text_of(
        client, upload(client, "polar.csv", b"alpha,cl,cd\n0,0.2,0.01\n2,0.4,0.012\n").json()
    )

    assert text.startswith("[CSV: 3 rows, 3 columns; first row: alpha, cl, cd]")
    assert "2,0.4,0.012" in text


def test_geometry_and_pdf_files_have_a_larger_limit_than_text(client: TestClient):
    big_obj = b"o big\n" + b"v 1 2 3\n" * 200_000 + b"f 1 1 1\n"  # about 1.6 MB
    assert len(big_obj) > 1_000_000
    assert upload(client, "big.obj", big_obj).status_code == 201
    assert upload(client, "big.csv", b"a,b\n" * 300_000).status_code == 413
    assert upload(client, "huge.obj", b"v 1 2 3\n" * 2_000_000).status_code == 413


def test_a_summary_reaches_the_model_in_place_of_the_geometry(client: TestClient):
    model = use_scripted_agent(client, [AIMessage("A unit cube.")])
    file = upload(client, "cube.stl", binary_stl(cube_triangles())).json()
    thread_id = create_thread(client)

    events = read_events(
        client, thread_id, {"content": "What is this?", "attachments": [file["id"]]}
    )

    assert events[-1][0] == "done"
    prompt = model.seen[0][-1].content
    # Its id is shown so a tool can take the file by id.
    assert f"<file name='cube.stl' id='{file['id']}' kind='stl'>" in prompt
    assert "STL file summary" in prompt and "watertight: yes" in prompt


def test_an_uploaded_mesh_can_be_fetched_back_by_its_owner_only(client: TestClient):
    data = binary_stl(cube_triangles())
    file = upload(client, "cube.stl", data).json()

    response = client.get(f"/api/files/{file['id']}/original")

    assert response.status_code == 200
    assert response.content == data and response.headers["content-type"] == "model/stl"
    text = upload(client, "notes.txt", b"hello").json()
    assert client.get(f"/api/files/{text['id']}/original").status_code == 404  # no original kept
    client.post("/api/auth/logout")
    assert client.get(f"/api/files/{file['id']}/original").status_code == 401
    sign_up_and_in(client, email="other@example.com")
    assert client.get(f"/api/files/{file['id']}/original").status_code == 404
