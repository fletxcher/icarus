"""Runs Python the agent wrote on the compute host, inside the sandbox (`run-sandboxed`).

The sandbox is what keeps the code away from the machine: it has no network and can't see
anything but a scratch directory (see workstation/run-sandboxed). This module only sends the
code and files there, runs the script, and brings back a bounded amount of output.
"""

import asyncio
import logging
import re
import shlex
import time
from dataclasses import dataclass
from uuid import uuid4

from ..files.repository import FileRepository
from .runner import CommandRunner

logger = logging.getLogger(__name__)

MAX_CODE_CHARS = 30_000
MAX_FILES = 5
# The script gets the exit statuses of `timeout`: 124 when the limit was hit, 137 when it had to
# be killed. 97 is the sandbox refusing to start (nothing was run).
EXIT_TIMED_OUT = (124, 137)
EXIT_NO_SANDBOX = 97


class PythonError(Exception):
    """The code couldn't be run at all; the message says why, for the model to act on."""


@dataclass(frozen=True)
class PythonResult:
    exit_code: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool


def clip(text: str, limit: int) -> str:
    """Keeps the start and the end of long output, which is where results and errors are."""
    if len(text) <= limit:
        return text
    head, tail = limit * 2 // 3, limit // 3
    return f"{text[:head]}\n[... {len(text) - head - tail} characters left out ...]\n{text[-tail:]}"


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "file"


class PythonService:
    def __init__(
        self,
        runner: CommandRunner,
        *,
        workdir: str,
        files: FileRepository,
        user_id: str,
        timeout_s: float,
        output_max_chars: int,
        slots: asyncio.Semaphore,
    ) -> None:
        self._runner = runner
        self._workdir = workdir
        self._files = files
        self._user_id = user_id
        self._timeout_s = timeout_s
        self._output_max = output_max_chars
        # Shared by every user: the compute host is small, so scripts run one at a time.
        self._slots = slots

    async def _stage_files(self, folder: str, file_ids: list[str]) -> list[str]:
        """Puts the user's own text and CSV files in the folder; returns the names they have."""
        if len(file_ids) > MAX_FILES:
            raise PythonError(f"at most {MAX_FILES} files can be given to a script")
        found = await self._files.get_many(file_ids, self._user_id)
        if len(found) != len(set(file_ids)):
            raise PythonError("one of the file ids isn't one of the user's files")
        texts = await self._files.texts([file.id for file in found])
        names: list[str] = []
        for file in found:
            if file.kind not in ("text", "csv"):
                raise PythonError(
                    f"{file.name} is a {file.kind} file; only text and CSV files can be read"
                )
            name = safe_name(file.name)
            text = texts[file.id].text
            if file.kind == "csv" and text.startswith("[CSV: "):
                text = text.split("\n", 1)[1] if "\n" in text else ""  # the summary line added
            await self._runner.write_file(f"{folder}/{name}", text)
            names.append(name)
        return names

    async def run(
        self, code: str, file_ids: list[str] | None = None
    ) -> tuple[PythonResult, list[str]]:
        """Runs the code, waiting its turn; returns the result and the names of the files it was
        given. Raises PythonError if nothing could be run."""
        if not code.strip():
            raise PythonError("there is no code to run")
        if len(code) > MAX_CODE_CHARS:
            raise PythonError(f"the code is over {MAX_CODE_CHARS} characters; split it up")
        folder = f"{self._workdir}/python/{uuid4()}"
        async with self._slots:
            try:
                await self._runner.run(f"mkdir -p {shlex.quote(folder)}")
                names = await self._stage_files(folder, file_ids or [])
                await self._runner.write_file(f"{folder}/main.py", code)
                command = " ".join(
                    shlex.quote(part)
                    for part in (
                        f"{self._workdir}/bin/run-sandboxed",
                        folder,
                        str(int(self._timeout_s)),
                    )
                )
                started = time.perf_counter()
                done = await self._runner.run(command, timeout_s=self._timeout_s + 20)
                seconds = time.perf_counter() - started
            except PythonError:
                await self._discard(folder)
                raise
            except Exception as error:
                await self._discard(folder)
                raise PythonError(f"couldn't reach the compute host: {error}") from error
            await self._discard(folder)
        if done.exit_code == EXIT_NO_SANDBOX:
            logger.error("python sandbox unavailable: %s", done.stderr.strip())
            raise PythonError(
                "the sandbox for running code isn't available on the compute host, "
                "so nothing was run"
            )
        result = PythonResult(
            exit_code=done.exit_code,
            stdout=clip(done.stdout, self._output_max),
            stderr=clip(done.stderr, self._output_max // 2),
            seconds=seconds,
            timed_out=done.exit_code in EXIT_TIMED_OUT,
        )
        return result, names

    async def _discard(self, folder: str) -> None:
        try:
            await self._runner.remove_dir(folder)
        except Exception:
            logger.warning("couldn't remove %s", folder, exc_info=True)


def format_result(result: PythonResult, files: list[str]) -> str:
    """What the model is told about a run."""
    if result.timed_out:
        status = f"The script was stopped after {result.seconds:.0f} s, the time limit."
    elif result.exit_code == 0:
        status = f"The script finished in {result.seconds:.1f} s."
    else:
        status = f"The script failed (exit status {result.exit_code}) after {result.seconds:.1f} s."
    lines = [status]
    if files:
        lines.append(f"Files it could read: {', '.join(files)}")
    lines += ["", "Output:", result.stdout.rstrip() or "(nothing printed)"]
    if result.stderr.strip():
        lines += ["", "Errors:", result.stderr.rstrip()]
    return "\n".join(lines)
