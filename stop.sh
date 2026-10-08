#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
task_port=8768
if [[ $# -gt 0 ]]; then
  if [[ "$1" == --port && $# -eq 2 ]]; then task_port="$2"
  else echo 'Usage: ./stop.sh [--port 8768]' >&2; exit 2; fi
fi
if [[ ! "$task_port" =~ ^[0-9]{1,5}$ ]] || (( 10#$task_port < 1 || 10#$task_port > 65535 )); then
  echo 'Port must be between 1 and 65535.' >&2; exit 2
fi
task_port=$((10#$task_port))
task_pid_file="$task_root/preview-server.pid"
if [[ ! -f "$task_pid_file" ]]; then echo 'No managed server.'; exit 0; fi
task_process="$(cat "$task_pid_file")"
if [[ ! "$task_process" =~ ^[0-9]+$ ]]; then echo 'Invalid saved PID. No process was stopped.' >&2; exit 1; fi
if ! kill -0 "$task_process" 2>/dev/null; then
  rm "$task_pid_file"
  echo 'Managed server is already stopped.'
  exit 0
fi
task_command="$(ps -p "$task_process" -o command=)"
task_port_pattern="--port[[:space:]]+$task_port([[:space:]]|$)"
if [[ "$task_command" != *"$task_root/server.py"* ]] || [[ ! "$task_command" =~ $task_port_pattern ]]; then
  echo 'The saved PID or requested port does not match this project. No process was stopped. Check --port.' >&2
  exit 1
fi
"$task_root/.venv/bin/python" - "$task_port" <<'PY'
import json, sys, urllib.error, urllib.request, uuid
base = 'http://127.0.0.1:' + sys.argv[1]
try:
    with urllib.request.urlopen(base + '/api/state', timeout=3) as response:
        state = json.load(response)
except (OSError, ValueError):
    state = {}
if state.get('session_id') and state.get('phase') not in ('completed', 'idle'):
    body = json.dumps({'command': 'finish', 'command_id': str(uuid.uuid4())}).encode()
    request = urllib.request.Request(base + '/api/sessions/' + state['session_id'] + '/commands',
                                     data=body, headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()
PY
# SIGTERM lets Uvicorn run its normal shutdown; do not escalate to kill -9.
kill -TERM "$task_process"
for ((task_attempt=0; task_attempt<50; task_attempt++)); do
  if ! kill -0 "$task_process" 2>/dev/null; then
    rm "$task_pid_file"
    echo 'Managed server stopped.'
    exit 0
  fi
  sleep 0.1
done
echo 'Shutdown is still in progress. The PID file was kept; retry shortly.' >&2
exit 1
