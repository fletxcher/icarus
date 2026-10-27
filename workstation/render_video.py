"""Renders a finished case with ParaView: field images, and a flow video if transient.

Run it with ParaView's headless Python, on a virtual display:

  xvfb-run -a pvbatch render_video.py <job_dir>

It reads the VTK files foamToVTK wrote under <job_dir>/VTK/<case>_<n>/internal.vtu and
case.json, and writes into <job_dir>:

  velocity.png   velocity magnitude at the end of the run
  pressure.png   pressure coefficient Cp = 2 p / U^2 at the end of the run
  flow.mp4       transient runs only: the z-vorticity at every saved time step (25 fps, H.264)
  video.json     what was rendered, for the results: {"media": [{file, title, kind, ...}]}

The body is the hole in the mesh, shown in the background colour. The view follows the
flow direction (alpha): 3 reference lengths upstream, 12 downstream. Colour ranges are
fixed so that images, frames and runs can be compared.
"""

import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

WIDTH, HEIGHT, FPS = 1280, 720, 25
VORTICITY_RANGE = 5.0  # in freestream speeds per reference length
SPEED_RANGE = 1.75  # velocity magnitude, in freestream speeds
PRESSURE_RANGE = 1.5  # pressure coefficient, either side of zero
UPSTREAM, DOWNSTREAM = 3.0, 12.0  # view extent, in reference lengths (or body lengths)
BACKGROUND = [0.18, 0.18, 0.18]


def snapshot_index(path: Path) -> int:
    return int(re.search(r"_(\d+)$", path.parent.name).group(1))


def snapshot_files(job_dir: Path) -> list[Path]:
    """foamToVTK's internal-mesh files in time order."""
    return sorted(job_dir.glob("VTK/*_*/internal.vtu"), key=snapshot_index)


def snapshot_times(job_dir: Path, files: list[Path]) -> list[float]:
    """Simulation time of each snapshot.

    foamToVTK lists them in VTK/<case>.vtm.series; failing that, the time directories
    OpenFOAM wrote are matched in order, and failing that the snapshots are just counted.
    """
    for series in job_dir.glob("VTK/*.vtm.series"):
        listed = {
            int(re.search(r"_(\d+)\.vtm$", entry["name"]).group(1)): float(entry["time"])
            for entry in json.loads(series.read_text())["files"]
        }
        if all(snapshot_index(f) in listed for f in files):
            return [listed[snapshot_index(f)] for f in files]
    times = sorted(
        float(path.name)
        for path in job_dir.iterdir()
        if path.is_dir() and re.fullmatch(r"[0-9]+(\.[0-9]+)?(e[-+]?[0-9]+)?", path.name)
    )
    return times if len(times) == len(files) else [float(i) for i in range(len(files))]


def body_length(case: dict) -> float:
    shape = case.get("shape", "airfoil")
    if shape == "wedge":
        return 0.5 / math.tan(math.radians(case["half_angle"]))
    if shape in ("ellipse", "rectangle"):
        return float(case["aspect_ratio"])
    return 1.0


def body_centre(case: dict) -> tuple[float, float]:
    shape = case.get("shape", "airfoil")
    if shape == "airfoil":
        return 0.5, 0.0
    if shape in ("wedge", "rectangle"):
        return body_length(case) / 2, 0.0
    return 0.0, 0.0


def is_transient(case: dict) -> bool:
    regime = case.get("regime") or "auto"
    return regime == "transient" or (regime == "auto" and case.get("shape", "airfoil") != "airfoil")


def main(job_dir: Path) -> None:
    from paraview import simple as pv  # only importable under pvbatch

    case = json.loads((job_dir / "case.json").read_text())
    transient = is_transient(case)
    all_files = snapshot_files(job_dir)
    if not all_files:
        raise SystemExit(f"no VTK snapshots under {job_dir / 'VTK'}")
    files = all_files if transient else all_files[-1:]
    times = snapshot_times(job_dir, files)

    alpha = math.radians(case.get("alpha") or 0.0)
    length = max(1.0, body_length(case))
    cx, cy = body_centre(case)
    # Centre of the view, along the flow direction through the body.
    reach = (DOWNSTREAM - UPSTREAM) / 2 * length
    focal = (cx + reach * math.cos(alpha), cy + reach * math.sin(alpha))
    half_height = (UPSTREAM + DOWNSTREAM) * length / 2 * HEIGHT / WIDTH

    reader = pv.XMLUnstructuredGridReader(FileName=[str(f) for f in files])
    field = pv.CellDatatoPointData(Input=reader)
    field = pv.Calculator(Input=field, Function="2*p", ResultArrayName="Cp")
    if transient:
        field = pv.Gradient(Input=field)
        field.ScalarArray = ["POINTS", "U"]
        field.ComputeVorticity = 1
        field.VorticityArrayName = "Vorticity"

    view = pv.CreateRenderView()
    view.ViewSize = [WIDTH, HEIGHT]
    view.Background = BACKGROUND
    view.OrientationAxesVisibility = 0
    view.CameraParallelProjection = 1

    def frame_camera() -> None:
        view.CameraPosition = [focal[0], focal[1], 10.0 * length]
        view.CameraFocalPoint = [focal[0], focal[1], 0.0]
        view.CameraViewUp = [0.0, 1.0, 0.0]
        view.CameraParallelScale = half_height

    display = pv.Show(field, view)
    pv.Hide(reader, view)
    label = pv.Text(Text="")
    label_display = pv.Show(label, view)
    label_display.WindowLocation = "Upper Left Corner"
    label_display.FontSize = 18
    # ParaView fits the camera to the data when it first draws, which would undo the framing,
    # so draw once and then frame the view.
    pv.Render(view)
    frame_camera()

    shown_bar = None

    def colour_by(array: str, component: str, low: float, high: float, preset: str, title: str) -> None:
        nonlocal shown_bar
        if shown_bar is not None:
            pv.GetScalarBar(shown_bar, view).Visibility = 0
        pv.ColorBy(display, ("POINTS", array, component) if component else ("POINTS", array))
        lut = pv.GetColorTransferFunction(array)
        lut.ApplyPreset(preset, True)
        lut.AutomaticRescaleRangeMode = "Never"
        lut.RescaleTransferFunction(low, high)
        bar = pv.GetScalarBar(lut, view)
        bar.Title, bar.ComponentTitle, bar.Visibility = title, "", 1
        shown_bar = lut

    steps = list(reader.TimestepValues) or [0.0]
    last = min(len(steps), len(files)) - 1

    def render_to(path: Path, step_index: int, caption: str) -> None:
        view.ViewTime = steps[step_index]
        label.Text = caption
        pv.Render(view)
        pv.SaveScreenshot(str(path), view, ImageResolution=[WIDTH, HEIGHT])

    caption = f"t = {times[last]:.2f}" if transient else "steady state"
    media: list[dict] = []

    colour_by("U", "Magnitude", 0.0, SPEED_RANGE, "Viridis (matplotlib)", "Velocity magnitude")
    render_to(job_dir / "velocity.png", last, caption)
    media.append({"file": "velocity.png", "title": "Velocity magnitude", "kind": "image"})

    colour_by("Cp", "", -PRESSURE_RANGE, PRESSURE_RANGE, "Cool to Warm", "Pressure coefficient")
    render_to(job_dir / "pressure.png", last, caption)
    media.append({"file": "pressure.png", "title": "Pressure coefficient", "kind": "image"})

    if transient and last > 0:
        colour_by("Vorticity", "Z", -VORTICITY_RANGE, VORTICITY_RANGE, "Cool to Warm", "Vorticity (z)")
        frames = job_dir / "frames"
        shutil.rmtree(frames, ignore_errors=True)
        frames.mkdir()
        for i in range(last + 1):
            render_to(frames / f"frame_{i:05d}.png", i, f"t = {times[i]:.2f}")
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
                "-i", str(frames / "frame_%05d.png"),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart",
                str(job_dir / "flow.mp4"),
            ],
            check=True,
        )  # fmt: skip
        shutil.rmtree(frames, ignore_errors=True)
        media.append(
            {
                "file": "flow.mp4",
                "title": f"Vorticity (z), t = {times[0]:.1f} to {times[last]:.1f}",
                "kind": "video",
                "frames": last + 1,
                "fps": FPS,
                "duration_s": round((last + 1) / FPS, 1),
            }
        )
    (job_dir / "video.json").write_text(json.dumps({"media": media}) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
