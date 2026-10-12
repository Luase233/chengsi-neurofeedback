#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")" && pwd)"
task_python="$task_root/.venv/bin/python"
task_helper="$task_root/launcher_state.py"
task_original_args=("$@")
task_requested_port=""
if [[ $# -gt 0 ]]; then
  if [[ "$1" == --port && $# -eq 2 ]]; then task_requested_port="$2"
  else echo 'Usage: ./stop.sh [--port ACTUAL_PORT]' >&2; exit 2; fi
fi
if [[ -n "$task_requested_port" ]]; then
  if [[ ! "$task_requested_port" =~ ^[0-9]{1,5}$ ]] || (( 10#$task_requested_port < 1 || 10#$task_requested_port > 65535 )); then
    echo 'Port must be between 1 and 65535.' >&2; exit 2
  fi
  task_requested_port=$((10#$task_requested_port))
fi
task_pid_file="$task_root/preview-server.pid"
task_state_file="$task_root/runtime/preview-server.json"
if [[ "${CHENGSI_LAUNCH_LOCK_ROOT:-}" != "$task_root" ]]; then
  exec "$task_python" "$task_helper" locked --root "$task_root" -- stop.sh "${task_original_args[@]}"
fi
if [[ ! -f "$task_pid_file" ]]; then echo 'No managed server.'; exit 0; fi
task_record="$("$task_python" "$task_helper" inspect --root "$task_root")"
if [[ "$task_record" == null ]]; then
  rm -f "$task_pid_file" "$task_state_file"
  echo 'Managed server is already stopped.'; exit 0
fi
task_fields="$("$task_python" -c 'import json,sys; r=json.loads(sys.argv[1]); print(r["pid"],r["port"])' "$task_record")"
read -r task_process task_port <<< "$task_fields"
if [[ -n "$task_requested_port" && "$task_requested_port" != "$task_port" ]]; then
  echo "The requested port does not match this project's managed server. No process was stopped." >&2; exit 1
fi
if "$task_python" "$task_helper" owned --root "$task_root"; then
  "$task_python" - "$task_port" <<'PY'
import json, sys, urllib.request, uuid
base = 'http://127.0.0.1:' + sys.argv[1]
try:
    with urllib.request.urlopen(base + '/api/state', timeout=3) as response:
        state = json.load(response)
except (OSError, ValueError):
    state = {}
if state.get('session_id') and state.get('phase') not in ('completed', 'idle', 'error'):
    body = json.dumps({'command': 'finish', 'command_id': str(uuid.uuid4())}).encode()
    request = urllib.request.Request(base + '/api/sessions/' + state['session_id'] + '/commands',
                                     data=body, headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()
PY
fi
# Recheck creation time/identity immediately before signalling, never use kill -9.
"$task_python" "$task_helper" inspect --root "$task_root" > /dev/null
kill -TERM "$task_process"
for ((task_attempt=0; task_attempt<50; task_attempt++)); do
  if ! kill -0 "$task_process" 2>/dev/null; then
    rm -f "$task_pid_file" "$task_state_file"
    echo "Managed server stopped (port $task_port)."; exit 0
  fi
  sleep 0.1
done
echo 'Shutdown is still in progress. The managed state was kept; retry shortly.' >&2
exit 1
