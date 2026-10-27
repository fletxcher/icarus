#!/usr/bin/env bash
# Runs one CalculiX (ccx) structural/thermal model on the simulation work station.
#
# Usage: run_calculix.sh <job_dir> [job_name]
#   <job_dir>/<job_name>.inp   the CalculiX input deck (job_name defaults to "model")
# Writes next to it: <job_name>.dat (printed results), <job_name>.frd (results for
# CalculiX GraphiX / ParaView) and log.ccx (the solver's output).
#
# Settings (environment): OMP_NUM_THREADS  solver threads (default: min(cores, 4))
set -euo pipefail

job_dir="$(cd "$1" && pwd)"
job_name="${2:-model}"
cd "$job_dir"

[[ -f "$job_name.inp" ]] || { echo "No $job_name.inp in $job_dir" >&2; exit 1; }
command -v ccx > /dev/null || { echo "CalculiX (ccx) isn't installed; run setup.sh --install" >&2; exit 1; }

threads="${OMP_NUM_THREADS:-$(nproc)}"
((threads > 4)) && threads=4
export OMP_NUM_THREADS="$threads"

ccx -i "$job_name" > log.ccx 2>&1 || { tail -n 20 log.ccx >&2; exit 1; }
[[ -f "$job_name.dat" ]] || { echo "ccx finished without writing $job_name.dat" >&2; tail -n 20 log.ccx >&2; exit 1; }
