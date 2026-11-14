"""Parts the agent designs with build123d: built on the compute host (a fake), kept as STL."""

import json
from functools import partial

import pytest
from fastapi.testclient import TestClient

from api.agent.tools import design_tools
from api.compute.runner import CommandResult
from api.compute.station import Workstation
from api.design.service import DesignError
from tests.conftest import sign_up_and_in
from tests.test_chats_api import create_thread
from tests.test_file_kinds import binary_stl, cube_triangles

REPORT = {
    "ok": True, "isolated": True, "units": "mm",
    "bounding_box_mm": [[0, 0, 0], [1, 1, 1]], "size_mm": [1.0, 1.0, 1.0],
    "volume_mm3": 1.0, "surface_area_mm2": 6.0, "center_of_mass_mm": [0.5, 0.5, 0.5],
    "solids": 1, "faces": 6, "valid": True, "notes": [],
}  # fmt: skip
CODE = "from build123d import *\nresult = Box(1, 1, 1)\n"


class FakeBuilder:
    """A compute host whose CAD builder answers with a canned report and files."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.files: dict[str, bytes | str] = {}
        self.removed: list[str] = []
        self.report: dict = REPORT
        # What the sandbox itself answered, when it (not the builder) is what failed.
        self.exit_code = 0
        self.stderr = ""
        self.outputs = {"model.stl": binary_stl(cube_triangles()), "model.step": b"ISO-10303-21;"}

    async def run(self, command, *, cwd=None, timeout_s=None):
        self.commands.append(command)
        output = json.dumps(self.report) if self.exit_code == 0 else ""
        return CommandResult(self.exit_code, output, self.stderr)

    async def write_file(self, path, content):
        self.files[path] = content

    async def read_bytes(self, path):
        name = path.rsplit("/", 1)[1]
        if name not in self.outputs:
            raise FileNotFoundError(path)
        return self.outputs[name]

    async def remove_dir(self, path):
        self.removed.append(path)


@pytest.fixture
def builder(client: TestClient) -> FakeBuilder:
    fake = FakeBuilder()
    client.app.state.services.compute.set_station(Workstation(fake, "/w", True))
    return fake


def designs_for_current_user(client: TestClient):
    user_id = client.get("/api/auth/me").json()["id"]
    return client.app.state.services.compute.designs(user_id)


def tools_for_current_user(client: TestClient):
    return {t.name: t for t in design_tools(designs_for_current_user(client))}


def test_a_built_part_is_kept_as_an_stl_with_its_code(client, builder):
    thread_id = create_thread(client)
    tools = tools_for_current_user(client)

    text = client.portal.call(
        tools["create_geometry"].ainvoke,
        {"name": "Mounting bracket", "code": CODE},
        {"configurable": {"thread_id": thread_id}},
    )

    assert "Built Mounting_bracket.stl. File id: " in text
    assert "don't ask which format" in text and "unless they name one" in text
    file_id = text.split("File id: ")[1].split("\n")[0]
    original = client.get(f"/api/files/{file_id}/original")
    assert original.status_code == 200 and original.headers["content-type"] == "model/stl"
    assert original.content == builder.outputs["model.stl"]
    saved = client.portal.call(
        client.app.state.services.files.design_code,
        file_id,
        client.get("/api/auth/me").json()["id"],
    )
    assert saved == CODE
    assert builder.files[next(p for p in builder.files if p.endswith("design.py"))] == CODE
    # The builder runs in the sandbox, which sees the scratch folder as /work.
    command = builder.commands[-1]
    assert command.startswith("/w/bin/run-sandboxed /w/designs/")
    assert command.endswith(" 150 /w/bin/cad_build.py build /work")
    assert builder.removed  # the scratch folder is cleaned up


def test_a_failed_build_says_why_and_leaves_nothing_behind(client, builder):
    builder.report = {"ok": False, "error": "line 3: ValueError: radius too large"}

    text = client.portal.call(
        tools_for_current_user(client)["create_geometry"].ainvoke,
        {"name": "x", "code": CODE},
    )

    assert text == "The part wasn't built: line 3: ValueError: radius too large"
    assert builder.removed


@pytest.mark.parametrize(
    ("exit_code", "stderr", "expected"),
    [
        (97, "sandbox unavailable: bubblewrap (bwrap) isn't installed", "isn't available"),
        (127, "bash: /w/bin/run-sandboxed: No such file or directory", "setup.sh --install"),
        (124, "", "took longer than 150 s"),
        (137, "", "took longer than 150 s"),
    ],
)
def test_a_sandbox_that_cannot_run_the_build_is_explained_and_cleaned_up(
    client, builder, exit_code, stderr, expected
):
    builder.exit_code, builder.stderr = exit_code, stderr

    text = client.portal.call(
        tools_for_current_user(client)["create_geometry"].ainvoke, {"name": "x", "code": CODE}
    )

    assert text.startswith("The part wasn't built:") and expected in text
    assert builder.removed  # the scratch folder is cleaned up


def test_export_builds_again_in_the_chosen_format_and_downloads(client, builder):
    text = client.portal.call(
        tools_for_current_user(client)["create_geometry"].ainvoke, {"name": "cube", "code": CODE}
    )
    file_id = text.split("File id: ")[1].split("\n")[0]

    said = client.portal.call(
        tools_for_current_user(client)["export_geometry"].ainvoke,
        {"geometry_file_id": file_id, "format": "step"},
    )
    download = client.get(f"/api/designs/{file_id}/export", params={"format": "step"})

    assert said.startswith("Exported cube.step") and "download it from the part's card" in said
    assert builder.commands[-1].startswith("/w/bin/run-sandboxed /w/designs/")
    assert builder.commands[-1].endswith(" 150 /w/bin/cad_build.py export /work step")
    assert download.status_code == 200 and download.content == b"ISO-10303-21;"
    assert 'filename="cube.step"' in download.headers["content-disposition"]
    saved = client.get(f"/api/files/{file_id}/original", params={"download": "true"})
    assert 'filename="cube.stl"' in saved.headers["content-disposition"]


def test_export_refuses_other_formats_and_other_peoples_parts(client, builder):
    text = client.portal.call(
        tools_for_current_user(client)["create_geometry"].ainvoke, {"name": "cube", "code": CODE}
    )
    file_id = text.split("File id: ")[1].split("\n")[0]

    assert client.get(f"/api/designs/{file_id}/export", params={"format": "dwg"}).status_code == 422
    assert client.get("/api/designs/formats").json() == ["step", "stl", "3mf", "brep", "obj", "glb"]
    client.post("/api/auth/logout")
    sign_up_and_in(client, email="other@example.com")
    assert (
        client.get(f"/api/designs/{file_id}/export", params={"format": "step"}).status_code == 404
    )


def test_the_code_has_a_size_limit(client, builder):
    with pytest.raises(DesignError, match="over 20000 characters"):
        client.portal.call(
            partial(designs_for_current_user(client).build, "x", "#" * 20_001, thread_id=None)
        )


def test_the_agent_is_reminded_of_the_parts_it_designed_each_turn(client, builder):
    from api.design.service import designs_note

    thread_id = create_thread(client)
    tools = tools_for_current_user(client)
    config = {"configurable": {"thread_id": thread_id}}
    first = client.portal.call(
        tools["create_geometry"].ainvoke, {"name": "gear", "code": CODE}, config
    )
    second = client.portal.call(
        tools["create_geometry"].ainvoke, {"name": "shaft", "code": CODE + "# v2\n"}, config
    )
    ids = [t.split("File id: ")[1].split("\n")[0] for t in (first, second)]
    user_id = client.get("/api/auth/me").json()["id"]

    parts = client.portal.call(
        client.app.state.services.files.designs_in_thread, thread_id, user_id
    )
    note = designs_note(parts)

    assert [p.id for p in parts] == ids
    assert f"gear.stl: geometry_file_id {ids[0]}\n" in note
    assert f"shaft.stl: geometry_file_id {ids[1]} (the latest)" in note
    assert "never ask the user for one" in note
    assert designs_note([]) is None
    code = client.portal.call(tools["get_geometry"].ainvoke, {"geometry_file_id": ids[1]})
    assert code.startswith("shaft.stl was built") and "# v2" in code
    assert client.portal.call(
        tools["get_geometry"].ainvoke, {"geometry_file_id": "nope"}
    ).startswith("No designed part")


def test_a_part_build_waits_for_its_turn_on_the_host_and_gives_up_when_it_is_busy(client, builder):
    from api.compute.queue import HostQueue
    from api.design.service import DesignService
    from api.files.repository import FileRepository

    queue = HostQueue(slots=1, wait_s=0.05)
    service = DesignService(
        builder,
        workdir="/w",
        files=FileRepository(client.app.state.db),
        user_id="u",
        queue=queue,
    )

    async def scenario():
        async with queue.turn():  # a script is running
            with pytest.raises(DesignError, match="busy with other jobs"):
                await service.build("part", CODE, thread_id=None)
        return await service.build("part", CODE, thread_id=None)

    built = client.portal.call(scenario)

    assert built.file.name == "part.stl"
    assert len([c for c in builder.commands if "run-sandboxed" in c]) == 1
