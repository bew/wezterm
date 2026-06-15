#!/usr/bin/env bash

# This script runs linters for CI python scripts.
# It manages a python virtual env and installs missing dependencies as needed.
#
# NOTE: The script assumes it is started from project root.

set -euo pipefail # Safe, strict script execution

VENV_PATH=".venv"

function check_bin() {
  command -v "$1" >/dev/null
}

function check_and_load_venv() {
  [[ -d $VENV_PATH ]] || python -m venv -- $VENV_PATH
  source $VENV_PATH/bin/activate

  local missing_pkg=()
  check_bin ruff || missing_pkg+=("ruff")
  check_bin pyrefly || missing_pkg+=("pyrefly")

  if (( ${#missing_pkg[@]} != 0 )); then
    pip install "${missing_pkg[@]}"
  fi
}

function main() {
  cd ci/

  check_and_load_venv

  echo
  echo ":: Running ruff linter..."
  ruff check

  echo
  echo ":: Running pyrefly type checker..."
  pyrefly check
}

main
