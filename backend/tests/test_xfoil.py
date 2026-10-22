import pytest
from pydantic import ValidationError

from api.compute.runner import CommandResult
from api.compute.xfoil import POLAR_FILE, PolarRequest, XfoilService, build_script, parse_polar

SAMPLE_POLAR = """\
       XFOIL         Version 6.99

 Calculated polar for: NACA 2412

 1 1 Reynolds number fixed          Mach number fixed

 xtrf =   1.000 (top)        1.000 (bottom)
 Mach =   0.000     Re =     1.000 e 6     Ncrit =   9.000

  alpha    CL        CD       CDp       CM     Top_Xtr  Bot_Xtr
 ------ -------- --------- --------- -------- -------- --------
  -2.000   0.0350   0.00561   0.00104  -0.0533   0.7406   0.4371
   0.000   0.2489   0.00578   0.00103  -0.0546   0.6382   0.6114
   4.000   0.6862   0.00696   0.00186  -0.0580   0.3912   0.9900
"""


class FakeRunner:
    """Records what XFOIL would be given and serves a canned polar."""

    def __init__(self, polar: str = SAMPLE_POLAR) -> None:
        self.polar = polar
        self.files: dict[str, str] = {}
        self.commands: list[tuple[str, str | None]] = []
        self.removed: list[str] = []

    async def run(self, command, *, cwd=None, timeout_s=None):
        self.commands.append((command, cwd))
        return CommandResult(0, "", "")

    async def write_file(self, path, content):
        self.files[path] = content

    async def read_file(self, path):
        assert path.endswith(POLAR_FILE)
        return self.polar

    async def make_temp_dir(self):
        return "/tmp/job"

    async def remove_dir(self, path):
        self.removed.append(path)


def make_service(runner: FakeRunner) -> XfoilService:
    return XfoilService(runner, xfoil_path="xfoil", timeout_s=5, max_concurrency=2)


def test_naca_designation_is_normalized():
    assert PolarRequest(naca=" naca 2412 ", reynolds=1e6).naca == "2412"
    with pytest.raises(ValidationError):
        PolarRequest(naca="24x2", reynolds=1e6)


def test_build_script_sets_flow_conditions():
    script = build_script(PolarRequest(naca="0012", reynolds=5e5, mach=0.1, ncrit=7))
    lines = script.splitlines()
    assert "NACA 0012" in lines
    assert "VISC 500000" in lines
    assert "MACH 0.1" in lines
    assert "N 7" in lines
    assert "ASEQ -4 12 1" in lines
    assert lines[-1] == "QUIT"


def test_parse_polar_reads_data_rows():
    points = parse_polar(SAMPLE_POLAR)
    assert [p.alpha for p in points] == [-2.0, 0.0, 4.0]
    assert points[2].cl == pytest.approx(0.6862)
    assert points[2].lift_to_drag == pytest.approx(0.6862 / 0.00696)


def test_parse_polar_without_data_is_empty():
    assert parse_polar("no polar here") == []


async def test_run_polar_writes_script_runs_xfoil_and_cleans_up():
    runner = FakeRunner()
    polar = await make_service(runner).run_polar(PolarRequest(naca="2412", reynolds=1e6))

    assert "NACA 2412" in runner.files["/tmp/job/input.txt"]
    assert runner.commands == [("xfoil < input.txt", "/tmp/job")]
    assert runner.removed == ["/tmp/job"]
    assert polar.best_lift_to_drag is not None
    assert polar.best_lift_to_drag.alpha == 4.0


async def test_search_naca4_ranks_candidates_and_skips_symmetric_duplicates():
    runner = FakeRunner()
    ranked = await make_service(runner).search_naca4(
        PolarRequest(naca="0012", reynolds=1e6),
        cambers=[0, 2],
        camber_positions=[2, 4],
        thicknesses=[12],
    )
    # m=0 makes the camber position irrelevant: 0012, 2212 and 2412.
    assert sorted(polar.request.naca for polar in ranked) == ["0012", "2212", "2412"]
