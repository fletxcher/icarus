#!/usr/bin/env bash
# Runs one 3D CFD case around an uploaded solid. Installed next to run_cfd2d.sh.
#
# Usage: run_cfd3d.sh <job_dir>
#   <job_dir>/case.json      inputs written by the backend (see Cfd3dCase)
#   <job_dir>/geometry.stl   the uploaded solid
#   <job_dir>/status.json    kept up to date here; the backend polls it
#
# Pipeline: cfd3d_case.py mesh (cleans the surface, fills cavities, blockMesh + snappyHexMesh +
# checkMesh) -> decomposePar -> simpleFoam in parallel -> reconstructPar -> y+ and the surface
# fields -> foamToVTK -> cfd3d_case.py results. Each step logs to <job_dir>/log.<step>.
#
# Settings (environment): OPENFOAM_BASHRC  OpenFOAM's etc/bashrc (found automatically)
#                         ICARUS_NPROC     MPI ranks (default: min(cores, 4))
#                         ICARUS_VENV      Python environment with numpy, pyvista and scipy
#                                          (default: ../venv next to this script's bin/)
set -euo pipefail

job_dir="$(cd "$1" && pwd)"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv="${ICARUS_VENV:-$here/../venv}"
python="$venv/bin/python"
[[ -x "$python" ]] || python=python3
cd "$job_dir"

status() { python3 "$here/cfd3d_case.py" status "$job_dir" "$@"; }

stage="starting"
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
stage="environment"
set +eu
# shellcheck disable=SC1090
source "$bashrc" > "$job_dir/log.environment" 2>&1
set -eu
for tool in blockMesh snappyHexMesh simpleFoam foamToVTK; do
  command -v "$tool" > /dev/null || { echo "$tool not found after sourcing $bashrc" >> "$job_dir/log.environment"; false; }
done

procs="${ICARUS_NPROC:-$(nproc)}"
((procs > 4)) && procs=4
export ICARUS_NPROC="$procs"

# The mesh stage runs blockMesh, snappyHexMesh and checkMesh itself (logging each to log.<tool>).
run mesh "$python" "$here/cfd3d_case.py" mesh "$job_dir"

stage="solve"
: > "$job_dir/log.solve"
decomposePar -force > "$job_dir/log.decomposePar" 2>&1
mpirun --oversubscribe -np "$procs" simpleFoam -parallel > "$job_dir/log.solver" 2>&1
reconstructPar -latestTime > "$job_dir/log.reconstructPar" 2>&1

stage="results"
: > "$job_dir/log.results"
simpleFoam -postProcess -func yPlus -latestTime > "$job_dir/log.yPlus" 2>&1 || true
simpleFoam -postProcess -func wallShearStress -latestTime > "$job_dir/log.wallShearStress" 2>&1 || true
foamToVTK -latestTime -ascii -no-internal -patches '(body inlet outlet)' > "$job_dir/log.foamToVTK" 2>&1
"$python" "$here/cfd3d_case.py" results "$job_dir" > "$job_dir/log.results" 2>&1
