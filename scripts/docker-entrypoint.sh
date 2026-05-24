#!/bin/bash

set -e

MODE="${EK_MODE:-cli}"

if [ "$MODE" = "api" ]; then
    exec "embykeeperapi" "--basedir" "/app" "$@"
elif [ "$MODE" = "web" ]; then
    if [ -z "${EK_WEBPASS}" ]; then
        exec "embykeeper" "--basedir" "/app" "$@"
    else
        exec "embykeeperweb" "--basedir" "/app" "--public" "$@"
    fi
else
    exec "embykeeper" "--basedir" "/app" "$@"
fi