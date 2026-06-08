#!/bin/bash

set -e

MODE="${EK_MODE:-api}"

if [ "$MODE" = "api" ]; then
    exec "embykeeperapi" "--basedir" "/app" "$@"
elif [ "$MODE" = "cli" ]; then
    exec "embykeeper" "--basedir" "/app" "$@"
else
    exec "embykeeperapi" "--basedir" "/app" "$@"
fi
