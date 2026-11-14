"""Python the agent writes: sent to the compute host's sandbox (a fake), output brought back."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from api.agent.prompts import build_system_prompt
from api.agent.tools import python_tools
from api.compute.python_exec import PythonError, PythonResult, clip, format_result, safe_name
from api.compute.redact import hide_host_paths
from api.compute.runner import CommandResult
from api.compute.station import Workstation
from tests.conftest import sign_up_and_in
from tests.test_files import upload


class FakeHost:
    """A compute host whose sandbox answers with a canned result."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.files: dict[str, str] = {}
        self.removed: list[str] = []
        self.result = CommandResult(0, "42\n", "")
        self.delay = 0.0
        self.running = 0
        self.most_running = 0

    async def run(self, command, *, cwd=None, timeout_s=None):
        self.commands.append(command)
        if "run-sandboxed" not in command:
            return CommandResult(0, "", "")
        self.running += 1
        self.most_running = max(self.most_running, self.running)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.running -= 1
        return self.result

    async def write_file(self, path, content):
        self.files[path] = content

    async def remove_dir(self, path):
        self.removed.append(path)


@pytest.fixture
def host(client: TestClient) -> FakeHost:
    fake = FakeHost()
    client.app.state.services.compute.set_station(Workstation(fake, "/w", True))
    return fake


def service(client: TestClient):
    user_id = client.get("/api/auth/me").json()["id"]
    return client.app.state.services.compute.python(user_id)


def run(client: TestClient, code: str, file_ids: list[str] | None = None):
    return client.portal.call(service(client).run, code, file_ids)


def test_the_code_is_sent_to_the_sandbox_and_its_output_comes_back(client, host):
    result, files = run(client, "print(6 * 7)")

    assert result.exit_code == 0 and result.stdout == "42\n" and not result.timed_out
    assert files == []
    [(path, code)] = [(p, c) for p, c in host.files.items() if p.endswith("/main.py")]
    assert code == "print(6 * 7)" and path.startswith("/w/python/")
    sandbox = next(c for c in host.commands if "run-sandboxed" in c)
    assert sandbox.startswith("/w/bin/run-sandboxed /w/python/") and sandbox.endswith(" 60")
    # The scratch directory is removed afterwards.
    assert host.removed == [path.rsplit("/", 1)[0]]


def test_a_script_that_hits_the_time_limit_says_so(client, host):
    host.result = CommandResult(124, "partial\n", "")

    result, _ = run(client, "while True: pass")

    assert result.timed_out
    assert "stopped after" in format_result(result, [])
    assert "time limit" in format_result(result, [])


def test_a_failing_script_reports_its_error(client, host):
    host.result = CommandResult(1, "", "Traceback...\nZeroDivisionError: division by zero")

    result, _ = run(client, "1 / 0")
    text = format_result(result, [])

    assert "failed (exit status 1)" in text and "ZeroDivisionError" in text


def test_nothing_is_run_when_the_sandbox_cannot_start(client, host):
    host.result = CommandResult(97, "", "sandbox unavailable: bubblewrap (bwrap) isn't installed")

    with pytest.raises(PythonError, match="sandbox for running code isn't available"):
        run(client, "print(1)")

    assert host.removed  # and the scratch directory is cleaned up


def test_an_unreachable_host_is_explained_and_cleaned_up(client, host):
    async def broken(command, *, cwd=None, timeout_s=None):
        if "run-sandboxed" in command:
            raise OSError("connection reset")
        return CommandResult(0, "", "")

    host.run = broken  # type: ignore[method-assign]

    with pytest.raises(PythonError, match="couldn't reach the compute host"):
        run(client, "print(1)")

    assert host.removed


def test_empty_and_oversized_code_is_refused_without_touching_the_host(client, host):
    with pytest.raises(PythonError, match="no code"):
        run(client, "   ")
    with pytest.raises(PythonError, match="over"):
        run(client, "x = 1\n" * 10_000)

    assert host.commands == []


def test_long_output_keeps_the_start_and_the_end():
    text = "".join(f"line {i}\n" for i in range(5000))

    clipped = clip(text, 1000)

    assert len(clipped) < 1200
    assert clipped.startswith("line 0") and clipped.endswith("line 4999\n")
    assert "characters left out" in clipped
    assert clip("short", 1000) == "short"


def test_attached_text_files_can_be_read_by_name(client, host):
    sheet = upload(client, "my data (1).csv", b"x,y\n1,2\n3,4\n").json()

    result, files = run(client, "print(open('my_data_1_.csv').read())", [sheet["id"]])

    assert files == ["my_data_1_.csv"]
    assert any(
        p.endswith("/my_data_1_.csv") and c == "x,y\n1,2\n3,4\n" for p, c in host.files.items()
    )
    assert "Files it could read: my_data_1_.csv" in format_result(result, files)


def test_other_users_files_and_unknown_ids_are_refused(client, anon_client, host):
    mine = upload(client, "mine.txt", b"secret").json()
    sign_up_and_in(anon_client, email="other@example.com")
    other = anon_client.app.state.services.compute.python(
        anon_client.get("/api/auth/me").json()["id"]
    )

    with pytest.raises(PythonError, match="isn't one of the user's files"):
        anon_client.portal.call(other.run, "print(1)", [mine["id"]])
    with pytest.raises(PythonError, match="isn't one of the user's files"):
        run(client, "print(1)", ["no-such-id"])
    assert not any(p.endswith("mine.txt") for p in host.files)


def test_scripts_run_one_at_a_time(client, host):
    host.delay = 0.05

    python = service(client)

    async def two_at_once():
        await asyncio.gather(python.run("print(1)"), python.run("print(2)"))

    client.portal.call(two_at_once)

    assert host.most_running == 1


def test_the_tool_returns_the_output_or_the_reason_it_didnt_run(client, host):
    [tool] = python_tools(service(client))

    ran = client.portal.call(tool.ainvoke, {"code": "print(6 * 7)"})
    assert tool.name == "exec_python"
    assert "finished" in ran and "Output:" in ran and "42" in ran

    host.result = CommandResult(97, "", "no bwrap")
    refused = client.portal.call(tool.ainvoke, {"code": "print(1)"})
    assert refused.startswith("Not run:") and "sandbox" in refused


def test_the_prompt_explains_python_only_when_the_tool_exists(client, host):
    with_tool = build_system_prompt(python_tools(service(client)), {})

    assert "Running Python:" in with_tool and "create_plot" in with_tool
    assert "Running Python:" not in build_system_prompt([], {})


def test_file_names_are_made_safe():
    assert safe_name("../../etc/passwd") == "etc_passwd"
    assert safe_name("data set.csv") == "data_set.csv"
    assert safe_name("...") == "file"
    assert PythonResult(0, "", "", 1.0, False).exit_code == 0


def test_host_directories_are_replaced_by_stars():
    text = (
        "bash: line 1: /home/fletxcher/icarus/bin/run-sandboxed: No such file or directory\n"
        'File "/home/fletxcher/icarus/venv/lib/python3.12/site-packages/numpy/x.py", line 3\n'
        "also /home/someone_else/notes and /tmp/icarus-ab12cd/out"
    )

    hidden = hide_host_paths(text, "/home/fletxcher/icarus")

    assert "***/bin/run-sandboxed: No such file or directory" in hidden
    assert "***/venv/lib/python3.12/site-packages/numpy/x.py" in hidden
    assert "also ***/notes and ***/out" in hidden
    assert "fletxcher" not in hidden and "someone_else" not in hidden
    assert (
        hide_host_paths("nothing to hide: /work/main.py", None) == "nothing to hide: /work/main.py"
    )
    assert hide_host_paths("a /b c", "/") == "a /b c"  # a root workdir would mask everything


def test_the_agent_never_sees_the_hosts_paths_in_results_or_errors(client, host):
    from api.agent.tools import hiding_host_paths, python_tools

    [tool] = hiding_host_paths(python_tools(service(client)), "/srv/icarus")
    host.result = CommandResult(
        1,
        "",
        "File \"/srv/icarus/venv/lib/python3.12/x.py\", line 1\nNameError: name 'q' is not defined",
    )

    failed = client.portal.call(tool.ainvoke, {"code": "print(q)"})

    assert "***/venv/lib/python3.12/x.py" in failed and "NameError" in failed
    assert "/srv/icarus" not in failed

    async def broken(command, *, cwd=None, timeout_s=None):
        raise OSError("ssh to /home/fletxcher/icarus/x: connection reset")

    host.run = broken  # type: ignore[method-assign]
    refused = client.portal.call(tool.ainvoke, {"code": "print(1)"})
    assert "fletxcher" not in refused and "connection reset" in refused


def test_a_host_without_the_sandbox_script_says_to_run_setup_not_a_shell_error(client, host):
    host.result = CommandResult(
        127, "", "bash: line 1: /w/bin/run-sandboxed: No such file or directory"
    )

    with pytest.raises(PythonError, match=r"setup\.sh --install") as error:
        run(client, "print(1)")

    assert "/w/" not in str(error.value) and host.removed
