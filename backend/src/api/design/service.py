"""Parts the agent designs with build123d code, built on the user's work station.

The code runs in `workstation/cad_build.py`, which checks and fences it in. A built part is kept
as an ordinary STL file (so the FEA and CFD tools, the 3D viewer and downloads all take it as they
take an upload) together with the code that made it, which exports it again in any format asked for.
"""

import json
import logging
import re
import shlex
from dataclasses import dataclass
from uuid import uuid4

from ..compute.runner import CommandRunner
from ..config import get_settings
from ..files.extract import extract
from ..files.repository import FileRepository
from ..files.schemas import StoredFile

logger = logging.getLogger(__name__)

BUILD_TIMEOUT_S = 150
MAX_CODE_CHARS = 20_000

# format -> (media type, file extension)
FORMATS: dict[str, tuple[str, str]] = {
    "step": ("model/step", "step"),
    "stl": ("model/stl", "stl"),
    "3mf": ("model/3mf", "3mf"),
    "brep": ("application/octet-stream", "brep"),
    "obj": ("model/obj", "obj"),
    "glb": ("model/gltf-binary", "glb"),
}


class DesignError(Exception):
    """The part couldn't be built or exported; the message says why, for the model to act on."""


@dataclass(frozen=True)
class Built:
    file: StoredFile
    report: dict


@dataclass(frozen=True)
class Exported:
    name: str
    media_type: str
    data: bytes


def stem(name: str) -> str:
    """A safe file name without its extension."""
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", name.rsplit(".", 1)[0] if "." in name else name)
    return base.strip("._-")[:60] or "part"


def designs_note(parts: list[StoredFile]) -> str | None:
    """What the agent is told each turn about the parts it designed in this chat. Earlier turns'
    tool calls aren't replayed to it, so without this it would lose their ids."""
    if not parts:
        return None
    lines = [
        f"- {part.name}: geometry_file_id {part.id}{' (the latest)' if part is parts[-1] else ''}"
        for part in parts
    ]
    return (
        "Parts you designed in this chat, with the geometry_file_id to give export_geometry, "
        "submit_fea_simulation or submit_cfd3d_simulation:\n"
        + "\n".join(lines)
        + "\nUse these ids yourself; never ask the user for one. To change a part, call "
        "get_geometry to read its code, then create_geometry with the edited code."
    )


class DesignService:
    def __init__(
        self, runner: CommandRunner, *, workdir: str, files: FileRepository, user_id: str
    ) -> None:
        self._runner = runner
        self._workdir = workdir
        self._files = files
        self._user_id = user_id

    async def _run(self, code: str, *args: str) -> tuple[dict, str]:
        """Runs the code on the work station; returns cad_build's report and the scratch folder.
        The caller removes the folder."""
        if len(code) > MAX_CODE_CHARS:
            raise DesignError(f"the code is over {MAX_CODE_CHARS} characters; simplify it")
        folder = f"{self._workdir}/designs/{uuid4()}"
        try:
            await self._runner.run(f"mkdir -p {shlex.quote(folder)}")
            await self._runner.write_file(f"{folder}/design.py", code)
            command = " ".join(
                shlex.quote(part)
                for part in (
                    f"{self._workdir}/venv/bin/python",
                    f"{self._workdir}/bin/cad_build.py",
                    args[0],
                    folder,
                    *args[1:],
                )
            )
            result = await self._runner.run(command, timeout_s=BUILD_TIMEOUT_S)
        except Exception as error:
            await self._discard(folder)
            raise DesignError(f"couldn't reach the work station: {error}") from error
        try:
            report = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            await self._discard(folder)
            detail = result.stderr.strip().splitlines()[-1:] or ["no output"]
            raise DesignError(f"the CAD builder failed: {detail[0]}") from None
        if not report.get("ok"):
            await self._discard(folder)
            raise DesignError(report.get("error", "the build failed"))
        return report, folder

    async def _discard(self, folder: str) -> None:
        try:
            await self._runner.remove_dir(folder)
        except Exception:
            logger.warning("couldn't remove %s", folder, exc_info=True)

    async def build(self, name: str, code: str, *, thread_id: str | None) -> Built:
        """Runs the code, and keeps the part it makes as an STL file the tools can use."""
        report, folder = await self._run(code, "build")
        try:
            data = await self._runner.read_bytes(f"{folder}/model.stl")
        finally:
            await self._discard(folder)
        limit = get_settings().file_max_binary_bytes
        if len(data) > limit:
            raise DesignError(
                f"the part's mesh is {len(data) // 1_000_000} MB, over the {limit // 1_000_000} MB "
                "limit; use fewer small curved features"
            )
        file_name = f"{stem(name)}.stl"
        summary = extract(file_name, data).text
        stored = await self._files.create(
            self._user_id,
            name=file_name,
            content_type="model/stl",
            text=summary,
            size=len(data),
            kind="stl",
            data=data,
            thread_id=thread_id,
            design_code=code,
        )
        return Built(stored, report)

    async def code(self, file_id: str) -> tuple[str, str] | None:
        """(the part's file name, its build123d code), or None if it isn't one of this user's."""
        code = await self._files.design_code(file_id, self._user_id)
        if code is None:
            return None
        stored = (await self._files.get_many([file_id], self._user_id))[0]
        return stored.name, code

    async def export(self, file_id: str, fmt: str) -> Exported:
        """Builds the part again from its code, in the format asked for."""
        fmt = fmt.lower()
        if fmt not in FORMATS:
            raise DesignError(f"unknown format {fmt!r}; use one of {', '.join(FORMATS)}")
        code = await self._files.design_code(file_id, self._user_id)
        if code is None:
            raise DesignError(f"no designed part has the id {file_id!r}")
        stored = (await self._files.get_many([file_id], self._user_id))[0]
        _, folder = await self._run(code, "export", fmt)
        media_type, extension = FORMATS[fmt]
        try:
            data = await self._runner.read_bytes(f"{folder}/model.{extension}")
        finally:
            await self._discard(folder)
        return Exported(f"{stem(stored.name)}.{extension}", media_type, data)
