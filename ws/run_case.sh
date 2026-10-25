#!/usr/bin/env bash
# Runs one CFD case on the simulation work station. Install at WS_CFD_SCRIPT.
#
# Usage: run_case.sh <job_dir>
#   <job_dir>/case.json    inputs written by the backend (see CfdCase)
#   <job_dir>/status.json  kept up to date here; the backend polls it
#
# Pipeline: gmsh mesh -> gmshToFoam -> checkMesh -> simpleFoam / pimpleFoam (parallel)
# -> results parsed from the force coefficients. cfd_case.py (next to this script) builds
# the mesh script and the OpenFOAM case; each step logs to <job_dir>/log.<step>.
#
# Settings (environment): OPENFOAM_BASHRC  OpenFOAM's etc/bashrc (found automatically)
#                         ICARUS_NPROC     MPI ranks (default: min(cores, 4))
set -euo pipefail

job_dir="$(cd "$1" && pwd)"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case_tool=("$here/cfd_case.py")
cd "$job_dir"

status() { python3 "${case_tool[0]}" status "$job_dir" "$@"; }

stage="starting"
# Whatever ends the script early (a failed step, `set -e`, a signal) marks the job failed.
on_exit() {
  local code=$?
  ((code == 0)) && return
  local log="$job_dir/log.${stage}"
  local detail=""
  [[ -f "$log" ]] && detail=": $(tail -n 6 "$log" | tr '\n' ' ' | cut -c1-600)"
  status failed "failed during $stage (exit $code)$detail" || true
}
trap on_exit EXIT

run() { # run <step> <command...>: log the step's output to log.<step>
  stage="$1"; shift
  "$@" > "$job_dir/log.$stage" 2>&1
}

status running

# --- OpenFOAM environment ----------------------------------------------------
bashrc="${OPENFOAM_BASHRC:-}"
if [[ -z "$bashrc" ]]; then
  for candidate in /usr/lib/openfoam/openfoam*/etc/bashrc /opt/openfoam*/etc/bashrc; do
    [[ -f "$candidate" ]] && bashrc="$candidate"
  done
fi
if [[ -z "$bashrc" ]]; then
  stage="environment"
  echo "OpenFOAM isn't installed (no etc/bashrc found); run setup.sh --install" > "$job_dir/log.environment"
  false
fi
# OpenFOAM's bashrc is not written for `set -eu`.
stage="environment"
set +eu
# shellcheck disable=SC1090
source "$bashrc" > "$job_dir/log.environment" 2>&1
set -eu
command -v simpleFoam > /dev/null || { echo "simpleFoam not found after sourcing $bashrc" >> "$job_dir/log.environment"; false; }

procs="${ICARUS_NPROC:-$(nproc)}"
((procs > 4)) && procs=4

transient="$(python3 -c "
import sys; sys.path.insert(0, '$here')
import cfd_case, pathlib
print(int(cfd_case.is_transient(cfd_case.read_case(pathlib.Path('$job_dir')))))")"

# --- mesh --------------------------------------------------------------------
run geo python3 "${case_tool[0]}" geo "$job_dir"
run gmsh gmsh -3 mesh/body.geo -format msh2 -o mesh/body.msh -v 3
# gmshToFoam needs the case's system/controlDict, so write the OpenFOAM case first.
run foam python3 "${case_tool[0]}" foam "$job_dir" "$procs"
run gmshToFoam gmshToFoam mesh/body.msh -case "$job_dir"
run boundary python3 "${case_tool[0]}" boundary "$job_dir"
run checkMesh checkMesh -case "$job_dir"
grep -q "Mesh OK" log.checkMesh || echo "checkMesh reported problems; continuing" >> log.checkMesh

# --- solve -------------------------------------------------------------------
solver=simpleFoam
((transient)) && solver=pimpleFoam
if ((procs > 1)); then
  run decomposePar decomposePar -case "$job_dir"
  run solver mpirun --oversubscribe -np "$procs" "$solver" -case "$job_dir" -parallel
else
  run solver "$solver" -case "$job_dir"
fi

# --- video -------------------------------------------------------------------
# A flow video (transient) or still (steady) from the saved time steps. The numbers don't
# depend on it, so a failure here is recorded as a warning in the results, not a failed job.
make_video() {
  stage="render"
  command -v pvbatch > /dev/null && command -v ffmpeg > /dev/null && command -v xvfb-run > /dev/null ||
    { echo "ParaView (pvbatch), ffmpeg or xvfb-run isn't installed" > "$job_dir/log.render"; return 1; }
  # Transient runs: every saved time step. Steady runs: just the last.
  local reconstruct=(-newTimes) vtk=()
  ((transient)) || { reconstruct=(-latestTime); vtk=(-latestTime); }
  run reconstruct reconstructPar -case "$job_dir" "${reconstruct[@]}" &&
    run vtk foamToVTK -case "$job_dir" -fields '(U p)' -no-boundary "${vtk[@]}" &&
    run render xvfb-run -a pvbatch "$here/render_video.py" "$job_dir"
}
rm -f video.json video.failed
if [[ "$(python3 -c "import json; print(json.load(open('case.json')).get('video', True))")" == "True" ]]; then
  if ! make_video; then
    echo "$(tail -n 3 "$job_dir/log.$stage" 2> /dev/null | tr '\n' ' ' | cut -c1-300)" > video.failed
  fi
  rm -rf VTK frames
fi

# --- results -----------------------------------------------------------------
stage="results"
run results python3 "${case_tool[0]}" results "$job_dir"
