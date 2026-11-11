#!/usr/bin/env bash
# Sets up the compute host: the machine the Icarus server reaches over SSH to run XFOIL, to build
# parts with build123d, and to run the Python the agent writes (numpy, scipy, pandas,
# scikit-learn and Cantera) inside a sandbox. For a small Debian or Ubuntu machine (an e2-micro is enough).
#
#   ./setup.sh --install     installs what's missing and copies Icarus's scripts to $ICARUS_HOME
#   ./setup.sh --check       says what is and isn't in place, changing nothing
#   ./setup.sh --uninstall   removes $ICARUS_HOME (and the swap file --install made)
#
# Run it as the user the server logs in as (WS_USER), not as root; it uses sudo for apt.
# Safe to run again: it skips what is already there. ICARUS_HOME defaults to ~/icarus, which is
# what the server's WS_WORKDIR should point at.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ICARUS_HOME="${ICARUS_HOME:-$HOME/icarus}"
SWAP_FILE=/swapfile
SWAP_MARKER="$ICARUS_HOME/.made-swap"
# Machines with less memory than this and no swap get a swap file: pip and the CAD kernel
# need more than 1 GB at their peak.
SWAP_BELOW_MB=2048
SWAP_SIZE=2G
APT_PACKAGES=(xfoil xvfb python3-venv libgl1 libglu1-mesa bubblewrap)
# Installed with pip into $ICARUS_HOME/venv, as "pip name:import name".
PYTHON_PACKAGES=(build123d:build123d cantera:cantera numpy:numpy scipy:scipy pandas:pandas scikit-learn:sklearn)

log() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

sudo_cmd() {
  if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi
}

# What the install needs from the machine, checked before anything changes.
preflight() {
  [[ "$(uname -s)" == Linux ]] || die "this only sets up Linux machines"
  command -v apt-get > /dev/null || die "this needs apt (Debian or Ubuntu)"
  [[ $EUID -ne 0 ]] || die "run this as the user Icarus logs in as, not as root"
  command -v sudo > /dev/null || die "sudo isn't installed, and the install needs it for apt"
  sudo -n true 2> /dev/null || log "sudo will ask for your password once."
}

memory_mb() { awk '/^MemTotal:/ { printf "%d", $2 / 1024 }' /proc/meminfo; }
swap_mb() { awk '/^SwapTotal:/ { printf "%d", $2 / 1024 }' /proc/meminfo; }

ensure_swap() {
  if (($(memory_mb) >= SWAP_BELOW_MB)) || (($(swap_mb) > 0)); then
    return
  fi
  if [[ -e $SWAP_FILE ]]; then
    log "Not adding swap: $SWAP_FILE already exists"
    return
  fi
  log "Adding a $SWAP_SIZE swap file (this machine has $(memory_mb) MB of memory and no swap)"
  sudo_cmd fallocate -l "$SWAP_SIZE" "$SWAP_FILE"
  sudo_cmd chmod 600 "$SWAP_FILE"
  sudo_cmd mkswap "$SWAP_FILE" > /dev/null
  sudo_cmd swapon "$SWAP_FILE"
  grep -q "^$SWAP_FILE " /etc/fstab || echo "$SWAP_FILE none swap sw 0 0" | sudo_cmd tee -a /etc/fstab > /dev/null
  mkdir -p "$ICARUS_HOME"
  touch "$SWAP_MARKER"
}

missing_packages() {
  local package
  for package in "${APT_PACKAGES[@]}"; do
    dpkg -s "$package" > /dev/null 2>&1 || printf '%s\n' "$package"
  done
}

install_packages() {
  local missing
  missing="$(missing_packages)"
  if [[ -z $missing ]]; then
    log "Packages already installed: ${APT_PACKAGES[*]}"
    return
  fi
  # shellcheck disable=SC2086  # one word per package
  log "Installing: $(echo $missing)"
  sudo_cmd apt-get update -qq
  # shellcheck disable=SC2086
  sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $missing
}

install_python_environment() {
  if [[ ! -x "$ICARUS_HOME/venv/bin/python" ]]; then
    log "Creating the Python environment in $ICARUS_HOME/venv"
    python3 -m venv "$ICARUS_HOME/venv"
  fi
  local entry package module missing=()
  for entry in "${PYTHON_PACKAGES[@]}"; do
    package="${entry%%:*}"
    module="${entry##*:}"
    "$ICARUS_HOME/venv/bin/python" -c "import $module" 2> /dev/null || missing+=("$package")
  done
  if ((${#missing[@]} == 0)); then
    log "Python packages already installed: ${PYTHON_PACKAGES[*]%%:*}"
    return
  fi
  log "Installing ${missing[*]} (a few minutes on a small machine)"
  "$ICARUS_HOME/venv/bin/pip" install --quiet --no-cache-dir --upgrade pip
  "$ICARUS_HOME/venv/bin/pip" install --quiet --no-cache-dir "${missing[@]}"
}

install_scripts() {
  mkdir -p "$ICARUS_HOME/bin"
  local name
  for name in cad_build.py icarus-exec run-sandboxed; do
    [[ -f "$SCRIPT_DIR/$name" ]] || die "$name is missing next to setup.sh"
    install -m 755 "$SCRIPT_DIR/$name" "$ICARUS_HOME/bin/$name"
  done
  log "Copied the runner scripts to $ICARUS_HOME/bin"
}

# Runs a throwaway script in the sandbox and checks that it has no network.
sandbox_works() {
  [[ -x "$ICARUS_HOME/bin/run-sandboxed" ]] || return 1
  local job out
  job="$(mktemp -d)"
  cat > "$job/main.py" << 'PY'
import os, socket
try:
    socket.create_connection(("1.1.1.1", 53), timeout=2)
    print("network-open")
except OSError:
    print("no-network")
PY
  out="$("$ICARUS_HOME/bin/run-sandboxed" "$job" 20 2> /dev/null)" || out=""
  rm -rf "$job"
  [[ $out == *no-network* ]]
}

# Whether each piece works, not only whether it is installed.
check() {
  local failed=0
  report() {
    if [[ $1 == ok ]]; then log "  ok       $2"; else log "  MISSING  $2"; failed=1; fi
  }
  log "Checking this machine:"
  if command -v xfoil > /dev/null; then report ok "xfoil ($(command -v xfoil))"; else report no "xfoil"; fi
  if command -v xvfb-run > /dev/null; then report ok "xvfb-run"; else report no "xvfb-run (package xvfb)"; fi
  local entry package module
  for entry in "${PYTHON_PACKAGES[@]}"; do
    package="${entry%%:*}"
    module="${entry##*:}"
    if [[ -x "$ICARUS_HOME/venv/bin/python" ]] &&
      "$ICARUS_HOME/venv/bin/python" -c "import $module" 2> /dev/null; then
      report ok "$package in $ICARUS_HOME/venv"
    else
      report no "$package in $ICARUS_HOME/venv"
    fi
  done
  if [[ -x "$ICARUS_HOME/bin/cad_build.py" && -x "$ICARUS_HOME/bin/icarus-exec" &&
    -x "$ICARUS_HOME/bin/run-sandboxed" ]]; then
    report ok "runner scripts in $ICARUS_HOME/bin"
  else
    report no "runner scripts in $ICARUS_HOME/bin"
  fi
  if sandbox_works; then
    report ok "sandbox for the agent's Python (no network, no access to your files)"
  else
    report no "sandbox for the agent's Python (needs bubblewrap and unprivileged user namespaces)"
  fi
  log "  memory   $(memory_mb) MB, swap $(swap_mb) MB"
  return "$failed"
}

do_install() {
  preflight
  ensure_swap
  install_packages
  mkdir -p "$ICARUS_HOME"
  install_python_environment
  install_scripts
  log ""
  if check; then
    log ""
    log "Done. Add the server's public key to ~/.ssh/authorized_keys here, then give the server:"
    log "  WS_HOST=<this machine's address>   WS_SSH_KEY=<its private key>   WS_HOST_KEY=<this host's key>"
    log "(see the README). Set WS_USER=$USER if it isn't icarus, and WS_WORKDIR=$ICARUS_HOME if it"
    log "isn't ~/icarus. Turning off password logins in sshd is a good idea."
  else
    die "something above is still missing"
  fi
}

do_uninstall() {
  [[ -d $ICARUS_HOME ]] || log "$ICARUS_HOME doesn't exist"
  if [[ -f $SWAP_MARKER ]]; then
    log "Removing the swap file this script added"
    sudo_cmd swapoff "$SWAP_FILE" 2> /dev/null || true
    sudo_cmd sed -i "\\|^$SWAP_FILE |d" /etc/fstab
    sudo_cmd rm -f "$SWAP_FILE"
  fi
  rm -rf "$ICARUS_HOME"
  log "Removed $ICARUS_HOME. The apt packages (${APT_PACKAGES[*]}) are left in place;"
  log "remove them with apt if nothing else uses them."
}

case "${1:-}" in
  --install) do_install ;;
  --check) check ;;
  --uninstall) do_uninstall ;;
  *)
    sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
