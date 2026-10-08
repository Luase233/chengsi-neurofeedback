#!/bin/bash
cd "$(dirname "$0")" || exit 1
./setup.sh "$@"
task_result=$?
echo 'Press Return to close.'
read -r
exit "$task_result"
