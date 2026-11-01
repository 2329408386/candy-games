#!/bin/bash
# Restarts the app (or nginx) when it is running but no longer answering, which systemd alone cannot detect.
probe(){ curl -fsS -m 5 -o /dev/null "$1"; }
healthy(){ probe "$1/health" && probe "$1/math/health" && probe "$1/english/health"; }
retry(){ healthy "$1" || { sleep 3; healthy "$1"; }; }

if ! retry http://127.0.0.1:18082; then
    echo "candy-games not answering /health, restarting"
    systemctl restart candy-games
elif ! retry http://127.0.0.1; then
    echo "app healthy but nginx not answering, restarting nginx"
    systemctl restart nginx
fi
