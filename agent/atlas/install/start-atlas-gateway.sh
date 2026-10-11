#!/usr/bin/env bash
# Start the Atlas Hermes gateway only if the startup policy check passes.
# Usage: HERMES_PYTHON=~/hermes-venv/bin/python bash start-atlas-gateway.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
hermes_python="${HERMES_PYTHON:?set HERMES_PYTHON to the Python of the Hermes install}"
hermes_bin="${HERMES_EXECUTABLE:-$(dirname "$hermes_python")/hermes}"
hermes_root="${HERMES_ROOT:-$HOME/.hermes}"
profile_home="$hermes_root/profiles/atlas"

if ! "$hermes_python" "$here/check_policy.py" --hermes-home "$profile_home"; then
  echo "Refusing to start Atlas: fix the policy violations above first." >&2
  exit 1
fi

exec "$hermes_bin" -p atlas gateway run
