#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
task_python=""
if [[ $# -gt 0 ]]; then
  if [[ "$1" == "--python" && $# -eq 2 ]]; then
    task_python="$2"
  else
    echo "Usage: ./setup.sh [--python /path/to/python3.12]" >&2
    exit 2
  fi
fi
if [[ ! -x "$task_root/.venv/bin/python" ]]; then
  if [[ -z "$task_python" ]]; then
    task_python="$(command -v python3.12 || command -v python3 || true)"
  fi
  if [[ -z "$task_python" ]]; then
    echo 'Install Python 3.12 from python.org, then run ./setup.sh again.' >&2
    exit 1
  fi
  "$task_python" -c 'import sys; assert sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32, "Use 64-bit Python 3.12"'
  "$task_python" -m venv "$task_root/.venv"
fi
task_python="$task_root/.venv/bin/python"
"$task_python" -c 'import sys; assert sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32, "Recreate .venv with 64-bit Python 3.12"'
"$task_python" -m pip install -r "$task_root/requirements.txt"
"$task_python" -X utf8 -c 'from brainflow.board_shim import BoardShim, BoardIds; import fastapi, uvicorn, scipy, bleak, pyedflib; print(BoardShim.get_board_descr(BoardIds.MUSE_2_BOARD))'
echo 'Environment ready. Run ./start.sh, or double-click 启动系统.command.'
