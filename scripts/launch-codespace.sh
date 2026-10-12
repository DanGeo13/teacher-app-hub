#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
run_dir="${TMPDIR:-/tmp}/hermes-hub-codespace"
mkdir -p "$run_dir"
chmod 700 "$run_dir"
pid_file="$run_dir/server.pid"
log_file="$run_dir/server.log"

# Avoid starting duplicate servers when Codespaces resumes an existing one.
if [ -f "$pid_file" ]; then
  old_pid="$(cat "$pid_file")"
  if kill -0 "$old_pid" 2>/dev/null && curl --silent --fail http://127.0.0.1:9120/api/auth/session >/dev/null; then
    exit 0
  fi
  rm -f "$pid_file"
fi

nohup bash "$repo_root/scripts/start-codespace.sh" >"$log_file" 2>&1 < /dev/null &
echo "$!" > "$pid_file"

for _ in $(seq 1 60); do
  if curl --silent --fail http://127.0.0.1:9120/api/auth/session >/dev/null; then
    exit 0
  fi
  if ! kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    cat "$log_file" >&2
    exit 1
  fi
  sleep 1
done
printf 'Hermes Hub did not become ready on port 9120. Log: %s\n' "$log_file" >&2
exit 1
