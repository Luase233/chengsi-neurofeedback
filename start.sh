#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
task_python="$task_root/.venv/bin/python"
task_port=8768
task_lan=true
task_restart=false
task_browser=true
task_data_dir=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port|--data-dir)
      if [[ $# -lt 2 ]]; then echo "Missing value for $1" >&2; exit 2; fi
      if [[ "$1" == --port ]]; then task_port="$2"; else task_data_dir="$2"; fi
      shift 2 ;;
    --local-only) task_lan=false; shift ;;
    --restart) task_restart=true; shift ;;
    --no-browser) task_browser=false; shift ;;
    --help|-h)
      echo 'Usage: ./start.sh [--local-only] [--port 8768] [--restart] [--no-browser] [--data-dir /path]'
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
if [[ ! "$task_port" =~ ^[0-9]{1,5}$ ]] || (( 10#$task_port < 1 || 10#$task_port > 65535 )); then
  echo 'Port must be between 1 and 65535.' >&2; exit 2
fi
task_port=$((10#$task_port))
if [[ ! -x "$task_python" ]]; then echo 'Run ./setup.sh first.' >&2; exit 1; fi
if $task_restart; then "$task_root/stop.sh" --port "$task_port"; fi
task_url="http://127.0.0.1:$task_port"
task_pid_file="$task_root/preview-server.pid"

# The health check uses only the standard library and never sends control commands.
task_health() {
  "$task_python" - "$task_url" "$task_lan" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1] + '/api/health', timeout=1) as response:
        health = json.load(response)
except Exception:
    sys.exit(1)
if health.get('service') != 'chengsi-backend' or str(health.get('api_version')) != '1':
    sys.exit(1)
if bool(health.get('lan_enabled', False)) != (sys.argv[2] == 'true'):
    sys.exit(2)
PY
}

task_status=0
task_health || task_status=$?
if [[ "$task_status" == 2 ]]; then
  echo 'A server is already running in a different LAN/local-only mode. Stop it, then restart with the desired mode (or use --restart for this managed server).' >&2
  exit 1
elif [[ "$task_status" == 0 ]]; then
  echo "Already running: $task_url/operator.html"
else
  if [[ -f "$task_pid_file" ]]; then
    task_old_pid="$(cat "$task_pid_file")"
    if [[ "$task_old_pid" =~ ^[0-9]+$ ]] && kill -0 "$task_old_pid" 2>/dev/null; then
      echo 'A managed PID is still running. Stop the existing server using its original --port before starting another.' >&2
      exit 1
    fi
  fi
  # Probe the requested bind address before starting. Never terminate its owner.
  "$task_python" - "$task_port" "$task_lan" <<'PY'
import socket, sys
with socket.socket() as probe:
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(('0.0.0.0' if sys.argv[2] == 'true' else '127.0.0.1', int(sys.argv[1])))
    except OSError as exc:
        sys.exit(f'Port {sys.argv[1]} is unavailable: {exc}. Choose another --port.')
PY
  task_args=(-X utf8 -u "$task_root/server.py" --port "$task_port")
  if $task_lan; then task_args+=(--lan); fi
  if [[ -n "$task_data_dir" ]]; then task_args+=(--data-dir "$task_data_dir"); fi
  (cd "$task_root" && exec nohup "$task_python" "${task_args[@]}") >"$task_root/backend-server.log" 2>"$task_root/backend-server-error.log" < /dev/null &
  task_process=$!
  echo "$task_process" > "$task_pid_file"
  task_ready=false
  for ((task_attempt=0; task_attempt<60; task_attempt++)); do
    sleep 0.25
    if task_health; then task_ready=true; break; fi
    if ! kill -0 "$task_process" 2>/dev/null; then break; fi
  done
  if ! $task_ready; then
    echo "Backend did not start. See $task_root/backend-server-error.log" >&2
    exit 1
  fi
fi

echo "Operator: $task_url/operator.html"
"$task_python" - "$task_url" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1] + '/api/connection', timeout=3) as response:
        connection = json.load(response)
    if connection.get('lan_enabled'):
        print('iPad: join the same LAN, then scan the QR code in the operator connection panel or open:')
        for url in connection.get('participant_urls', []):
            print('  ' + url)
        if not connection.get('participant_urls'):
            print('  No LAN address detected. Connect this computer to Wi-Fi/Ethernet, then refresh the operator panel.')
    else:
        print('Local-only mode: ' + connection.get('local_participant_url', sys.argv[1] + '/participant.html'))
except Exception as exc:
    print('Connection details are unavailable; check the operator panel:', exc, file=sys.stderr)
PY
if $task_browser && command -v open >/dev/null 2>&1; then open "$task_url/operator.html"; fi
