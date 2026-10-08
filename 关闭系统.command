#!/bin/bash
cd "$(dirname "$0")" || exit 1
./stop.sh "$@"
task_result=$?
if [[ "$task_result" -ne 0 ]]; then echo 'Press Return to close.'; read -r; fi
exit "$task_result"
