#!/bin/bash

set -e

if [ -d "/src" ]; then
    if [ ! "$(ls -A /src)" ]; then
        cp -rT /build /src
    fi
    echo ">> 正在根据源码配置程序, 请稍候."
    EK_EXTRAS="${EK_EXTRAS:-full}"
    if [ -z "$EK_EXTRAS" ] || [ "$EK_EXTRAS" = "none" ]; then
        pip install --no-cache-dir -e /src
    else
        pip install --no-cache-dir -e "/src[${EK_EXTRAS}]"
    fi
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
