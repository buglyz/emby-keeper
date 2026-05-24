#!/bin/bash

set -e

if [ -d "/src" ]; then
    if [ ! "$(ls -A /src)" ]; then
        cp -rT /build /src
    fi
    echo ">> 正在根据源码配置程序, 请稍候."
    pip install --no-cache-dir -e /src
    echo ">> 已配置完成."
    echo
else
    echo ">> 请挂载目录 /src, 以释放源码."
    exit 1
fi

if [ -z "${EK_MODE}" ] || [ "${EK_MODE}" = "api" ]; then
    exec "embykeeperapi" "--basedir" "/app" "$@"
else
    exec "embykeeper" "--basedir" "/app" "$@"
fi
