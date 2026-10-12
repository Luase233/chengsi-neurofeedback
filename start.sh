#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")" && pwd)"
task_python="$task_root/.venv/bin/python"
task_helper="$task_root/launcher_state.py"
task_original_args=("$@")
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
if [[ -n "$task_data_dir" ]]; then
  task_data_dir="$("$task_python" -c 'from pathlib import Path; import sys; print((Path(sys.argv[1])/sys.argv[2]).resolve())' "$task_root" "$task_data_dir")"
fi
# The lock owner keeps its file descriptor while this child launcher runs.
if [[ "${CHENGSI_LAUNCH_LOCK_ROOT:-}" != "$task_root" ]]; then
  exec "$task_python" "$task_helper" locked --root "$task_root" -- start.sh "${task_original_args[@]}"
fi
if $task_restart; then /bin/bash "$task_root/stop.sh"; fi
task_pid_file="$task_root/preview-server.pid"
task_state_file="$task_root/runtime/preview-server.json"
task_record="$("$task_python" "$task_helper" inspect --root "$task_root")"
task_ready=false
if [[ "$task_record" != null ]]; then
  task_fields="$("$task_python" -c 'import json,sys; r=json.loads(sys.argv[1]); print(r["pid"],r["port"],str(r["lan_enabled"]).lower())' "$task_record")"
  read -r task_process task_actual_port task_actual_lan <<< "$task_fields"
  if [[ "$task_actual_lan" != "$task_lan" ]]; then
    echo 'This project is running in a different LAN/local-only mode. Use --restart to change it.' >&2; exit 1
  fi
  if [[ -n "$task_data_dir" ]]; then
    "$task_python" - "$task_record" "$task_data_dir" <<'PY'
import json, pathlib, sys
if str(pathlib.Path(sys.argv[2]).resolve()) != json.loads(sys.argv[1])["data_dir"]:
    sys.exit('This project is running with a different data directory. Use --restart to change it.')
PY
  fi
  for ((task_attempt=0; task_attempt<60; task_attempt++)); do
    if "$task_python" "$task_helper" healthy --root "$task_root"; then task_ready=true; break; fi
    if ! kill -0 "$task_process" 2>/dev/null; then break; fi
    sleep 0.25
  done
  if ! $task_ready; then echo 'The managed server is not ready. Check backend-server-error.log or use --restart.' >&2; exit 1; fi
  "$task_python" "$task_helper" record --root "$task_root" --pid "$task_process"
  echo "Already running: http://127.0.0.1:$task_actual_port/operator.html"
else
  task_preferred="$task_port"
  for ((task_bind_attempt=0; task_bind_attempt<10; task_bind_attempt++)); do
    task_port_args=(port --port "$task_preferred")
    if $task_lan; then task_port_args+=(--lan); fi
    task_actual_port="$("$task_python" "$task_helper" "${task_port_args[@]}")"
    if [[ "$task_actual_port" != "$task_port" ]]; then
      echo "Port $task_port is occupied; using available port $task_actual_port."
    fi
    task_args=(-X utf8 -u "$task_root/server.py" --port "$task_actual_port")
    if $task_lan; then task_args+=(--lan); fi
    if [[ -n "$task_data_dir" ]]; then task_args+=(--data-dir "$task_data_dir"); fi
    (cd "$task_root" && exec nohup "$task_python" "${task_args[@]}") >"$task_root/backend-server.log" 2>"$task_root/backend-server-error.log" < /dev/null &
    task_process=$!
    echo "$task_process" > "$task_pid_file"
    rm -f "$task_state_file"
    for ((task_attempt=0; task_attempt<60; task_attempt++)); do
      sleep 0.25
      if "$task_python" "$task_helper" healthy --root "$task_root"; then task_ready=true; break; fi
      if ! kill -0 "$task_process" 2>/dev/null; then break; fi
    done
    if $task_ready; then
      "$task_python" "$task_helper" record --root "$task_root" --pid "$task_process"
      break
    fi
    # A bind probe cannot reserve a port for Uvicorn. Retry if another owner wins.
    if ! kill -0 "$task_process" 2>/dev/null && grep -Eiq 'address already in use|10048|Errno 98|Errno 48|error while attempting to bind' "$task_root/backend-server-error.log"; then
      rm -f "$task_pid_file"
      if (( task_actual_port < 65535 )); then task_preferred=$((task_actual_port+1)); else task_preferred=49152; fi
    else
      echo "Backend did not start. See $task_root/backend-server-error.log" >&2; exit 1
    fi
  done
  if ! $task_ready; then echo 'Repeated port contention prevented startup. Please retry.' >&2; exit 1; fi
fi
task_url="http://127.0.0.1:$task_actual_port"
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
