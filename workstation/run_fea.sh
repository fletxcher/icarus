#!/usr/bin/env bash
# Runs one structural/thermal CalculiX case on an uploaded solid. Installed next to run_openfoam.sh.
#
# Usage: run_fea.sh <job_dir>
#   <job_dir>/case.json      inputs written by the backend (see FeaCase)
#   <job_dir>/geometry.stl   the uploaded solid
#   <job_dir>/status.json    kept up to date here; the backend polls it
#
# Pipeline: repair + tetrahedral mesh (fea_case.py mesh) -> CalculiX deck -> ccx (run_calculix.sh)
# -> a frequency run if asked -> results parsed into status.json. Each step logs to
# <job_dir>/log.<step>.
#
# Settings (environment): ICARUS_VENV   Python environment with numpy, pyvista and pytetwild
#                                       (default: ../venv next to this script's bin/, made by setup.sh)
#                         ICARUS_FEA_MAX_ELEMENTS  largest mesh the solver gets (default 70000)
set -euo pipefail

job_dir="$(cd "$1" && pwd)"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv="${ICARUS_VENV:-$here/../venv}"
python="$venv/bin/python"
[[ -x "$python" ]] || python=python3
cd "$job_dir"

status() { python3 "$here/fea_case.py" status "$job_dir" "$@"; }

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

# The mesher needs the Python environment; every other step runs on the system Python.
run mesh "$python" "$here/fea_case.py" mesh "$job_dir"
run deck "$python" "$here/fea_case.py" deck "$job_dir"
run solve "$here/run_calculix.sh" "$job_dir" model
if [[ -f modal.inp ]]; then
  run modal "$here/run_calculix.sh" "$job_dir" modal
fi
stage="results"
"$python" "$here/fea_case.py" results "$job_dir" > "$job_dir/log.results" 2>&1
