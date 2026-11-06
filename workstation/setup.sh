#!/usr/bin/env bash
# Prepares (or cleans up) the simulation work station the Icarus backend reaches over SSH.
#
# Run it on the work station as the user the backend connects as:
#   ./setup.sh --install          Docker mode: install Docker if it's missing (needs root), build the
#                                 solver image and put the runner files in place
#   ./setup.sh --install-native   install XFOIL, gmsh, OpenFOAM, CalculiX, ParaView and ffmpeg directly
#                                 with apt instead of using Docker
#   ./setup.sh --uninstall        remove what an install added (asks before deleting job data)
# The app's setup button runs the two halves of --install itself:
#   --install-system  --home H --user U    the part that needs root: Docker and the docker group
#   --install-user    --home H             everything else, as the normal user
#
# Layout under ICARUS_HOME (default ~/icarus), matching the backend's defaults:
#   bin/icarus-exec   runs a command in the solver image (Docker mode) or directly (native)
#   bin/run_cfd2d.sh  2D CFD entrypoint (WS_CFD_SCRIPT), OpenFOAM
#   bin/run_cfd3d.sh  3D CFD entrypoint, OpenFOAM
#   bin/run_fea.sh    FEA entrypoint, which calls run_calculix.sh (the bare ccx wrapper)
#   Each entrypoint has a <job type>_case.py next to it (cfd2d_case.py, cfd3d_case.py, fea_case.py).
#   jobs/             one directory per CFD job (WS_WORKDIR/jobs)
#   runtime.env       Docker mode only: which image icarus-exec runs commands in
#   setup.status      progress of a running install, as JSON (the app polls it)
#
# Docker mode puts the solvers in one image built from the Dockerfile next to this script
# (OpenFOAM from the ESI release OPENFOAM_PACKAGE, CalculiX, gmsh, ParaView, ffmpeg, XFOIL and the
# Python environment for meshing and CAD), so the machine only needs Docker. The image is tagged
# with a hash of the Dockerfile, so a changed Dockerfile builds a new one.
#
# Native mode installs the same tools with apt (Debian/Ubuntu): it adds the official OpenFOAM
# apt repository if it's missing, and puts the Python packages in a virtual environment,
# $ICARUS_HOME/venv, so the system Python stays untouched.
#
# Both actions check before they act: an install skips what is already there and copies a runner
# file only when it's missing or differs; --uninstall removes only what an install added, and
# only what is still present, and leaves anything else in ICARUS_HOME alone.
set -euo pipefail

ICARUS_HOME="${ICARUS_HOME:-$HOME/icarus}"
# --home PATH (used when run through sudo, where $HOME isn't the user's) overrides it.
for ((i = 1; i < $#; i++)); do
  [[ "${!i}" == --home ]] && { j=$((i + 1)); ICARUS_HOME="${!j}"; }
done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Packages installed by --install, so --uninstall removes only those.
MANIFEST="$ICARUS_HOME/.setup-packages"
OPENFOAM_PACKAGE="${OPENFOAM_PACKAGE:-openfoam2512}"
PACKAGES=(xfoil xvfb xauth gmsh calculix-ccx paraview ffmpeg python3-venv "$OPENFOAM_PACKAGE")
VENV="$ICARUS_HOME/venv"
PYTHON_PACKAGES=(numpy scipy pyvista pytetwild build123d)
# The command each package provides: a tool already on the PATH needs no package.
declare -A PROVIDES=([xfoil]=xfoil [xvfb]=xvfb-run [xauth]=xauth [gmsh]=gmsh [calculix-ccx]=ccx [paraview]=pvbatch [ffmpeg]=ffmpeg)
# python3-venv has no command; it's there once a virtual environment can be made.
venv_possible() { python3 -c "import ensurepip, venv" 2> /dev/null; }
# Files this script puts in $ICARUS_HOME/bin (and the name an earlier version used).
RUNNER_FILES=(icarus-exec run_cfd2d.sh cfd2d_case.py run_calculix.sh render_video.py run_fea.sh fea_case.py run_cfd3d.sh cfd3d_case.py cad_build.py)
STALE_FILES=(run_case.sh run_openfoam.sh cfd_case.py)
STATUS_FILE="$ICARUS_HOME/setup.status"
RUNTIME_FILE="$ICARUS_HOME/runtime.env"
# The image is named after the Dockerfile's content, so the backend can tell whether it's current.
IMAGE_REPO=icarus-solvers

usage() {
  cat <<EOF
Usage: $(basename "$0") --install | --install-native | --uninstall [--yes] [--purge]

  --install         Docker mode: install Docker if it's missing (asks for root), build the solver
                    image (OpenFOAM $OPENFOAM_PACKAGE, CalculiX, gmsh, ParaView, ffmpeg, XFOIL) and put the
                    runner files in $ICARUS_HOME (only those that are missing or changed).
  --install-native  Install what's missing of XFOIL, xvfb, xauth, gmsh, OpenFOAM ($OPENFOAM_PACKAGE, unless
                    another release is there), CalculiX, ParaView and ffmpeg with apt instead.
  --uninstall       Remove the image, packages and runner files an install added, if still there.
                    Asks before deleting job folders, and leaves other files in $ICARUS_HOME.
  --yes             Don't ask before deleting job folders.
  --purge           With --uninstall: also remove the setup copy, designs and the Python environment.

Set ICARUS_HOME (or pass --home PATH) to use a different directory (then match WS_WORKDIR/WS_CFD_SCRIPT).
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

# Any OpenFOAM release found on disk counts: installing a second one next to it wouldn't help.
openfoam_bashrc() {
  # ls exits 2 when one of the two globs matches nothing, which pipefail would turn into an abort.
  ls /usr/lib/openfoam/openfoam*/etc/bashrc /opt/openfoam*/etc/bashrc 2> /dev/null | tail -n 1 || true
}

# Whether a package's job is already done: it's installed, or its command is on the PATH.
already_provided() {
  local package="$1"
  if [[ "$package" == "python3-venv" ]]; then
    is_installed "$package" || venv_possible
    return
  fi
  if [[ "$package" == "$OPENFOAM_PACKAGE" ]]; then
    is_installed "$package" || [[ -n "$(openfoam_bashrc)" ]]
    return
  fi
  is_installed "$package" || { [[ -n "${PROVIDES[$package]:-}" ]] && command -v "${PROVIDES[$package]}" > /dev/null; }
}

# Copies a file into place only if it's missing or different, and says which.
install_file() {
  local source="$1" target="$2"
  if [[ -f "$target" ]] && cmp -s "$source" "$target"; then
    log "$(basename "$target") is up to date"
  elif [[ -f "$target" ]]; then
    install -m 755 "$source" "$target"
    log "$(basename "$target") updated"
  else
    install -m 755 "$source" "$target"
    log "$(basename "$target") installed"
  fi
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
  if bash "$SCRIPT_DIR/run_calculix.sh" "$tmp" > "$tmp/output.txt" 2>&1; then
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
  bashrc="$(openfoam_bashrc)"
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

# The Python packages the FEA mesher needs, in a virtual environment of their own. Only what's
# missing is installed, so running this again changes nothing.
ensure_python_environment() {
  if [[ ! -x "$VENV/bin/python" ]]; then
    log "Creating the Python environment $VENV"
    python3 -m venv "$VENV"
    echo "$VENV" >> "$MANIFEST.venv"
  fi
  local missing=() package
  for package in "${PYTHON_PACKAGES[@]}"; do
    "$VENV/bin/python" -c "import ${package}" 2> /dev/null || missing+=("$package")
  done
  if ((${#missing[@]})); then
    log "Installing into the Python environment: ${missing[*]}"
    "$VENV/bin/pip" install -q "${missing[@]}"
  else
    log "Python environment already has: ${PYTHON_PACKAGES[*]}"
  fi
}

check_fea() {
  # In the solver image the environment is at ICARUS_VENV; on a native install it's next to bin/.
  local venv="${ICARUS_VENV:-$VENV}"
  if "$venv/bin/python" -c "import numpy, scipy, pyvista, pytetwild, build123d" 2> /dev/null; then
    log "FEA mesher check passed (numpy, scipy, pyvista, pytetwild, build123d in $venv)"
  else
    log "FEA mesher check FAILED: the Python environment $venv is missing numpy, scipy, pyvista, pytetwild or build123d"
    return 1
  fi
}

do_install_native() {
  if ! command -v apt-get > /dev/null; then
    echo "This script supports Debian/Ubuntu (apt) only." >&2
    exit 1
  fi
  if [[ $EUID -eq 0 ]]; then
    log "Running as root: files go to $ICARUS_HOME; make sure WS_USER can read them."
  fi

  local missing=() present=()
  for package in "${PACKAGES[@]}"; do
    if already_provided "$package"; then present+=("$package"); else missing+=("$package"); fi
  done
  ((${#present[@]})) && log "Already there, not installing: ${present[*]}"

  mkdir -p "$ICARUS_HOME/bin" "$ICARUS_HOME/jobs"
  if ((${#missing[@]})); then
    [[ " ${missing[*]} " == *" $OPENFOAM_PACKAGE "* ]] && ensure_openfoam_repo
    log "Installing ${missing[*]}"
    as_root apt-get update -qq
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
    printf '%s\n' "${missing[@]}" >> "$MANIFEST"
  else
    log "Nothing to install"
  fi

  for file in "${RUNNER_FILES[@]}"; do
    install_file "$SCRIPT_DIR/$file" "$ICARUS_HOME/bin/$file"
  done
  ensure_python_environment
  for file in "${STALE_FILES[@]}"; do
    if [[ -f "$ICARUS_HOME/bin/$file" ]]; then
      rm -f "$ICARUS_HOME/bin/$file"
      log "Removed $file, which an earlier version installed"
    fi
  done

  check_tools

  cat <<EOF

Done. Backend settings for this work station (in the repo-root .env):
  WS_HOST=<this work station's address>
  WS_USER=$(id -un)
  WS_WORKDIR=$ICARUS_HOME
  WS_CFD_SCRIPT=$ICARUS_HOME/bin/run_cfd2d.sh
  XFOIL_RUNNER=ws      # to run XFOIL here
EOF
}

# Progress for the app: {"state": running|completed|failed, "stage": ..., "message": ...}.
# Written through a temporary file so a reader never sees half of it, and so the root half of an
# install and the user half can each replace what the other wrote.
set_status() {
  [[ -d "$ICARUS_HOME" ]] || return 0
  local tmp message
  message="$(printf %s "$3" | tr -d '"\\\n' | cut -c1-300)"
  tmp="$(mktemp "$ICARUS_HOME/.setup.status.XXXXXX")" || return 0
  printf '{"state": "%s", "stage": "%s", "message": "%s", "updated": %s}\n' \
    "$1" "$2" "$message" "$(date +%s)" > "$tmp"
  chmod 644 "$tmp"
  mv -f "$tmp" "$STATUS_FILE"
}

# Marks the install failed when the script ends early, whatever the reason.
SETUP_STAGE=""
mark_failed_on_exit() {
  local code=$?
  ((code == 0)) && return
  # The checks run in a container with their own trap, which knows which tool failed; the
  # install that started them must not replace that with a vaguer "failed during checks".
  grep -q '"state": "failed"' "$STATUS_FILE" 2> /dev/null && return
  # The reason is in the log: the last "==> ... FAILED" line, else its last line.
  local reason=""
  if [[ -f "$ICARUS_HOME/setup.log" ]]; then
    reason="$(grep -E '^==> .*FAILED' "$ICARUS_HOME/setup.log" | tail -n 1)"
    [[ -n "$reason" ]] || reason="$(grep -v '^[[:space:]]*$' "$ICARUS_HOME/setup.log" | tail -n 1)"
  fi
  set_status failed "$SETUP_STAGE" "failed during $SETUP_STAGE (exit $code): ${reason#==> }"
}

docker_usable() { command -v docker > /dev/null && docker info > /dev/null 2>&1; }

image_name() { echo "$IMAGE_REPO:$(sha256sum "$SCRIPT_DIR/Dockerfile" | cut -c1-12)"; }

# The checks that run on the tools themselves, so in Docker mode inside the container. Each one
# is its own stage in setup.status, so the app can show which tool is being checked.
check_tools() {
  trap mark_failed_on_exit EXIT
  local step
  for step in "xfoil:XFOIL" "openfoam:OpenFOAM" "calculix:CalculiX" "fea:the Python tools for FEA and design" "video:the flow video tools"; do
    SETUP_STAGE="${step%%:*}"
    set_status running "$SETUP_STAGE" "Checking ${step#*:}"
    case "$SETUP_STAGE" in
      xfoil) check_xfoil ;;
      openfoam) check_openfoam ;;
      calculix) check_calculix ;;
      fea) check_fea ;;
      video) check_video ;;
    esac
  done
}

# The part of a Docker install that needs root: Docker itself, and the docker group for the user.
do_install_system() {
  trap mark_failed_on_exit EXIT
  SETUP_STAGE=docker
  [[ $EUID -eq 0 ]] || { echo "--install-system must run as root" >&2; exit 1; }
  local user="${TARGET_USER:-}"
  [[ -n "$user" ]] || { echo "--install-system needs --user NAME" >&2; exit 2; }
  id "$user" > /dev/null
  if ! command -v docker > /dev/null; then
    command -v apt-get > /dev/null || {
      echo "Installing Docker automatically supports Debian/Ubuntu (apt) only; install Docker yourself." >&2
      exit 1
    }
    set_status running docker "Installing Docker"
    log "Installing docker.io"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
    [[ -d "$ICARUS_HOME" ]] && echo docker.io >> "$MANIFEST"
  else
    log "Docker is already installed"
  fi
  if command -v systemctl > /dev/null; then systemctl enable --now docker > /dev/null 2>&1 || true; fi
  if id -nG "$user" | tr ' ' '\n' | grep -qx docker; then
    log "$user is already in the docker group"
  else
    log "Adding $user to the docker group"
    usermod -aG docker "$user"
  fi
  set_status running docker "Docker is installed"
}

# Everything that needs no root: the runner files, the solver image, the runtime marker, the checks.
do_install_user() {
  trap mark_failed_on_exit EXIT
  SETUP_STAGE=docker
  set_status running docker "Checking Docker"
  command -v docker > /dev/null || { echo "Docker isn't installed; run --install-system as root first." >&2; exit 1; }
  docker_usable || { echo "This user can't use Docker; it must be in the docker group (log in again after --install-system)." >&2; exit 1; }
  [[ $EUID -ne 0 ]] || log "Running as root: files go to $ICARUS_HOME; make sure WS_USER can read them."

  SETUP_STAGE=scripts
  set_status running scripts "Putting the runner files in place"
  mkdir -p "$ICARUS_HOME/bin" "$ICARUS_HOME/jobs"
  for file in "${RUNNER_FILES[@]}"; do
    install_file "$SCRIPT_DIR/$file" "$ICARUS_HOME/bin/$file"
  done
  for file in "${STALE_FILES[@]}"; do
    if [[ -f "$ICARUS_HOME/bin/$file" ]]; then
      rm -f "$ICARUS_HOME/bin/$file"
      log "Removed $file, which an earlier version installed"
    fi
  done

  SETUP_STAGE=image
  local image
  image="$(image_name)"
  if docker image inspect "$image" > /dev/null 2>&1; then
    log "Solver image $image is already built"
  else
    set_status running image "Building the solver image (OpenFOAM, CalculiX, gmsh, ParaView, ffmpeg, XFOIL); this takes a while"
    log "Building $image (this downloads about 1.5 GB)"
    docker build --build-arg "OPENFOAM_PACKAGE=$OPENFOAM_PACKAGE" -t "$image" - < "$SCRIPT_DIR/Dockerfile"
  fi
  printf 'ICARUS_RUNTIME=docker\nICARUS_IMAGE=%s\n' "$image" > "$RUNTIME_FILE"
  # Images of earlier versions of the Dockerfile are no longer needed.
  docker image ls --format '{{.Repository}}:{{.Tag}}' "$IMAGE_REPO" | grep -vxF "$image" | xargs -r docker rmi > /dev/null 2>&1 || true

  SETUP_STAGE=checks
  set_status running checks "Checking the solvers"
  # Run from the setup folder, which the wrapper mounts, so the script and its helpers are there.
  (cd "$SCRIPT_DIR" && "$ICARUS_HOME/bin/icarus-exec" bash ./setup.sh --check-tools --home "$ICARUS_HOME")

  set_status completed done "Ready"
  log "Docker work station ready (image $image)"
  cat <<EOF

Done. Backend settings for this work station (in the repo-root .env):
  WS_HOST=<this work station's address>
  WS_USER=$(id -un)
  WS_WORKDIR=$ICARUS_HOME
  WS_CFD_SCRIPT=$ICARUS_HOME/bin/run_cfd2d.sh
  XFOIL_RUNNER=ws      # to run XFOIL here
EOF
}

# --install by hand: root for Docker if needed, then everything else as this user.
do_install() {
  if ! docker_usable; then
    log "Docker isn't usable by $(id -un); installing it (needs root)"
    mkdir -p "$ICARUS_HOME"
    as_root bash "$SCRIPT_DIR/setup.sh" --install-system --home "$ICARUS_HOME" --user "$(id -un)"
    # The new docker group only applies to new logins; `sg` starts this step with it.
    if ! docker_usable; then
      exec sg docker -c "bash '$SCRIPT_DIR/setup.sh' --install-user --home '$ICARUS_HOME'"
    fi
  fi
  do_install_user
}

# Stops an install that is running and removes the solver image it built (Docker itself stays).
# Run by the app's Cancel button; it waits for the install to end so that nothing overwrites the
# "cancelled" status written last.
do_cancel() {
  local pattern='[s]etup\.sh --install-(system|user)' pid group mine
  mine="$(ps -o pgid= -p $$ | tr -d ' ')"
  log "Stopping setup"
  for signal in TERM KILL; do
    for pid in $(pgrep -f "$pattern" || true); do
      # The install was started with setsid, so its docker build and apt share its process group.
      group="$(ps -o pgid= -p "$pid" 2> /dev/null | tr -d ' ')"
      if [[ -n "$group" && "$group" != "$mine" ]]; then
        kill -"$signal" -- "-$group" 2> /dev/null || true
      fi
    done
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      pgrep -f "$pattern" > /dev/null || break
      sleep 0.5
    done
    pgrep -f "$pattern" > /dev/null || break
  done
  if command -v docker > /dev/null && docker info > /dev/null 2>&1; then
    local images image
    images="$(docker image ls --format '{{.Repository}}:{{.Tag}}' "$IMAGE_REPO" 2> /dev/null)"
    for image in $images; do
      docker ps -q --filter "ancestor=$image" | xargs -r docker kill > /dev/null 2>&1 || true
    done
    # shellcheck disable=SC2086
    [[ -z "$images" ]] || docker rmi -f $images > /dev/null 2>&1 || true
    [[ -z "$images" ]] || log "Removed the solver image(s): $images"
  fi
  rm -f "$RUNTIME_FILE"
  if pgrep -f "$pattern" > /dev/null; then
    set_status failed cancel "Setup couldn't be stopped: part of it runs as root. Try again once it finishes."
  else
    set_status cancelled cancel "Setup was cancelled and the solver image removed."
  fi
}

do_uninstall() {
  # Files this script installed, only where they are still there.
  local removed=0
  for file in "${RUNNER_FILES[@]}" "${STALE_FILES[@]}"; do
    if [[ -f "$ICARUS_HOME/bin/$file" ]]; then
      rm -f "$ICARUS_HOME/bin/$file"
      removed=$((removed + 1))
    fi
  done
  ((removed)) && log "Removed $removed runner file(s) from $ICARUS_HOME/bin"
  rm -rf "$ICARUS_HOME/bin/__pycache__"
  [[ -d "$ICARUS_HOME/bin" ]] && rmdir "$ICARUS_HOME/bin" 2> /dev/null && log "Removed the empty $ICARUS_HOME/bin"

  # The Docker solver image(s) and the files that went with them.
  if command -v docker > /dev/null && docker info > /dev/null 2>&1; then
    local images image
    images="$(docker image ls --format '{{.Repository}}:{{.Tag}}' "$IMAGE_REPO" 2> /dev/null)"
    for image in $images; do
      docker ps -q --filter "ancestor=$image" | xargs -r docker kill > /dev/null 2>&1 || true
    done
    if [[ -n "$images" ]]; then
      # shellcheck disable=SC2086
      docker rmi $images > /dev/null 2>&1 && log "Removed the solver image(s): $images"
    fi
  fi
  rm -rf "$RUNTIME_FILE" "$ICARUS_HOME/.container-home" "$STATUS_FILE" "$ICARUS_HOME/setup.log"

  # The Python environment, if this script made it.
  if [[ -f "$MANIFEST.venv" ]]; then
    if [[ -d "$VENV" ]]; then
      rm -rf "$VENV"
      log "Removed the Python environment $VENV"
    fi
    rm -f "$MANIFEST.venv"
  fi

  # Job data is the user's: only deleted when they say so.
  if [[ -d "$ICARUS_HOME/jobs" ]]; then
    local jobs
    jobs="$(find "$ICARUS_HOME/jobs" -mindepth 1 -maxdepth 1 -type d 2> /dev/null | wc -l)"
    if ((jobs == 0)); then
      rmdir "$ICARUS_HOME/jobs" 2> /dev/null || true
    else
      local answer=n
      if [[ "${ASSUME_YES:-}" == 1 ]]; then
        answer=y
      else
        read -r -p "Delete $jobs job folder(s) in $ICARUS_HOME/jobs? [y/N] " answer || true
      fi
      if [[ "$answer" =~ ^[Yy]$ ]]; then
        rm -rf "$ICARUS_HOME/jobs"
        log "Deleted $ICARUS_HOME/jobs"
      else
        log "Kept $ICARUS_HOME/jobs ($jobs job folder(s))"
      fi
    fi
  fi

  # Packages this script installed (and that are still installed).
  local remove=() package
  if [[ -f "$MANIFEST" ]]; then
    while read -r package; do
      if [[ -n "$package" ]] && is_installed "$package"; then remove+=("$package"); fi
    done < <(sort -u "$MANIFEST")
    rm -f "$MANIFEST"
  fi
  if ((${#remove[@]})); then
    if [[ $EUID -eq 0 ]] || sudo -n true 2> /dev/null; then
      log "Removing packages installed by --install: ${remove[*]}"
      as_root env DEBIAN_FRONTEND=noninteractive apt-get remove -y -qq "${remove[@]}"
      as_root apt-get autoremove -y -qq
    else
      # Removing them takes root, and nobody is here to type a password: leave them.
      log "Left these packages installed, since removing them takes root: ${remove[*]}"
    fi
  else
    log "No packages to remove (none were installed by --install, or they are already gone)"
  fi

  # --purge: everything else Icarus put in its directory (the setup copy, designs, notes about
  # what was installed). Named one by one: the directory is the person's to choose.
  if [[ "${PURGE:-}" == 1 && -d "$ICARUS_HOME" ]]; then
    rm -rf "$ICARUS_HOME/setup" "$ICARUS_HOME/designs" "$ICARUS_HOME/venv" "$ICARUS_HOME/.container-home"
    rm -f "$MANIFEST" "$MANIFEST.venv"
    log "Purged Icarus's files from $ICARUS_HOME"
  fi

  # The directory goes only if nothing else is in it.
  if [[ -d "$ICARUS_HOME" ]]; then
    if rmdir "$ICARUS_HOME" 2> /dev/null; then
      log "Removed the empty $ICARUS_HOME"
    else
      log "Left $ICARUS_HOME in place: it holds other files"
    fi
  fi
  log "Uninstalled"
}

# Only run when executed, so the functions can be sourced (for tests).
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then return 0; fi

action=""
TARGET_USER=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[i]}" in
    --install | --install-native | --install-system | --install-user | --check-tools | --cancel | --uninstall)
      action="${args[i]}" ;;
    --yes) ASSUME_YES=1 ;;
    --purge) PURGE=1 ;;
    --home) i=$((i + 1)) ;; # already read, before the variables that depend on it were set
    --user) i=$((i + 1)); TARGET_USER="${args[i]:-}" ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

case "$action" in
  --install) do_install ;;
  --install-native) do_install_native ;;
  --install-system) do_install_system ;;
  --install-user) do_install_user ;;
  --check-tools) check_tools ;;
  --cancel) do_cancel ;;
  --uninstall) do_uninstall ;;
  *) usage >&2; exit 2 ;;
esac
