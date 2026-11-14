from fastapi.testclient import TestClient


def test_status_reports_each_dependency(client: TestClient):
    checks = {check["id"]: check for check in client.get("/api/status").json()}

    assert set(checks) == {"database", "models", "xfoil", "work_station"}
    assert checks["database"]["ok"] is True
    assert checks["models"]["ok"] is True
    assert checks["work_station"]["ok"] is False  # none configured in tests
    assert "WS_HOST" in checks["work_station"]["detail"]


class FakeStation:
    """A compute host that is reachable; `scripts_installed` says whether setup.sh was run."""

    def __init__(self, *, scripts_installed: bool) -> None:
        self.answer = "0" if scripts_installed else "1"
        self.commands: list[str] = []

    async def run(self, command, *, cwd=None, timeout_s=None):
        from api.compute.runner import CommandResult

        self.commands.append(command)
        return CommandResult(0, f"{self.answer}\n", "")


def work_station_check(client: TestClient, station: FakeStation) -> dict:
    from api.compute.station import Workstation

    client.app.state.services.compute.set_station(Workstation(station, "/srv/icarus", True))
    checks = {check["id"]: check for check in client.get("/api/status").json()}
    return checks["work_station"]


def test_a_reachable_host_without_the_installed_scripts_is_reported_as_not_set_up(client):
    station = FakeStation(scripts_installed=False)

    check = work_station_check(client, station)

    assert check["ok"] is False and "setup.sh --install" in check["detail"]
    assert "/srv/icarus" not in check["detail"]
    for script in ("run-sandboxed", "cad_build.py", "icarus-exec"):
        assert f"/srv/icarus/bin/{script}" in station.commands[0]


def test_a_host_with_its_scripts_installed_is_ok(client):
    check = work_station_check(client, FakeStation(scripts_installed=True))

    assert check["ok"] is True and check["detail"] == "Reachable over SSH"
