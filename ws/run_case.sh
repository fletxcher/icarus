#!/usr/bin/env bash
# Runs one CFD case on the simulation work station. Install at WS_CFD_SCRIPT.
#
# Usage: run_case.sh <job_dir>
#   <job_dir>/case.json    inputs written by the backend (see CfdCase)
#   <job_dir>/status.json  kept up to date here; the backend polls it
#
# Placeholder: replace the "solve" section with the real pipeline
# (mesh generation + OpenFOAM/SU2 run + force coefficient extraction).
set -euo pipefail

job_dir="$1"
cd "$job_dir"

write_status() {
  # Write then rename so the backend never reads a half-written file.
  printf '%s\n' "$1" > status.json.tmp && mv status.json.tmp status.json
}

trap 'write_status "{\"status\": \"failed\", \"error\": \"run_case.sh exited with code $?\"}"' ERR

write_status '{"status": "running"}'

# --- solve -----------------------------------------------------------------
sleep 5
cl=0.0
cd=0.0
# ---------------------------------------------------------------------------

write_status "{\"status\": \"completed\", \"result\": {\"cl\": $cl, \"cd\": $cd}}"
