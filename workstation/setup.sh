#!/usr/bin/env bash
# Prepares (or cleans up) the simulation work station the Icarus backend reaches over SSH.
#
# Run it on the work station as the user the backend connects as (WS_USER, default "ubuntu"):
#   ./setup.sh --install      install XFOIL, gmsh, OpenFOAM, CalculiX, ParaView and ffmpeg, and the job layout
#   ./setup.sh --uninstall    remove what --install added (asks before deleting job data)
#
# Layout under ICARUS_HOME (default ~/icarus), matching the backend's defaults:
#   bin/run_openfoam.sh  CFD entrypoint (WS_CFD_SCRIPT)
#   bin/run_calculix.sh  runs a CalculiX (ccx) input deck in a job directory
#   jobs/             one directory per CFD job (WS_WORKDIR/jobs)
#
# CFD runs OpenFOAM (ESI release, OPENFOAM_PACKAGE) with gmsh meshes. If the work station
# doesn't have the OpenFOAM apt repository yet, --install adds the official one.
# CalculiX (https://www.calculix.de) comes from the distribution's calculix-ccx package.
# Assumes a Debian/Ubuntu work station.
set -euo pipefail

ICARUS_HOME="${ICARUS_HOME:-$HOME/icarus}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Packages installed by --install, so --uninstall removes only those.
MANIFEST="$ICARUS_HOME/.setup-packages"
OPENFOAM_PACKAGE="${OPENFOAM_PACKAGE:-openfoam2512}"
PACKAGES=(xfoil xvfb xauth gmsh calculix-ccx paraview ffmpeg "$OPENFOAM_PACKAGE")

usage() {
  cat <<EOF
Usage: $(basename "$0") --install | --uninstall [--yes]

  --install     Install XFOIL, xvfb, xauth, gmsh, OpenFOAM ($OPENFOAM_PACKAGE), CalculiX,
                ParaView and ffmpeg, and create $ICARUS_HOME with the CFD runner.
  --uninstall   Remove packages this script installed and delete $ICARUS_HOME.
  --yes         Don't ask before deleting $ICARUS_HOME (job data included).

Set ICARUS_HOME to use a different directory (then match WS_WORKDIR/WS_CFD_SCRIPT).
EOF
}

log() { printf '==> %s\n' "$*"; }

# apt needs root; use sudo unless already root.
as_root() {
  if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi
}

is_installed() {
  dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"
}

check_xfoil() {
  local tmp
  tmp="$(mktemp -d)"
  printf 'NACA 0012\nPANE\nOPER\nVISC 1e6\nPACC\npolar.txt\n\nALFA 2\nPACC\n\nQUIT\n' > "$tmp/input.txt"
  if (cd "$tmp" && xvfb-run -a xfoil < input.txt > output.txt 2>&1) \
    && grep -q '^ *2\.000' "$tmp/polar.txt"; then
    log "XFOIL check passed (NACA 0012, alpha 2, Re 1e6)"
  else
    log "XFOIL check FAILED; output:"
    cat "$tmp/output.txt" >&2
    rm -rf "$tmp"
    return 1
  fi
  rm -rf "$tmp"
}

# Makes sure apt can see the OpenFOAM package, adding the official repository if not.
ensure_openfoam_repo() {
  if apt-cache policy "$OPENFOAM_PACKAGE" 2> /dev/null | grep -q 'Candidate: [^(]'; then
    return
  fi
  log "Adding the OpenFOAM apt repository (dl.openfoam.com)"
  as_root apt-get install -y -qq curl ca-certificates
  curl -fsSL https://dl.openfoam.com/add-debian-repo.sh | as_root bash
  as_root apt-get update -qq
}

# Solves a one-element steel block pulled with 100 N and checks the displacement of the loaded
# face against the expected stretch (100 MPa / 210 GPa * 1 mm, a little less because the
# fixed face holds back the sideways contraction).
check_calculix() {
  local tmp u1
  tmp="$(mktemp -d)"
  cat > "$tmp/model.inp" <<'INP'
*NODE, NSET=NALL
1,0,0,0
2,1,0,0
3,1,1,0
4,0,1,0
5,0,0,1
6,1,0,1
7,1,1,1
8,0,1,1
*ELEMENT, TYPE=C3D8, ELSET=EALL
1,1,2,3,4,5,6,7,8
*NSET, NSET=FIXED
1,4,5,8
*NSET, NSET=LOADED
2,3,6,7
*MATERIAL, NAME=STEEL
*ELASTIC
210000.,0.3
*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL
*BOUNDARY
FIXED,1,3
*STEP
*STATIC
*CLOAD
LOADED,1,25.
*NODE PRINT, NSET=LOADED
U
*END STEP
INP
  if "$SCRIPT_DIR/run_calculix.sh" "$tmp" > "$tmp/output.txt" 2>&1; then
    u1="$(awk 'NF == 4 && $1 == 2 { print $2; exit }' "$tmp/model.dat")"
    if awk -v u="${u1:-0}" 'BEGIN { exit !(u > 3e-4 && u < 5e-4) }'; then
      log "CalculiX check passed (block pulled with 100 N, displacement $u1 mm)"
      rm -rf "$tmp"
      return
    fi
    echo "unexpected displacement '${u1:-none}'" >> "$tmp/output.txt"
  fi
  log "CalculiX check FAILED; output:"
  cat "$tmp/output.txt" >&2
  rm -rf "$tmp"
  return 1
}

check_video() {
  for tool in pvbatch ffmpeg xvfb-run; do
    command -v "$tool" > /dev/null || { log "Video check FAILED: $tool not found"; return 1; }
  done
  log "Video tools found (pvbatch, ffmpeg, xvfb-run)"
}

check_openfoam() {
  local bashrc
  # ls exits 2 when one of the two globs matches nothing, which pipefail would turn into an abort.
  bashrc="$(ls /usr/lib/openfoam/openfoam*/etc/bashrc /opt/openfoam*/etc/bashrc 2> /dev/null | tail -n 1 || true)"
  if [[ -z "$bashrc" ]]; then
    log "OpenFOAM check FAILED: no etc/bashrc found"
    return 1
  fi
  # OpenFOAM's bashrc isn't safe under `set -eu`.
  set +eu
  # shellcheck disable=SC1090
  source "$bashrc"
  set -eu
  for tool in simpleFoam pimpleFoam gmshToFoam checkMesh decomposePar; do
    if ! command -v "$tool" > /dev/null; then
      log "OpenFOAM check FAILED: $tool not found (sourced $bashrc)"
      return 1
    fi
  done
  log "OpenFOAM check passed ($(basename "$(dirname "$(dirname "$bashrc")")"), $bashrc)"
}

do_install() {
  if ! command -v apt-get > /dev/null; then
    echo "This script supports Debian/Ubuntu (apt) only." >&2
    exit 1
  fi
  if [[ $EUID -eq 0 ]]; then
    log "Running as root: files go to $ICARUS_HOME; make sure WS_USER can read them."
  fi

  local missing=()
  for package in "${PACKAGES[@]}"; do
    is_installed "$package" || missing+=("$package")
  done

  mkdir -p "$ICARUS_HOME/bin" "$ICARUS_HOME/jobs"
  if ((${#missing[@]})); then
    ensure_openfoam_repo
    log "Installing ${missing[*]}"
    as_root apt-get update -qq
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
    printf '%s\n' "${missing[@]}" >> "$MANIFEST"
  else
    log "Packages already installed: ${PACKAGES[*]}"
  fi

  install -m 755 "$SCRIPT_DIR/run_openfoam.sh" "$ICARUS_HOME/bin/run_openfoam.sh"
  install -m 755 "$SCRIPT_DIR/cfd_case.py" "$ICARUS_HOME/bin/cfd_case.py"
  install -m 755 "$SCRIPT_DIR/run_calculix.sh" "$ICARUS_HOME/bin/run_calculix.sh"
  install -m 755 "$SCRIPT_DIR/render_video.py" "$ICARUS_HOME/bin/render_video.py"
  log "Installed CFD runner at $ICARUS_HOME/bin/run_openfoam.sh (and cfd_case.py) and run_calculix.sh"

  check_xfoil
  check_openfoam
  check_calculix
  check_video

  cat <<EOF

Done. Backend settings for this work station (in the repo-root .env):
  WS_HOST=<this work station's address>
  WS_USER=$(id -un)
  WS_WORKDIR=$ICARUS_HOME
  WS_CFD_SCRIPT=$ICARUS_HOME/bin/run_openfoam.sh
  XFOIL_RUNNER=ws      # to run XFOIL here
EOF
}

do_uninstall() {
  local remove=()
  if [[ -f "$MANIFEST" ]]; then
    mapfile -t remove < <(sort -u "$MANIFEST")
  fi

  if [[ -d "$ICARUS_HOME" ]]; then
    local jobs
    jobs="$(find "$ICARUS_HOME/jobs" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)"
    if [[ "${ASSUME_YES:-}" != 1 ]]; then
      read -r -p "Delete $ICARUS_HOME, including $jobs job folder(s)? [y/N] " answer
      [[ "$answer" =~ ^[Yy]$ ]] || { echo "Aborted; nothing changed."; exit 1; }
    fi
    rm -rf "$ICARUS_HOME"
    log "Deleted $ICARUS_HOME"
  else
    log "$ICARUS_HOME not found; skipping"
  fi

  if ((${#remove[@]})); then
    log "Removing packages installed by --install: ${remove[*]}"
    as_root env DEBIAN_FRONTEND=noninteractive apt-get remove -y -qq "${remove[@]}"
    as_root apt-get autoremove -y -qq
  else
    log "No packages to remove (none were installed by --install)"
  fi
  log "Uninstalled"
}

action=""
for arg in "$@"; do
  case "$arg" in
    --install | --uninstall) action="$arg" ;;
    --yes) ASSUME_YES=1 ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

case "$action" in
  --install) do_install ;;
  --uninstall) do_uninstall ;;
  *) usage >&2; exit 2 ;;
esac
